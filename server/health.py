import os
import time

import httpx

from .state import now_text


def run_health_check(store, hardware, agent, button_result=None, mode="full"):
    mode = mode if mode in {"quick", "full"} else "full"
    items = [
        check_camera(hardware),
        check_yolo(hardware, mode=mode),
        check_distance(hardware),
        check_dht11(hardware),
        check_rgb_led(store, hardware, mode=mode),
        check_button(button_result),
        check_agent(agent, mode=mode),
        check_logs(store),
    ]
    statuses = [item["status"] for item in items]
    if "fail" in statuses:
        overall = "fail"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "pass"
    return {"overall": overall, "mode": mode, "time": now_text(), "items": items}


def health_item(name, label, status, message, details=None):
    return {
        "name": name,
        "label": label,
        "status": status,
        "message": message,
        "details": details or {},
    }


def check_camera(hardware):
    if not hardware._allow_real():
        return health_item(
            "camera",
            "摄像头",
            "warning",
            "当前为演示模式，未打开真实摄像头。",
            {"mode": "demo"},
        )
    start = time.time()
    try:
        success, frame = hardware._read_camera_frame()
        elapsed_ms = int((time.time() - start) * 1000)
        if not success or frame is None:
            return health_item(
                "camera",
                "摄像头",
                "fail",
                "无法打开摄像头或读取画面。",
                {"index": hardware._camera_index, "elapsed_ms": elapsed_ms},
            )
        height, width = frame.shape[:2]
        return health_item(
            "camera",
            "摄像头",
            "pass",
            f"摄像头可用，index={hardware._camera_index}，成功读取画面。",
            {"index": hardware._camera_index, "frame_size": f"{width}x{height}", "elapsed_ms": elapsed_ms},
        )
    except Exception as exc:
        return health_item("camera", "摄像头", "fail", f"摄像头检测失败：{exc}", {"index": hardware._camera_index})


def check_yolo(hardware, mode="full"):
    model_path = hardware._yolo_model_path
    if not model_path.exists():
        return health_item(
            "yolo",
            "YOLO 视觉",
            "fail",
            f"YOLOv5n 模型不存在：{model_path}",
            {"model_path": str(model_path)},
        )
    if mode != "full":
        return health_item(
            "yolo",
            "YOLO 视觉",
            "pass",
            "YOLOv5n 模型文件存在，快速自检未执行推理。",
            {"model_path": str(model_path), "confidence_threshold": hardware._yolo_confidence},
        )
    start = time.time()
    result = hardware.detect_person()
    elapsed_ms = int((time.time() - start) * 1000)
    status = "pass" if result.get("available", True) else "fail"
    if result.get("demo"):
        status = "warning"
    detected = "检测到人" if result.get("person_detected") else "未检测到人"
    message = f"模型存在，推理耗时 {elapsed_ms}ms，{detected}。"
    if not result.get("available", True):
        message = f"YOLO 推理不可用：{result.get('error', 'unknown')}"
    return health_item(
        "yolo",
        "YOLO 视觉",
        status,
        message,
        {
            "model_path": str(model_path),
            "elapsed_ms": elapsed_ms,
            "persons": result.get("persons", 0),
            "confidence": result.get("confidence"),
            "threshold": result.get("threshold", hardware._yolo_confidence),
            "demo": result.get("demo", False),
        },
    )


def check_distance(hardware):
    result = hardware.read_distance()
    status = "pass"
    if result.get("demo"):
        status = "warning"
    if result.get("available") is False:
        status = "fail"
    distance = result.get("distance_cm")
    threshold = result.get("threshold_cm", 40)
    if distance is None:
        message = f"超声波不可用：{result.get('error', 'unknown')}"
    else:
        seat = "在座" if result.get("distance_present") else "离座"
        mode_text = "演示模式，" if result.get("demo") else ""
        message = f"{mode_text}当前距离 {distance}cm，阈值 {threshold}cm，判定{seat}。"
    return health_item("distance", "超声波", status, message, result)


