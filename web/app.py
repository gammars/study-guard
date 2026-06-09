import sys
import json
import re
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, stream_with_context
from flask_cors import CORS

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.openai_compatible_agent import OpenAICompatibleMCPAgent
from server.hardware import Hardware
from server.health import run_health_check
from server.mcp_server import create_studyguard_mcp
from server.monitor_loop import MonitorLoop
from server.pi_audio import PiButtonAudioService
from server.state import StudyStore, fmt_duration, now_text, seconds_between, today_text
from server.tools import StudyTools
from server.tts import TTSService
from server.asr import MimoASRService


app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

store = StudyStore(ROOT)
store.prune_logs_to_today()
hardware = Hardware(ROOT / "photos")
hardware.apply_runtime_settings(
    distance_threshold_cm=store.state.distance_threshold_cm,
    yolo_confidence_threshold=store.state.yolo_confidence_threshold,
)
tools = StudyTools(store, hardware)
mcp_server = create_studyguard_mcp(tools)
agent = OpenAICompatibleMCPAgent(mcp_server, store, ROOT)
tts_service = TTSService(ROOT)
asr_service = MimoASRService(ROOT)
pi_audio_service = PiButtonAudioService(ROOT, store, asr_service, agent)
tools.set_tts_service(tts_service)
tools.set_pi_audio_service(pi_audio_service)
hardware.set_led_color("off")
store.state.last_environment = {
    "temperature": None,
    "humidity": None,
    "level": "pending",
    "suggestion": "开始学习时读取环境数据",
    "demo": False,
}
button_result = hardware.setup_button(
    lambda: tools.call("handle_button_press"),
    on_hold_start=lambda: tools.call("handle_button_hold_start"),
    on_hold_end=lambda: tools.call("handle_button_hold_end"),
)
store.add_log("BUTTON", f"按键初始化：{button_result}")
monitor = MonitorLoop(store, tools)
monitor.start()


@app.route("/")
def index():
    return redirect("/student")


@app.route("/student")
def student():
    return render_template("student.html")


@app.route("/parent")
def parent():
    return render_template("parent.html")


@app.route("/diagnostics")
def diagnostics():
    return render_template("diagnostics.html")


@app.route("/api/state")
def api_state():
    data = store.state.snapshot()
    data["today_stats"] = store.today_stats()
    data["abnormal_events"] = store.abnormal_events(limit=8)
    data["recent_logs"] = store.recent_logs(limit=8)
    data["trend"] = store.trend_data(
        request.args.get("trend_range", "30m"),
        request.args.get("start"),
        request.args.get("end"),
    )
    return jsonify(data)


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        settings = store.current_settings()
        settings["distance_threshold_cm"] = hardware.distance_threshold_cm
        settings["yolo_confidence_threshold"] = hardware._yolo_confidence
        return jsonify(settings)

    payload = request.get_json(force=True) or {}
    result = store.update_system_settings(
        study_minutes=payload.get("default_target_minutes"),
        break_seconds=payload.get("default_break_seconds"),
        distance_threshold_cm=payload.get("distance_threshold_cm"),
        yolo_confidence_threshold=payload.get("yolo_confidence_threshold"),
    )
    applied = hardware.apply_runtime_settings(
        distance_threshold_cm=store.state.distance_threshold_cm,
        yolo_confidence_threshold=store.state.yolo_confidence_threshold,
    )
    result["hardware"] = applied
    return jsonify(result)


@app.route("/api/health")
def api_health():
    mode = request.args.get("mode", "full")
    return jsonify(run_health_check(store, hardware, agent, button_result=button_result, mode=mode))


@app.route("/api/report/ui")
def api_ui_report():
    role = request.args.get("role", "student")
    report = build_ui_report(role)
    report["advice"] = None
    report["advice_status"] = "pending"
    return jsonify(report)


