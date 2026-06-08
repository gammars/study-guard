import json
import re
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def today_text():
    return time.strftime("%Y-%m-%d")


def fmt_duration(seconds):
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return f"{hours}小时{minutes}分钟"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


@dataclass
class StudyState:
    status: str = "idle"
    seat_status: str = "uncertain"
    led_color: str = "off"
    default_target_minutes: int = 1
    default_break_seconds: int = 30
    distance_threshold_cm: float = 40.0
    yolo_confidence_threshold: float = 0.35
    target_minutes: int = 1
    break_seconds: int = 30
    session_id: str | None = None
    session_start: float | None = None
    session_end: float | None = None
    break_start: float | None = None
    paused_started_at: float | None = None
    paused_total_seconds: int = 0
    away_since: float | None = None
    alert_sent: bool = False
    away_count: int = 0
    alert_count: int = 0
    present_hits: int = 0
    away_hits: int = 0
    last_environment: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)
    tool_trace: list = field(default_factory=list)
    voice_events: list = field(default_factory=list)
    voice_event_id: int = 0
    silent_student_tts: bool = False

    def current_elapsed(self):
        if not self.session_start:
            return 0
        end = self.session_end or time.time()
        paused_seconds = self.paused_total_seconds
        if self.paused_started_at:
            paused_seconds += int(time.time() - self.paused_started_at)
        elapsed = int(end - self.session_start - paused_seconds)
        return min(max(0, elapsed), self.target_minutes * 60)

    def current_break_elapsed(self):
        if not self.break_start:
            return 0
        return min(max(0, int(time.time() - self.break_start)), self.break_seconds)

    def snapshot(self):
        remaining = max(0, self.target_minutes * 60 - self.current_elapsed())
        if self.status == "break":
            phase_elapsed = self.current_break_elapsed()
            phase_total = self.break_seconds
        else:
            phase_elapsed = self.current_elapsed()
            phase_total = self.target_minutes * 60
        return {
            "status": self.status,
            "status_text": status_text(self.status),
            "seat_status": self.seat_status,
            "seat_text": seat_text(self.seat_status),
            "led_color": self.led_color,
            "default_target_minutes": self.default_target_minutes,
            "default_break_seconds": self.default_break_seconds,
            "distance_threshold_cm": self.distance_threshold_cm,
            "yolo_confidence_threshold": self.yolo_confidence_threshold,
            "target_minutes": self.target_minutes,
            "break_seconds": self.break_seconds,
            "elapsed_seconds": self.current_elapsed(),
            "elapsed_text": fmt_duration(self.current_elapsed()),
            "remaining_seconds": remaining,
            "remaining_text": fmt_duration(remaining),
            "phase_elapsed_seconds": phase_elapsed,
            "phase_elapsed_text": fmt_duration(phase_elapsed),
            "phase_total_seconds": phase_total,
            "phase_total_text": fmt_duration(phase_total),
            "timer_display": f"{fmt_duration(phase_elapsed)}/{fmt_duration(phase_total)}",
            "away_count": self.away_count,
            "alert_count": self.alert_count,
            "messages": self.messages[-20:],
            "environment": self.last_environment,
            "tool_trace": self.tool_trace[-12:],
            "voice_events": self.voice_events[-10:],
            "session_id": self.session_id,
            "today": today_text(),
        }


