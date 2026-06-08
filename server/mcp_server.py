from mcp.server.fastmcp import FastMCP


def create_studyguard_mcp(tools):
    mcp = FastMCP(
        "StudyGuard",
        instructions=(
            "StudyGuard MCP Server exposes Raspberry Pi study companion tools: "
            "camera, seat sensing, environment, LED, messages, sessions, logs, and reports."
        ),
    )

    @mcp.tool(description="调用摄像头拍照并保存。")
    def take_photo(reason: str = "manual") -> dict:
        return tools.call("take_photo", reason=reason)

    @mcp.tool(description="检测画面中是否有人，优先使用轻量级 YOLOv5n person detector。")
    def detect_person(image_path: str | None = None) -> dict:
        return tools.call("detect_person", image_path=image_path)

    @mcp.tool(description="读取超声波距离，距离小于等于 40cm 认为距离上可能有人，大于 40cm 判定为离座。")
    def read_distance() -> dict:
        return tools.call("read_distance")

    @mcp.tool(description="读取 DHT11 温湿度并给出环境建议。")
    def read_environment() -> dict:
        return tools.call("read_environment")

    @mcp.tool(description="融合超声波距离和视觉检测结果判断当前是否在座。")
    def get_seat_status() -> dict:
        return tools.call("get_seat_status")

    @mcp.tool(description="控制 RGB LED 状态灯，颜色为 green、blue、red 或 off。")
    def set_led_color(color: str) -> dict:
        return tools.call("set_led_color", color=color)

    @mcp.tool(description="读取系统默认设置和硬件/AI 能力配置，包括默认学习时长、默认休息时长、超声波阈值、视觉模型、TTS/ASR 状态。")
    def get_system_settings() -> dict:
        return tools.call("get_system_settings")

    @mcp.tool(description="读取当前学习会话状态，包括学习/休息/离座状态、已用时、剩余时间、休息剩余时间、离座次数和提醒次数。")
    def get_current_session() -> dict:
        return tools.call("get_current_session")

    @mcp.tool(description="向学生端网页推送文字消息。")
    def send_student_message(message: str) -> dict:
        return tools.call("send_student_message", message=message)

    @mcp.tool(description="向家长端网页推送文字消息。")
    def send_parent_message(message: str) -> dict:
        return tools.call("send_parent_message", message=message)

    @mcp.tool(description="开始一次学习任务。未指定 target_minutes 时使用当前系统默认学习时长。")
    def start_focus_session(target_minutes: int | None = None) -> dict:
        return tools.call("start_focus_session", target_minutes=target_minutes)

    @mcp.tool(description="让当前学习任务进入休息状态。可临时指定 break_seconds 或 break_minutes，不会修改系统默认休息时长。")
    def start_break(
        break_seconds: int | None = None,
        break_minutes: int | None = None,
        reason: str = "manual",
    ) -> dict:
        return tools.call("start_break", break_seconds=break_seconds, break_minutes=break_minutes, reason=reason)

    @mcp.tool(description="结束当前休息。默认直接开始下一轮学习；start_next 为 false 时只结束休息并回到未开始状态。")
    def end_break(start_next: bool = True) -> dict:
        return tools.call("end_break", start_next=start_next)

    @mcp.tool(description="修改默认学习时长和默认休息时长。study_minutes 单位为分钟，break_seconds 单位为秒，也可传 break_minutes。")
    def set_session_defaults(
        study_minutes: int | None = None,
        break_seconds: int | None = None,
        break_minutes: int | None = None,
    ) -> dict:
        return tools.call(
            "set_session_defaults",
            study_minutes=study_minutes,
            break_seconds=break_seconds,
            break_minutes=break_minutes,
        )

    @mcp.tool(description="结束当前学习任务并统计本次学习。")
    def end_focus_session(session_id: str | None = None) -> dict:
        return tools.call("end_focus_session", session_id=session_id)

    @mcp.tool(description="确认学生已经回座，恢复学习状态和绿色 LED。")
    def confirm_return() -> dict:
        return tools.call("confirm_return")

    @mcp.tool(description="查询学习日志。query_type 可为 recent、today、away、alert、env、start、end。")
    def query_study_log(query_type: str = "recent", date: str | None = None, limit: int = 8) -> dict:
        return tools.call("query_study_log", query_type=query_type, date=date, limit=limit)

    @mcp.tool(description="生成今天的学习日报。")
    def generate_daily_report(date: str | None = None) -> dict:
        return tools.call("generate_daily_report", date=date)

    return mcp