@app.route("/api/report/advice", methods=["POST"])
def api_report_advice():
    payload = request.get_json(silent=True) or {}
    role = payload.get("role") or request.args.get("role", "student")
    report = payload.get("report") if isinstance(payload.get("report"), dict) else build_ui_report(role)
    try:
        advice = agent.report_advice(report_payload_for_advice(report), role=role)
        return jsonify({"success": True, "advice": advice, "advice_status": "ready"})
    except Exception as exc:
        store.add_log("ERROR", f"日报 AI 建议生成失败：{exc}")
        return jsonify(
            {
                "success": True,
                "advice": "今日建议：保持固定学习节奏；离座或提醒偏多时，先补充饮水、整理桌面，再开始下一轮学习。",
                "advice_status": "fallback",
            }
        )


def build_ui_report(role):
    stats = store.today_stats()
    trend = store.trend_data("24h")
    distribution = trend.get("distribution", {})
    seconds = distribution.get("seconds", {})
    abnormal = store.abnormal_events(limit=20)
    env_summary = report_environment_summary()

    effective_seconds = int(seconds.get("studying") or 0) + int(seconds.get("uncertain") or 0)
    if effective_seconds <= 0:
        effective_seconds = max(0, int(stats.get("focus_seconds") or 0) - int(stats.get("away_count") or 0) * 30)
    longest_away_seconds = longest_away_duration(abnormal.get("items", []))
    focus_score = report_focus_score(
        total_seconds=int(stats.get("focus_seconds") or 0),
        effective_seconds=effective_seconds,
        away_count=int(stats.get("away_count") or 0),
        alert_count=int(stats.get("alert_count") or 0),
    )

    return {
        "success": True,
        "role": role,
        "date": today_text(),
        "generated_at": now_text(),
        "summary": {
            "total_study_seconds": int(stats.get("focus_seconds") or 0),
            "total_study_text": stats.get("focus_text") or fmt_duration(0),
            "effective_study_seconds": effective_seconds,
            "effective_study_text": fmt_duration(effective_seconds),
            "away_count": int(stats.get("away_count") or 0),
            "longest_away_seconds": longest_away_seconds,
            "longest_away_text": fmt_duration(longest_away_seconds),
            "alert_count": int(stats.get("alert_count") or 0),
            "focus_score": focus_score,
        },
        "environment": env_summary,
        "distribution": distribution,
        "abnormal_events": abnormal,
    }


def report_environment_summary():
    env_values = []
    for line in store.today_logs():
        if "[ENV]" not in line:
            continue
        match = re.search(r"温度\s*([0-9]+(?:\.[0-9]+)?)℃，湿度\s*([0-9]+(?:\.[0-9]+)?)%", line)
        if match:
            env_values.append((float(match.group(1)), float(match.group(2))))
    recent = store.state.last_environment or {}
    average_temperature = round(sum(item[0] for item in env_values) / len(env_values), 1) if env_values else None
    average_humidity = round(sum(item[1] for item in env_values) / len(env_values), 1) if env_values else None
    return {
        "sample_count": len(env_values),
        "average_temperature": average_temperature,
        "average_humidity": average_humidity,
        "average_text": f"{average_temperature}℃ / {average_humidity}%" if average_temperature is not None else "暂无环境均值",
        "recent_temperature": recent.get("temperature"),
        "recent_humidity": recent.get("humidity"),
        "recent_text": (
            f"{recent.get('temperature')}℃ / {recent.get('humidity')}%"
            if recent.get("temperature") is not None and recent.get("humidity") is not None
            else "暂无最近环境"
        ),
        "suggestion": recent.get("suggestion") or "暂无环境建议",
        "level": recent.get("level") or "pending",
    }


def longest_away_duration(items):
    durations = []
    current_time = now_text()
    for item in items or []:
        if item.get("kind") != "away":
            continue
        duration = int(item.get("duration_seconds") or 0)
        if not duration and item.get("start_time"):
            duration = seconds_between(item.get("start_time"), current_time)
        durations.append(duration)
    return max(durations, default=0)