class StudyStore:
    def __init__(self, root):
        self.root = Path(root)
        self.logs_dir = self.root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.study_log = self.logs_dir / "study_log.txt"
        self.trace_log = self.logs_dir / "tool_trace.jsonl"
        self.sample_log = self.logs_dir / "state_samples.jsonl"
        self.settings_file = self.logs_dir / "settings.json"
        self.state = StudyState()
        self.load_settings()

    def load_settings(self):
        if not self.settings_file.exists():
            return
        try:
            settings = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        study_minutes = safe_int(settings.get("default_target_minutes")) or self.state.default_target_minutes
        break_seconds = safe_int(settings.get("default_break_seconds")) or self.state.default_break_seconds
        distance_threshold = safe_float(settings.get("distance_threshold_cm"), self.state.distance_threshold_cm)
        yolo_confidence = safe_float(settings.get("yolo_confidence_threshold"), self.state.yolo_confidence_threshold)
        self.state.default_target_minutes = clamp(study_minutes, 1, 240)
        self.state.default_break_seconds = clamp(break_seconds, 5, 3600)
        self.state.distance_threshold_cm = clamp_float(distance_threshold, 5, 200)
        self.state.yolo_confidence_threshold = clamp_float(yolo_confidence, 0.05, 0.95)
        if not self.state.session_start:
            self.state.target_minutes = self.state.default_target_minutes
            self.state.break_seconds = self.state.default_break_seconds

    def save_settings(self):
        settings = {
            "default_target_minutes": self.state.default_target_minutes,
            "default_break_seconds": self.state.default_break_seconds,
            "distance_threshold_cm": self.state.distance_threshold_cm,
            "yolo_confidence_threshold": self.state.yolo_confidence_threshold,
            "updated_at": now_text(),
        }
        self.settings_file.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return settings

    def current_settings(self):
        return {
            "success": True,
            "default_target_minutes": self.state.default_target_minutes,
            "default_break_seconds": self.state.default_break_seconds,
            "default_break_minutes": round(self.state.default_break_seconds / 60, 2),
            "distance_threshold_cm": self.state.distance_threshold_cm,
            "yolo_confidence_threshold": self.state.yolo_confidence_threshold,
            "settings_path": str(self.settings_file),
        }

    def update_system_settings(
        self,
        study_minutes=None,
        break_seconds=None,
        break_minutes=None,
        distance_threshold_cm=None,
        yolo_confidence_threshold=None,
    ):
        state = self.state
        if study_minutes is not None:
            state.default_target_minutes = clamp(safe_int(study_minutes), 1, 240)
        if break_minutes is not None:
            break_seconds = safe_int(break_minutes) * 60
        if break_seconds is not None:
            state.default_break_seconds = clamp(safe_int(break_seconds), 5, 3600)
        if state.status == "idle":
            state.target_minutes = state.default_target_minutes
            state.break_seconds = state.default_break_seconds
        if distance_threshold_cm is not None:
            state.distance_threshold_cm = clamp_float(safe_float(distance_threshold_cm), 5, 200)
        if yolo_confidence_threshold is not None:
            state.yolo_confidence_threshold = clamp_float(safe_float(yolo_confidence_threshold), 0.05, 0.95)
        settings = self.save_settings()
        self.add_log(
            "CONFIG",
            (
                f"系统参数更新：学习 {state.default_target_minutes} 分钟，"
                f"休息 {state.default_break_seconds} 秒，"
                f"超声波阈值 {state.distance_threshold_cm}cm，"
                f"YOLO 阈值 {state.yolo_confidence_threshold}"
            ),
        )
        return {"settings": settings, **self.current_settings()}

    def update_session_defaults(self, study_minutes=None, break_seconds=None, break_minutes=None):
        state = self.state
        if study_minutes is not None:
            state.default_target_minutes = clamp(safe_int(study_minutes), 1, 240)
        if break_minutes is not None:
            break_seconds = safe_int(break_minutes) * 60
        if break_seconds is not None:
            state.default_break_seconds = clamp(safe_int(break_seconds), 5, 3600)
        if state.status == "idle":
            state.target_minutes = state.default_target_minutes
            state.break_seconds = state.default_break_seconds
        settings = self.save_settings()
        self.add_log(
            "CONFIG",
            f"默认学习时长 {state.default_target_minutes} 分钟，默认休息时长 {state.default_break_seconds} 秒",
        )
        return {
            "success": True,
            "default_target_minutes": state.default_target_minutes,
            "default_break_seconds": state.default_break_seconds,
            "settings": settings,
        }

    def prune_logs_to_today(self):
        today = today_text()
        self._prune_text_log(self.study_log, today)
        self._prune_jsonl_log(self.trace_log, today)
        self._prune_jsonl_log(self.sample_log, today)

    def add_log(self, event_type, message):
        message = " | ".join(str(message).splitlines())
        line = f"[{now_text()}] [{event_type}] {message}"
        with self.study_log.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        return line

    def add_message(self, target, message, silent_tts=False, source=None):
        item = {"time": now_text(), "target": target, "message": message}
        if silent_tts:
            item["silent_tts"] = True
        if source:
            item["source"] = source
        self.state.messages.append(item)
        if len(self.state.messages) > 100:
            self.state.messages = self.state.messages[-100:]
        return item

    def add_trace(self, tool_name, result):
        item = {"time": now_text(), "tool": tool_name, "result": result}
        self.state.tool_trace.append(item)
        if len(self.state.tool_trace) > 80:
            self.state.tool_trace = self.state.tool_trace[-80:]
        with self.trace_log.open("a", encoding="utf-8") as file:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")
        return item

    def add_voice_event(self, action, source="button"):
        self.state.voice_event_id += 1
        item = {
            "id": self.state.voice_event_id,
            "time": now_text(),
            "action": action,
            "source": source,
        }
        self.state.voice_events.append(item)
        if len(self.state.voice_events) > 30:
            self.state.voice_events = self.state.voice_events[-30:]
        return item

    def add_state_sample(self, seat=None):
        state = self.state
        seat = seat or {}
        distance = seat.get("distance") or {}
        vision = seat.get("vision") or {}
        raw_status = seat.get("status") or state.seat_status
        binary_seat = "away" if state.status == "away_alert" or raw_status == "away" else "present"
        item = {
            "time": now_text(),
            "epoch": time.time(),
            "session_id": state.session_id,
            "status": state.status,
            "seat_status": binary_seat,
            "raw_seat_status": raw_status,
            "confidence": seat.get("confidence"),
            "distance_cm": distance.get("distance_cm"),
            "distance_present": distance.get("distance_present"),
            "vision_present": vision.get("person_detected"),
            "elapsed_seconds": state.current_elapsed(),
            "break_elapsed_seconds": state.current_break_elapsed() if state.status == "break" else 0,
            "break_seconds": state.break_seconds,
            "focus_score": focus_score(state.status, binary_seat, raw_status, seat.get("confidence")),
        }
        with self.sample_log.open("a", encoding="utf-8") as file:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")
        return item

    def recent_logs(self, limit=8, event_type=None):
        if not self.study_log.exists():
            return []
        lines = self.study_log.read_text(encoding="utf-8").splitlines()
        lines = [line for line in lines if line.startswith("[")]
        if event_type:
            lines = [line for line in lines if f"[{event_type}]" in line]
        return lines[-limit:]

    def today_logs(self):
        if not self.study_log.exists():
            return []
        today = today_text()
        return [
            line
            for line in self.study_log.read_text(encoding="utf-8").splitlines()
            if line.startswith("[") and today in line
        ]

    def abnormal_events(self, limit=8):
        events = []
        active_away = None
        for line in self.today_logs():
            item = parse_log_line(line)
            if not item:
                continue
            event_type = item["type"]
            message = item["message"]
            if event_type == "AWAY":
                if active_away:
                    events.append(active_away)
                active_away = {
                    "kind": "away",
                    "severity": "high",
                    "title": "离座事件",
                    "time": item["time"],
                    "time_label": time_only(item["time"]),
                    "start_time": item["time"],
                    "start_label": time_only(item["time"]),
                    "summary": short_text(message, 92),
                    "reason": message,
                    "distance_cm": parse_distance_cm(message),
                    "alert_time": None,
                    "alert_label": None,
                    "return_time": None,
                    "return_label": None,
                    "duration_seconds": None,
                    "duration_text": "持续中",
                    "status": "未回座",
                }
            elif event_type == "ALERT":
                if active_away:
                    active_away["alert_time"] = item["time"]
                    active_away["alert_label"] = time_only(item["time"])
                    active_away["status"] = "已提醒"
                else:
                    events.append(
                        {
                            "kind": "alert",
                            "severity": "high",
                            "title": "离座提醒",
                            "time": item["time"],
                            "time_label": time_only(item["time"]),
                            "summary": short_text(message, 92),
                            "status": "已提醒",
                        }
                    )
            elif event_type == "RETURN":
                if active_away:
                    active_away["return_time"] = item["time"]
                    active_away["return_label"] = time_only(item["time"])
                    duration = seconds_between(active_away["start_time"], item["time"])
                    active_away["duration_seconds"] = duration
                    active_away["duration_text"] = fmt_duration(duration)
                    active_away["status"] = "已回座"
                    active_away["severity"] = "medium"
                    events.append(active_away)
                    active_away = None
            elif event_type == "REMIND":
                events.append(
                    {
                        "kind": "remind",
                        "severity": "medium",
                        "title": "家长提醒",
                        "time": item["time"],
                        "time_label": time_only(item["time"]),
                        "summary": short_text(message, 92),
                        "status": "已发送",
                    }
                )
            elif event_type == "ERROR":
                events.append(
                    {
                        "kind": "error",
                        "severity": "high",
                        "title": "设备/服务异常",
                        "time": item["time"],
                        "time_label": time_only(item["time"]),
                        "summary": short_text(message, 92),
                        "status": "需检查",
                    }
                )
        if active_away:
            events.append(active_away)
        events = sorted(events, key=lambda event: event.get("time", ""), reverse=True)
        return {
            "items": events[:limit],
            "total": len(events),
            "open_away": sum(1 for event in events if event.get("kind") == "away" and event.get("status") != "已回座"),
            "latest": events[0] if events else None,
        }

    def today_stats(self):
        logs = self.today_logs()
        samples = [
            item
            for item in self._read_jsonl(self.sample_log)
            if str(item.get("time", "")).startswith(today_text())
        ]

        session_elapsed = {}
        for sample in samples:
            session_id = sample.get("session_id")
            if not session_id:
                continue
            elapsed = safe_int(sample.get("elapsed_seconds"))
            session_elapsed[session_id] = max(session_elapsed.get(session_id, 0), elapsed)

        if self.state.session_id:
            session_elapsed[self.state.session_id] = max(
                session_elapsed.get(self.state.session_id, 0),
                self.state.current_elapsed(),
            )

        sampled_focus_seconds = sum(session_elapsed.values())
        ended_focus_seconds = sum(
            parse_chinese_duration(line)
            for line in logs
            if "[END]" in line and "累计" in line
        )
        focus_seconds = max(sampled_focus_seconds, ended_focus_seconds)

        away_count = sum(1 for line in logs if "[AWAY]" in line)
        system_alert_count = sum(1 for line in logs if "[ALERT]" in line)
        parent_remind_count = sum(1 for line in logs if "[REMIND]" in line)
        alert_count = system_alert_count + parent_remind_count

        return {
            "date": today_text(),
            "focus_seconds": focus_seconds,
            "focus_text": fmt_duration(focus_seconds),
            "away_count": away_count,
            "alert_count": alert_count,
            "system_alert_count": system_alert_count,
            "parent_remind_count": parent_remind_count,
            "session_count": len(session_elapsed),
        }

    def trend_data(self, range_key="30m", start_text=None, end_text=None):
        now = time.time()
        if range_key == "custom":
            start, end = custom_trend_window(start_text, end_text, now)
        else:
            duration = trend_duration_seconds(range_key)
            end = now
            start = now - duration
        samples = [
            item
            for item in self._read_jsonl(self.sample_log)
            if item.get("epoch") and start <= float(item["epoch"]) <= end
        ]
        return build_trend(samples, start, end, range_key)

    def _prune_text_log(self, path, today):
        if not path.exists():
            return
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith(f"[{today} ")
        ]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def _prune_jsonl_log(self, path, today):
        if not path.exists():
            return
        kept = []
        for item in self._read_jsonl(path):
            if str(item.get("time", "")).startswith(today):
                kept.append(json.dumps(item, ensure_ascii=False))
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")

    def _read_jsonl(self, path):
        if not path.exists():
            return []
        items = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return items