def check_dht11(hardware):
    result = hardware.read_environment(use_demo_fallback=False, timeout_seconds=4)
    if result.get("temperature") is not None and result.get("humidity") is not None:
        status = "warning" if result.get("demo") else "pass"
        message = f"温度 {result['temperature']}℃，湿度 {result['humidity']}%，读取 {result.get('attempts', 1)} 次。"
    else:
        status = "fail"
        message = f"DHT11 读取失败：{result.get('error', 'unknown')}"
    return health_item("dht11", "DHT11 温湿度", status, message, result)


def check_rgb_led(store, hardware, mode="full"):
    previous_color = hardware.led_color
    if mode != "full":
        return health_item(
            "rgb",
            "RGB LED",
            "pass",
            f"当前颜色 {previous_color}，快速自检未切换 LED。",
            {"current_color": previous_color},
        )
    results = []
    status = "pass"
    try:
        for color in ["red", "green", "blue"]:
            result = hardware.set_led_color(color)
            results.append(result)
            if result.get("demo"):
                status = "warning"
            time.sleep(0.08)
        restore = hardware.set_led_color(previous_color)
        results.append(restore)
        store.state.led_color = previous_color
        if restore.get("demo"):
            status = "warning"
        mode_text = "GPIO 不可用，已降级为演示状态。" if status == "warning" else "红绿蓝切换正常，已恢复原颜色。"
        return health_item(
            "rgb",
            "RGB LED",
            status,
            f"当前颜色 {previous_color}，{mode_text}",
            {"current_color": previous_color, "results": results},
        )
    except Exception as exc:
        try:
            hardware.set_led_color(previous_color)
            store.state.led_color = previous_color
        except Exception:
            pass
        return health_item("rgb", "RGB LED", "fail", f"RGB LED 自检失败：{exc}", {"current_color": previous_color})


def check_button(button_result):
    result = button_result or {}
    if result.get("success") and not result.get("demo"):
        status = "pass"
        message = f"按键初始化成功，GPIO 引脚 {result.get('button_pin', 26)}。"
    elif result.get("success"):
        status = "warning"
        message = f"按键处于演示/降级状态：{result.get('message', 'GPIO 不可用')}"
    else:
        status = "fail"
        message = f"按键初始化失败：{result.get('error', 'unknown')}"
    return health_item("button", "物理按键", status, message, result)


def check_agent(agent, mode="full"):
    configured = bool(agent.api_key and agent.model and agent.base_url)
    details = {
        "configured": configured,
        "model": agent.model,
        "base_url": agent.base_url,
        "api_key_present": bool(agent.api_key),
    }
    if not configured:
        return health_item("agent", "Agent/LLM", "fail", ".env 中 LLM_API_KEY / LLM_MODEL_ID / LLM_BASE_URL 配置不完整。", details)
    if mode != "full":
        return health_item("agent", "Agent/LLM", "warning", "LLM 配置存在，快速自检未请求模型接口。", details)
    payload = {
        "model": agent.model,
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0,
        "max_tokens": 1,
    }
    try:
        with httpx.Client(timeout=8) as client:
            response = client.post(
                f"{agent.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {agent.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        details["status_code"] = response.status_code
        if response.status_code < 400:
            return health_item("agent", "Agent/LLM", "pass", f"模型接口可用，HTTP {response.status_code}。", details)
        return health_item("agent", "Agent/LLM", "fail", f"模型接口返回 HTTP {response.status_code}：{response.text[:180]}", details)
    except Exception as exc:
        return health_item("agent", "Agent/LLM", "fail", f"模型接口请求失败：{exc}", details)


def check_logs(store):
    logs_dir = store.logs_dir
    study_log = store.study_log
    writable = logs_dir.exists() and os.access(logs_dir, os.W_OK)
    recent_count = len(store.recent_logs(limit=50))
    details = {
        "logs_dir": str(logs_dir),
        "study_log": str(study_log),
        "study_log_exists": study_log.exists(),
        "writable": writable,
        "recent_count": recent_count,
    }
    if not writable:
        return health_item("logs", "日志系统", "fail", "日志目录不可写。", details)
    return health_item("logs", "日志系统", "pass", f"日志目录可写，最近日志 {recent_count} 条。", details)
