import time

from .state import clamp, fmt_duration, safe_int, seat_text, status_text, now_text, today_text


class StudyTools:
    def __init__(self, store, hardware):
        self.store = store
        self.hardware = hardware
        self.tts_service = None
        self.pi_audio_service = None

    def set_tts_service(self, tts_service):
        self.tts_service = tts_service

    def set_pi_audio_service(self, pi_audio_service):
        self.pi_audio_service = pi_audio_service

    def call(self, name, **kwargs):
        method = getattr(self, name)
        result = method(**kwargs)
        self.store.add_trace(name, result)
        return result

    def take_photo(self, reason="manual"):
        result = self.hardware.take_photo(reason=reason)
        if result.get("success"):
            self.store.add_log("PHOTO", f"拍照成功，原因：{reason}，路径：{result.get('image_path')}")
        else:
            self.store.add_log("ERROR", f"拍照失败：{result.get('error', 'unknown')}")
        return result

    def detect_person(self, image_path=None):
        return self.hardware.detect_person(image_path=image_path)

    def read_distance(self):
        return self.hardware.read_distance()

    def read_environment(self, use_demo_fallback=True):
        result = self.hardware.read_environment(use_demo_fallback=use_demo_fallback)
        self.store.state.last_environment = result
        if result.get("temperature") is None or result.get("humidity") is None:
            self.store.add_log("ENV", f"温湿度读取失败，已尝试 {result.get('attempts', 1)} 次：{result.get('error', 'unknown')}")
        else:
            attempts = result.get("attempts", 1)
            attempt_text = f"，采集 {attempts} 次" if attempts > 1 else ""
            self.store.add_log("ENV", f"温度 {result['temperature']}℃，湿度 {result['humidity']}%，{result['suggestion']}{attempt_text}")
        return result

    def get_seat_status(self):
        distance = self.read_distance()
        vision = self.detect_person()
        present_by_distance = distance.get("distance_present", False)
        present_by_vision = vision.get("person_detected", False)
        distance_available = distance.get("available", True)
        vision_available = vision.get("available", True)

        if not distance_available or not vision_available:
            status = "uncertain"
            confidence = "low"
        elif present_by_distance and present_by_vision:
            status = "present"
            confidence = "high"
        elif present_by_distance or present_by_vision:
            status = "uncertain"
            confidence = "medium"
        else:
            status = "away"
            confidence = "high"

        distance_text = (
            f"超声波距离 {distance.get('distance_cm')}cm"
            if distance.get("distance_cm") is not None
            else f"超声波不可用：{distance.get('error', 'unknown')}"
        )
        if vision_available:
            vision_text = (
                f"视觉检测{'有人' if present_by_vision else '无人'}"
                f"（YOLOv5n，人数 {vision.get('persons', 0)}，置信度 {vision.get('confidence', 0)}）"
            )
        else:
            vision_text = f"视觉不可用：{vision.get('error', 'unknown')}"
        reason = f"{distance_text}，{vision_text}"
        return {"status": status, "confidence": confidence, "reason": reason, "distance": distance, "vision": vision}

    def set_led_color(self, color):
        result = self.hardware.set_led_color(color)
        self.store.state.led_color = result["current_color"]
        return result

    def get_system_settings(self):
        state = self.store.state
        return {
            "success": True,
            "default_study_minutes": state.default_target_minutes,
            "default_study_seconds": state.default_target_minutes * 60,
            "default_break_seconds": state.default_break_seconds,
            "default_break_text": fmt_duration(state.default_break_seconds),
            "active_target_minutes": state.target_minutes,
            "active_target_seconds": state.target_minutes * 60,
            "active_break_seconds": state.break_seconds,
            "active_break_text": fmt_duration(state.break_seconds),
            "distance_threshold_cm": getattr(self.hardware, "distance_threshold_cm", state.distance_threshold_cm),
            "vision_model": "YOLOv5n person detector",
            "vision_confidence_threshold": getattr(self.hardware, "_yolo_confidence", None),
            "demo_mode": getattr(self.hardware, "demo_mode", None),
            "tts_enabled": True,
            "asr_enabled": True,
            "pi_audio_input_device": getattr(self.pi_audio_service, "input_device", None),
            "pi_audio_output_device": getattr(self.tts_service, "output_device", None),
            "led_color": state.led_color,
        }

    def get_current_session(self):
        state = self.store.state
        focus_total = state.target_minutes * 60
        focus_elapsed = state.current_elapsed()
        break_elapsed = state.current_break_elapsed()
        break_remaining = max(0, state.break_seconds - break_elapsed)
        return {
            "success": True,
            "status": state.status,
            "status_text": status_text(state.status),
            "seat_status": state.seat_status,
            "seat_text": seat_text(state.seat_status),
            "session_id": state.session_id,
            "led_color": state.led_color,
            "target_minutes": state.target_minutes,
            "target_seconds": focus_total,
            "elapsed_seconds": focus_elapsed,
            "elapsed_text": fmt_duration(focus_elapsed),
            "remaining_seconds": max(0, focus_total - focus_elapsed),
            "remaining_text": fmt_duration(max(0, focus_total - focus_elapsed)),
            "break_seconds": state.break_seconds,
            "break_text": fmt_duration(state.break_seconds),
            "break_elapsed_seconds": break_elapsed,
            "break_elapsed_text": fmt_duration(break_elapsed),
            "break_remaining_seconds": break_remaining,
            "break_remaining_text": fmt_duration(break_remaining),
            "away_count": state.away_count,
            "alert_count": state.alert_count,
            "paused": state.paused_started_at is not None,
        }

    def send_student_message(self, message):
        silent_tts = bool(getattr(self.store.state, "silent_student_tts", False))
        source = "ai_tool_side_effect" if silent_tts else None
        item = self.store.add_message("student", message, silent_tts=silent_tts, source=source)
        audio_event = None
        if not silent_tts:
            audio_event = self.store.add_audio_event(message, source="send_student_message")
        return {
            "success": True,
            "time": item["time"],
            "message": message,
            "silent_tts": silent_tts,
            "audio_event": audio_event,
        }

    def send_parent_message(self, message):
        item = self.store.add_message("parent", message)
        return {"success": True, "time": item["time"], "message": message}

    def set_session_defaults(self, study_minutes=None, break_seconds=None, break_minutes=None):
        result = self.store.update_session_defaults(
            study_minutes=study_minutes,
            break_seconds=break_seconds,
            break_minutes=break_minutes,
        )
        self.call(
            "send_parent_message",
            message=(
                f"默认时长已更新：学习 {result['default_target_minutes']} 分钟，"
                f"休息 {fmt_duration(result['default_break_seconds'])}。"
            ),
        )
        return result

    def start_focus_session(self, target_minutes=None):
        state = self.store.state
        state.status = "studying"
        state.seat_status = "uncertain"
        state.target_minutes = int(target_minutes or state.default_target_minutes)
        state.break_seconds = state.default_break_seconds
        state.session_id = f"session-{int(time.time())}"
        state.session_start = time.time()
        state.session_end = None
        state.break_start = None
        state.paused_started_at = None
        state.paused_total_seconds = 0
        state.away_since = None
        state.alert_sent = False
        state.away_count = 0
        state.alert_count = 0
        state.present_hits = 0
        state.away_hits = 0
        self.call("read_environment", use_demo_fallback=False)
        self.call("set_led_color", color="green")
        message = f"学习已开始，目标时长 {state.target_minutes} 分钟，请保持专注。"
        self.call("send_student_message", message=message)
        self.store.add_log("START", f"开始学习，目标 {state.target_minutes} 分钟，休息 {state.break_seconds} 秒")
        self.store.add_state_sample()
        return {
            "session_id": state.session_id,
            "start_time": now_text(),
            "target_minutes": state.target_minutes,
            "break_seconds": state.break_seconds,
        }

    def start_break(self, break_seconds=None, break_minutes=None, reason="manual"):
        state = self.store.state
        if state.status == "break":
            return {"success": True, "already_break": True, **self.get_current_session()}
        if state.status not in {"studying", "away_alert"}:
            return {
                **self.get_current_session(),
                "success": False,
                "error": f"当前状态为 {status_text(state.status)}，不能开始休息。",
            }

        if state.paused_started_at:
            state.paused_total_seconds += int(time.time() - state.paused_started_at)
            state.paused_started_at = None

        if break_minutes is not None:
            break_seconds = safe_int(break_minutes) * 60
        if break_seconds is not None:
            state.break_seconds = clamp(break_seconds, 5, 3600)

        focus_elapsed = state.current_elapsed()
        state.status = "break"
        state.seat_status = "present"
        state.session_end = time.time()
        state.break_start = time.time()
        state.away_since = None
        state.alert_sent = False
        self.call("set_led_color", color="blue")
        message = f"已进入休息，本轮已学习 {fmt_duration(focus_elapsed)}，可以休息 {fmt_duration(state.break_seconds)}。"
        self.call("send_student_message", message=message)
        self.store.add_log("BREAK_START", f"手动进入休息，原因：{reason}，已学习 {fmt_duration(focus_elapsed)}，休息 {fmt_duration(state.break_seconds)}")
        self.store.add_state_sample()
        return {
            "success": True,
            "status": state.status,
            "focus_elapsed_seconds": focus_elapsed,
            "focus_elapsed_text": fmt_duration(focus_elapsed),
            "break_seconds": state.break_seconds,
            "break_text": fmt_duration(state.break_seconds),
            "reason": reason,
        }

    def end_break(self, start_next=True):
        state = self.store.state
        if state.status != "break":
            return {
                **self.get_current_session(),
                "success": False,
                "error": f"当前状态为 {status_text(state.status)}，不在休息中。",
            }

        break_elapsed = state.current_break_elapsed()
        self.store.add_log("BREAK_END", f"手动结束休息，已休息 {fmt_duration(break_elapsed)}")
        if start_next:
            state.status = "idle"
            state.break_start = None
            result = self.call("start_focus_session")
            return {
                "success": True,
                "status": self.store.state.status,
                "break_elapsed_seconds": break_elapsed,
                "break_elapsed_text": fmt_duration(break_elapsed),
                "next_session": result,
            }

        state.status = "idle"
        state.session_end = time.time()
        state.break_start = None
        self.call("set_led_color", color="off")
        self.call("send_student_message", message="休息已结束，可以准备下一轮学习。")
        self.store.add_state_sample()
        return {
            "success": True,
            "status": state.status,
            "break_elapsed_seconds": break_elapsed,
            "break_elapsed_text": fmt_duration(break_elapsed),
        }

    def end_focus_session(self, session_id=None):
        state = self.store.state
        if state.paused_started_at:
            state.paused_total_seconds += int(time.time() - state.paused_started_at)
            state.paused_started_at = None
        elapsed = state.current_elapsed()
        state.status = "idle"
        state.session_end = time.time()
        state.break_start = None
        self.call("set_led_color", color="off")
        message = f"本次学习结束，累计 {fmt_duration(elapsed)}，离座 {state.away_count} 次，提醒 {state.alert_count} 次。"
        self.call("send_student_message", message=message)
        self.store.add_log("END", message)
        self.store.add_state_sample()
        return {
            "total_time": fmt_duration(elapsed),
            "effective_time": fmt_duration(max(0, elapsed - state.away_count * 30)),
            "away_count": state.away_count,
            "alert_count": state.alert_count,
        }

    def confirm_return(self):
        state = self.store.state
        if state.paused_started_at:
            state.paused_total_seconds += int(time.time() - state.paused_started_at)
            state.paused_started_at = None
        state.status = "studying"
        state.seat_status = "present"
        state.away_since = None
        state.alert_sent = False
        self.call("set_led_color", color="green")
        self.call("send_student_message", message="已确认回到座位，继续保持。")
        self.store.add_log("RETURN", "学生确认回座，恢复学习，LED 绿色")
        self.store.add_state_sample()
        return {"success": True, "status": state.status}

    def handle_button_press(self):
        state = self.store.state
        if state.status == "idle":
            result = self.call("start_focus_session")
            self.store.add_log("BUTTON", f"按键短按：开始默认 {state.default_target_minutes} 分钟学习")
            return {"event": "start_default_session", "result": result}
        if state.status == "away_alert":
            result = self.call("confirm_return")
            self.store.add_log("BUTTON", "按键短按：确认回座并恢复计时")
            return {"event": "confirm_return", "result": result}
        if state.status == "break":
            result = self.call("start_focus_session")
            self.store.add_log("BUTTON", "按键短按：休息中提前开始下一轮")
            return {"event": "start_next_session", "result": result}
        self.call("send_student_message", message="已收到按键操作，当前正在学习中。")
        self.store.add_log("BUTTON", "按键短按：学习中确认仍在座")
        return {"event": "acknowledge_studying"}

    def handle_button_hold_start(self):
        if not self.pi_audio_service:
            self.store.add_log("ERROR", "按键长按：树莓派本地语音服务未初始化")
            return {"success": False, "event": "pi_audio_unavailable"}
        result = self.pi_audio_service.start_recording()
        return {"event": "pi_voice_recording_start", "result": result}

    def handle_button_hold_end(self):
        if not self.pi_audio_service:
            self.store.add_log("ERROR", "按键松开：树莓派本地语音服务未初始化")
            return {"success": False, "event": "pi_audio_unavailable"}
        result = self.pi_audio_service.stop_recording_and_send()
        return {"event": "pi_voice_recording_stop", "result": result}

    def query_study_log(self, query_type="recent", date=None, limit=50):
        event_map = {
            "away": "AWAY",
            "alert": "ALERT",
            "break_end": "BREAK_END",
            "break_start": "BREAK_START",
            "button": "BUTTON",
            "config": "CONFIG",
            "end": "END",
            "env": "ENV",
            "error": "ERROR",
            "photo": "PHOTO",
            "pomodoro": "POMODORO",
            "remind": "REMIND",
            "report": "REPORT",
            "return": "RETURN",
            "start": "START",
            "status": "STATUS",
        }
        event_type = event_map.get(query_type)
        if query_type == "today":
            logs = self.store.today_logs()[-limit:]
        else:
            logs = self.store.recent_logs(limit=limit, event_type=event_type)
        return {"success": True, "query_type": query_type, "event_type": event_type, "date": date or today_text(), "result": logs}

    def generate_daily_report(self, date=None):
        state = self.store.state
        stats = self.store.today_stats()
        away_count = stats.get("away_count", state.away_count)
        alert_count = stats.get("alert_count", state.alert_count)
        elapsed = stats.get("focus_seconds", state.current_elapsed())
        env = state.last_environment or self.read_environment()
        report = (
            f"今日学习累计约 {fmt_duration(elapsed)}，检测到离座 {away_count} 次，触发提醒 {alert_count} 次。"
            f"当前环境温度 {env.get('temperature')}℃，湿度 {env.get('humidity')}%，{env.get('suggestion')}"
        )
        if away_count >= 3:
            report += " 今天离座次数偏多，建议下次学习前先安排饮水和休息。"
        else:
            report += " 整体学习状态较稳定，可以继续保持。"
        self.store.add_log("REPORT", report)
        return {
            "success": True,
            "date": date or today_text(),
            "total_time": fmt_duration(elapsed),
            "effective_time": fmt_duration(max(0, elapsed - away_count * 30)),
            "away_count": away_count,
            "alert_count": alert_count,
            "report_text": report,
        }