def status_text(status):
    return {
        "idle": "当前未开始学习",
        "studying": "正在学习，本轮计时中",
        "break": "当前为休息时间",
        "away_alert": "检测到离座超时",
        "error": "设备异常，请检查",
    }.get(status, status)


def seat_text(status):
    return {"present": "在座", "away": "离座", "uncertain": "不确定"}.get(status, status)


def focus_score(status, seat_status, raw_status, confidence):
    if status == "studying" and seat_status == "present":
        return 88 if raw_status == "present" and confidence == "high" else 62
    if status == "away_alert" or seat_status == "away":
        return 8
    if status == "break":
        return 28
    return 0


def safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def safe_float(value, default=0):
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, safe_int(value)))


def clamp_float(value, minimum, maximum):
    return max(float(minimum), min(float(maximum), safe_float(value, minimum)))


def parse_chinese_duration(text):
    match = re.search(r"累计\s*([^，。]+)", text)
    if not match:
        return 0
    value = match.group(1)
    hours = re.search(r"(\d+)\s*小时", value)
    minutes = re.search(r"(\d+)\s*分", value)
    seconds = re.search(r"(\d+)\s*秒", value)
    return (
        safe_int(hours.group(1) if hours else 0) * 3600
        + safe_int(minutes.group(1) if minutes else 0) * 60
        + safe_int(seconds.group(1) if seconds else 0)
    )


