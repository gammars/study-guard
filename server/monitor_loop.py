import time

from .state import fmt_duration


class MonitorLoop:
    def __init__(self, store, tools, interval_seconds=2):
        self.store = store
        self.tools = tools
        self.interval_seconds = interval_seconds
        self._thread = None
        import threading

        self._stop = threading.Event()
        self._threading = threading

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = self._threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                self.store.add_log("ERROR", f"后台检测异常：{exc}")
            self._stop.wait(self.interval_seconds)

    def tick(self):
        state = self.store.state
        if state.status == "break":
            if self._tick_break():
                return
            self.store.add_state_sample()
            return
        if state.status not in {"studying", "away_alert"}:
            return

        seat = self.tools.call("get_seat_status")
        old_status = state.seat_status

        if seat["status"] == "present":
            state.present_hits += 1
            state.away_hits = 0
            if state.present_hits >= 2:
                state.seat_status = "present"
        elif seat["status"] == "away":
            state.away_hits = 1
            state.present_hits = 0
            if state.status == "studying" and not state.alert_sent:
                state.away_count += 1
                state.away_since = time.time()
                self.store.add_log("AWAY", f"检测到离座，{seat['reason']}")
            state.seat_status = "away"
            if state.status == "studying":
                state.status = "away_alert"
                state.alert_sent = True
                state.alert_count += 1
                state.paused_started_at = time.time()
                self.tools.call("set_led_color", color="red")
                self.tools.call("send_student_message", message="检测到你离开座位，学习计时已暂停。回到座位后请按下按键恢复。")
                self.store.add_log("ALERT", "检测到离座，已暂停学习计时并发送学生端提醒，LED 红色")
        else:
            state.seat_status = "uncertain"

        if state.seat_status == "present" and old_status != "present":
            self.store.add_log("STATUS", f"在座，{seat['reason']}")

        self.store.add_state_sample(seat)

        if state.status == "studying" and state.current_elapsed() >= state.target_minutes * 60:
            state.status = "break"
            state.break_start = time.time()
            self.tools.call("set_led_color", color="blue")
            break_text = fmt_duration(state.break_seconds)
            self.tools.call("send_student_message", message=f"本轮学习已完成，可以休息 {break_text}。")
            self.store.add_log("POMODORO", f"完成一轮学习，进入 {break_text} 休息，LED 蓝色")
            self.store.add_state_sample(seat)

    def _tick_break(self):
        state = self.store.state
        if state.current_break_elapsed() < state.break_seconds:
            return False
        state.status = "idle"
        state.session_end = time.time()
        state.break_start = None
        self.tools.call("set_led_color", color="off")
        self.tools.call("send_student_message", message="休息结束，可以开始下一轮学习。")
        self.store.add_log("BREAK_END", f"{fmt_duration(state.break_seconds)}休息结束，LED 熄灭")
        self.store.add_state_sample()
        return True
