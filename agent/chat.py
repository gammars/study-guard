import re


class DemoAgent:
    def __init__(self, tools, store):
        self.tools = tools
        self.store = store

    def reply(self, text, role="student"):
        text = (text or "").strip()
        if not text:
            return "可以直接告诉我：开始学习、还剩多久、我回来了、今天学了多久，或者生成日报。"

        minutes = self._extract_minutes(text)
        if "现在" in text or "状态" in text or "在学习" in text:
            seat = self.tools.call("get_seat_status")
            state = self.store.state.snapshot()
            return f"当前：{state['status_text']}，座位状态：{state['seat_text']}。判断依据：{seat['reason']}。"

        if "开始" in text or "启动" in text:
            result = self.tools.call("start_focus_session", target_minutes=minutes or 25)
            return f"已开始学习，目标 {result['target_minutes']} 分钟。我会监测在座状态并记录日志。"

        if "结束" in text or "停止" in text:
            result = self.tools.call("end_focus_session")
            return f"已结束学习，本次累计 {result['total_time']}，离座 {result['away_count']} 次。"

        if "回来" in text or "回座" in text or "还在" in text:
            self.tools.call("confirm_return")
            return "已确认你回到座位，状态恢复为学习中。"

        if "还剩" in text:
            state = self.store.state.snapshot()
            return f"本轮学习还剩 {state['remaining_text']}，当前状态：{state['status_text']}。"

        if "环境" in text or "温度" in text or "湿度" in text:
            env = self.tools.call("read_environment")
            return f"当前温度 {env['temperature']}℃，湿度 {env['humidity']}%，{env['suggestion']}"

        if "拍" in text or "照片" in text or "桌面" in text:
            result = self.tools.call("take_photo", reason=text[:40])
            if result.get("success"):
                return f"已拍照保存：{result.get('image_path')}。"
            return f"拍照失败：{result.get('error', '未知错误')}。"

        if "日报" in text or "总结" in text or "报告" in text:
            result = self.tools.call("generate_daily_report")
            return result["report_text"]

        if "离座" in text or "刚才" in text:
            result = self.tools.call("query_study_log", query_type="away", limit=5)
            if result["result"]:
                return "有离座记录：" + "；".join(result["result"][-3:])
            return "目前没有查到今天的离座记录。"

        if "多久" in text or "时长" in text or "今天" in text:
            state = self.store.state.snapshot()
            return f"今天当前累计 {state['elapsed_text']}，离座 {state['away_count']} 次，提醒 {state['alert_count']} 次。"

        logs = self.tools.call("query_study_log", query_type="recent", limit=5)
        if role == "parent":
            return "我查了最近记录：" + "；".join(logs["result"]) if logs["result"] else "目前还没有学习记录。"
        return "我可以帮你开始学习、结束学习、确认回座、查询剩余时间、读取环境和生成总结。"

    def _extract_minutes(self, text):
        match = re.search(r"(\d+)\s*分钟?", text)
        if match:
            return int(match.group(1))
        match = re.search(r"(\d+)\s*min", text, re.I)
        if match:
            return int(match.group(1))
        return None