def parse_log_line(line):
    match = re.match(r"^\[(?P<time>[^\]]+)\]\s+\[(?P<type>[^\]]+)\]\s*(?P<message>.*)$", line or "")
    if not match:
        return None
    return match.groupdict()


def time_only(value):
    return str(value or "")[11:] or "--:--:--"


def short_text(value, limit=90):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def parse_distance_cm(text):
    match = re.search(r"距离\s*([0-9]+(?:\.[0-9]+)?)\s*cm", text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def seconds_between(start_text, end_text):
    try:
        start = datetime.strptime(start_text, "%Y-%m-%d %H:%M:%S")
        end = datetime.strptime(end_text, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return 0
    return max(0, int((end - start).total_seconds()))


def trend_duration_seconds(range_key):
    return {
        "10m": 10 * 60,
        "30m": 30 * 60,
        "1h": 60 * 60,
        "3h": 3 * 60 * 60,
        "24h": 24 * 60 * 60,
    }.get(range_key, 30 * 60)


def custom_trend_window(start_text, end_text, now_epoch):
    today = datetime.now()
    day_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = min(now_epoch, day_start.timestamp() + 24 * 60 * 60 - 1)
    fallback_end = min(now_epoch, day_end)
    fallback_start = max(day_start.timestamp(), fallback_end - 30 * 60)
    start = parse_today_datetime(start_text)
    end = parse_today_datetime(end_text)
    if start is None or end is None:
        return fallback_start, fallback_end
    start = max(day_start.timestamp(), min(start, day_end))
    end = max(day_start.timestamp(), min(end, day_end))
    if end <= start:
        return fallback_start, fallback_end
    return start, end


def parse_today_datetime(value):
    if not value:
        return None
    value = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.strftime("%Y-%m-%d") != today_text():
                return None
            return parsed.timestamp()
        except ValueError:
            continue
    return None


def build_trend(samples, start, end, range_key):
    labels = trend_labels(start, end)
    samples = sorted(samples, key=lambda item: float(item.get("epoch") or 0))
    if not samples:
        return {
            "has_data": False,
            "range": range_key,
            "timeline_segments": [
                timeline_segment("no_data", start, end, start, end),
            ],
            "labels": labels,
            "distribution": timeline_distribution([timeline_segment("no_data", start, end, start, end)]),
        }

    segments = []
    cursor = start
    max_gap = timeline_max_gap_seconds(end - start)
    for index, sample in enumerate(samples):
        epoch = max(start, min(end, float(sample.get("epoch") or start)))
        segment_start = max(epoch, cursor)

        next_epoch = end
        if index + 1 < len(samples):
            next_epoch = max(start, min(end, float(samples[index + 1].get("epoch") or end)))

        if next_epoch <= cursor:
            continue

        if segment_start > cursor:
            segments.append(timeline_segment("no_data", cursor, segment_start, start, end))

        sample_status = timeline_status(sample)
        if next_epoch - segment_start > max_gap:
            active_end = min(end, segment_start + max_gap, sample_status_end(sample, sample_status, end))
            segments.append(timeline_segment(sample_status, segment_start, active_end, start, end))
            cursor = active_end
        else:
            active_end = min(next_epoch, sample_status_end(sample, sample_status, end))
            segments.append(timeline_segment(sample_status, segment_start, active_end, start, end))
            cursor = active_end

    if cursor < end:
        segments.append(timeline_segment("no_data", cursor, end, start, end))

    segments = merge_timeline_segments(segments)
    distribution = timeline_distribution(segments)
    return {
        "has_data": True,
        "range": range_key,
        "timeline_segments": segments,
        "labels": labels,
        "distribution": distribution,
    }


def timeline_max_gap_seconds(duration):
    return max(8, min(120, duration / 120))


def timeline_status(sample):
    status = sample.get("status")
    seat_status = sample.get("seat_status")
    raw_status = sample.get("raw_seat_status")
    confidence = sample.get("confidence")
    if status == "away_alert" or seat_status == "away":
        return "away"
    if status == "break":
        return "break"
    if status == "studying" and (raw_status == "uncertain" or confidence in {"medium", "low"}):
        return "uncertain"
    if status == "studying" and seat_status == "present":
        return "studying"
    return "no_data"


def sample_status_end(sample, status, fallback_end):
    if status != "break":
        return fallback_end
    break_total = safe_int(sample.get("break_seconds"))
    break_elapsed = safe_int(sample.get("break_elapsed_seconds"))
    if break_total <= 0:
        return fallback_end
    epoch = float(sample.get("epoch") or 0)
    remaining = max(0, break_total - break_elapsed)
    return min(fallback_end, epoch + remaining)


def timeline_segment(kind, start_epoch, end_epoch, range_start, range_end):
    duration = max(1, range_end - range_start)
    left = max(0, min(100, (start_epoch - range_start) / duration * 100))
    right = max(0, min(100, (end_epoch - range_start) / duration * 100))
    return {
        "kind": kind,
        "label": timeline_label(kind),
        "start_epoch": round(start_epoch, 3),
        "end_epoch": round(end_epoch, 3),
        "start": time.strftime("%H:%M:%S", time.localtime(start_epoch)),
        "end": time.strftime("%H:%M:%S", time.localtime(end_epoch)),
        "seconds": max(0, int(round(end_epoch - start_epoch))),
        "left": round(left, 3),
        "width": round(max(0.15, right - left), 3),
    }


def timeline_label(kind):
    return {
        "studying": "在座学习",
        "uncertain": "不确定学习",
        "break": "休息",
        "away": "离座/提醒",
        "no_data": "无数据",
    }.get(kind, "无数据")


def merge_timeline_segments(segments):
    merged = []
    for segment in segments:
        if segment["width"] <= 0:
            continue
        if merged and merged[-1]["kind"] == segment["kind"]:
            previous = merged[-1]
            previous["end_epoch"] = segment["end_epoch"]
            previous["end"] = segment["end"]
            previous["seconds"] = max(0, int(round(previous["end_epoch"] - previous["start_epoch"])))
            previous["width"] = round(segment["left"] + segment["width"] - previous["left"], 3)
        else:
            merged.append(segment)
    return merged


def timeline_distribution(segments):
    keys = ("studying", "uncertain", "break", "away", "no_data")
    seconds = {key: 0 for key in keys}
    for segment in segments:
        kind = segment.get("kind")
        if kind in seconds:
            seconds[kind] += safe_int(segment.get("seconds"))
    range_seconds = safe_int(
        round(max((segment.get("end_epoch", 0) for segment in segments), default=0) - min((segment.get("start_epoch", 0) for segment in segments), default=0))
    )
    total = max(1, sum(seconds.values()))
    percentages = {key: round(seconds[key] / total * 100) for key in keys}
    return {
        "studying": percentages["studying"],
        "uncertain": percentages["uncertain"],
        "break": percentages["break"],
        "away": percentages["away"],
        "no_data": max(0, 100 - percentages["studying"] - percentages["uncertain"] - percentages["break"] - percentages["away"]),
        "seconds": seconds,
        "total_seconds": range_seconds or sum(seconds.values()),
        "total_text": fmt_duration(range_seconds or sum(seconds.values())),
    }


def focus_distribution(samples):
    if not samples:
        return {"deep": 0, "medium": 0, "low": 0}
    buckets = {"deep": 0, "medium": 0, "low": 0}
    for sample in samples:
        score = int(sample.get("focus_score") or 0)
        if score >= 75:
            buckets["deep"] += 1
        elif score >= 35:
            buckets["medium"] += 1
        else:
            buckets["low"] += 1
    total = max(1, sum(buckets.values()))
    return {key: round(value / total * 100) for key, value in buckets.items()}


def trend_labels(start, end):
    return {
        "start": time.strftime("%H:%M", time.localtime(start)),
        "middle": time.strftime("%H:%M", time.localtime((start + end) / 2)),
        "end": time.strftime("%H:%M", time.localtime(end)),
    }