def report_focus_score(total_seconds, effective_seconds, away_count, alert_count):
    if total_seconds <= 0:
        return 0
    base = min(100, round(effective_seconds / max(1, total_seconds) * 100))
    penalty = min(35, away_count * 4 + alert_count * 3)
    return max(0, min(100, base - penalty))


def report_payload_for_advice(report):
    return {
        "date": report["date"],
        "summary": report["summary"],
        "environment": report["environment"],
        "abnormal_events": report["abnormal_events"].get("items", [])[:6],
    }


@app.route("/api/action", methods=["POST"])
def api_action():
    payload = request.get_json(force=True) or {}
    action = payload.get("action")
    if action == "start":
        result = tools.call("start_focus_session", target_minutes=payload.get("target_minutes"))
    elif action == "end":
        result = tools.call("end_focus_session")
    elif action == "return":
        result = tools.call("confirm_return")
    elif action == "report":
        result = tools.call("generate_daily_report")
    elif action == "env":
        result = tools.call("read_environment")
    elif action == "photo":
        result = tools.call("take_photo", reason=payload.get("reason", "web"))
    elif action == "remind":
        message = payload.get("message") or "家长提醒：请保持专注，注意坐姿。"
        result = tools.call("send_student_message", message=message)
        store.add_log("REMIND", f"家长端发送提醒：{message}")
    elif action == "demo_present":
        result = hardware.set_demo_present(True)
        store.add_log("STATUS", "演示模式：模拟回座")
    elif action == "real_presence":
        result = hardware.use_real_presence()
        store.add_log("STATUS", "恢复真实超声波距离检测")
    elif action == "demo_away":
        result = hardware.set_demo_present(False)
        store.add_log("AWAY", "演示模式：模拟离座")
    else:
        return jsonify({"success": False, "message": "unknown action"}), 400
    return jsonify({"success": True, "result": result, "state": store.state.snapshot()})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    payload = request.get_json(force=True) or {}
    role = payload.get("role", "student")
    text = payload.get("text", "")
    try:
        reply = agent.reply(text, role=role)
    except Exception as exc:
        reply = f"LLM + MCP 问答链路执行失败：{exc}"
    store.add_message(role, f"问：{text}")
    store.add_message(role, f"答：{reply}")
    return jsonify({"reply": reply, "state": store.state.snapshot()})


@app.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    payload = request.get_json(force=True) or {}
    role = payload.get("role", "student")
    text = payload.get("text", "")

    @stream_with_context
    def events():
        stream = agent.stream_reply(text, role=role)
        try:
            for event in stream:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except GeneratorExit:
            raise
        except Exception as exc:
            error = {"type": "error", "error": str(exc) or exc.__class__.__name__}
            yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
        finally:
            stream.close()

    return Response(events(), mimetype="text/event-stream")


@app.route("/api/tts", methods=["POST"])
def api_tts():
    payload = request.get_json(force=True) or {}
    text = payload.get("text", "")
    try:
        result = tts_service.synthesize(text)
        if not result.get("success"):
            return jsonify(result), 400
        return send_file(result["path"], mimetype="audio/mpeg")
    except Exception as exc:
        store.add_log("ERROR", f"TTS 生成失败：{exc}")
        return jsonify({"success": False, "error": str(exc)}), 503


@app.route("/api/asr", methods=["POST"])
def api_asr():
    audio = request.files.get("audio")
    if not audio:
        return jsonify({"success": False, "error": "missing audio"}), 400
    try:
        result = asr_service.transcribe(audio.read(), audio.mimetype or "audio/wav")
        if not result.get("success"):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as exc:
        store.add_log("ERROR", f"语音识别失败：{exc}")
        return jsonify({"success": False, "error": str(exc)}), 503


@app.route("/api/video")
def api_video():
    try:
        return Response(hardware.iter_video_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")
    except Exception as exc:
        store.add_log("ERROR", f"视频流打开失败：{exc}")
        return jsonify({"success": False, "message": "camera unavailable", "error": str(exc)}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
