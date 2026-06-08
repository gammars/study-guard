import sys
import json
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, stream_with_context
from flask_cors import CORS

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.openai_compatible_agent import OpenAICompatibleMCPAgent
from server.hardware import Hardware
from server.mcp_server import create_studyguard_mcp
from server.monitor_loop import MonitorLoop
from server.state import StudyStore
from server.tools import StudyTools
from server.tts import TTSService
from server.asr import MimoASRService


app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

store = StudyStore(ROOT)
store.prune_logs_to_today()
hardware = Hardware(ROOT / "photos")
tools = StudyTools(store, hardware)
mcp_server = create_studyguard_mcp(tools)
agent = OpenAICompatibleMCPAgent(mcp_server, store, ROOT)
tts_service = TTSService(ROOT)
asr_service = MimoASRService(ROOT)
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


@app.route("/api/state")
def api_state():
    data = store.state.snapshot()
    data["today_stats"] = store.today_stats()
    data["recent_logs"] = store.recent_logs(limit=8)
    data["trend"] = store.trend_data(
        request.args.get("trend_range", "30m"),
        request.args.get("start"),
        request.args.get("end"),
    )
    return jsonify(data)


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
