import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


SYSTEM_PROMPT = """你是 StudyGuard 学习陪伴 Agent。
你通过 OpenAI-compatible tool_calls 调用 StudyGuard MCP 工具完成用户请求。
你必须自己理解用户意图并规划工具调用顺序，Python 代码只负责执行你返回的 tool_calls。
涉及真实设备状态、摄像头、距离、温湿度、LED、系统消息、学习开始/结束、休息开始/结束、默认时长、当前会话、日志、日报或工具测试时，必须调用相应 MCP 工具，不要凭空编造。
如果用户询问“当前有哪些 MCP 工具”，你可以基于本轮提供给你的工具 schema 直接回答工具名称和用途。
如果用户要求“测试 MCP 工具/全部工具/分别测试工具”，你应按需要依次调用相关 MCP 工具，并在最终回答中总结每个工具是否成功。
如果一个请求包含多个动作，例如“读取温度并开始学习”，你应在同一轮任务中调用多个工具完成全部动作。
当当前入口是 student端 时，每一轮用户问答都必须恰好调用一次 send_student_message，把你想对学生播报的话封装为 message；这句话应适合 TTS 朗读，简短、自然、不要包含 Markdown、JSON、工具名或调试信息。
如果 student端 问答中还需要调用其他工具，应先调用必要工具获取真实结果，再调用 send_student_message 播报给学生，最后给出聊天区文字回答。
send_student_message 调用完成后，不要因为它的返回结果再次调用 send_student_message，避免重复播报。
当当前入口是 parent端 时，不要默认调用 send_student_message，除非家长明确要求“提醒孩子/发送给学生/告诉孩子”。
只能使用 OpenAI tool_calls 调用工具；禁止在正文里输出 XML、<function_calls>、<invoke> 或伪函数调用。
回答要简洁、中文、面向当前用户角色。
"""


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "take_photo",
            "description": "调用摄像头拍照并保存。用于家长查看现场、测试摄像头或用户明确要求拍照。",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "拍照原因"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_person",
            "description": "用 YOLOv5n 检测摄像头画面中是否有人，返回人数和置信度。用于视觉检测、人是否在画面内、测试视觉模型。",
            "parameters": {
                "type": "object",
                "properties": {"image_path": {"type": "string", "description": "可选图片路径"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_distance",
            "description": "读取超声波距离，距离小于等于 40cm 认为距离上可能有人，大于 40cm 判定为离座。用于距离检测、离座判断或测试超声波。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_environment",
            "description": "读取 DHT11 温湿度和环境建议。用于询问温度、湿度、环境是否适合学习或测试环境传感器。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_seat_status",
            "description": "融合超声波距离和视觉检测结果判断当前是否在座。用于询问学生是否在座、是否离座、当前座位/学习状态。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_led_color",
            "description": "设置 RGB LED，颜色为 green、blue、red 或 off。用于切换状态灯、测试 LED 或表示学习/休息/离座状态。",
            "parameters": {
                "type": "object",
                "properties": {"color": {"type": "string", "enum": ["green", "blue", "red", "off"]}},
                "required": ["color"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_settings",
            "description": "读取系统默认设置和硬件/AI 能力配置。用于询问默认学习时长、默认休息时长、超声波阈值、视觉模型、TTS/ASR 是否启用等。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_session",
            "description": "读取当前学习会话状态。用于询问当前是否学习/休息/离座、已学多久、还剩多久、休息还剩多久、离座次数、提醒次数。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_student_message",
            "description": "向学生端网页发送文字系统消息，并触发学生端 TTS 播报。学生端每轮 AI 问答必须调用一次，用于把 AI 想对学生说的话播报出来；家长端仅在明确要求提醒学生时调用。",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_parent_message",
            "description": "向家长端网页发送文字消息。用于通知家长、同步配置变更或测试家长端消息推送。",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_focus_session",
            "description": "开始一次学习任务。未指定 target_minutes 时使用系统默认学习时长；用户说开始学习、设定学习时长时调用。",
            "parameters": {
                "type": "object",
                "properties": {"target_minutes": {"type": "integer", "description": "目标学习分钟数"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_break",
            "description": "让当前学习任务进入休息状态。用户说想休息、暂停学习去休息、提前进入休息时调用；可临时指定休息时长，不修改默认休息时长。",
            "parameters": {
                "type": "object",
                "properties": {
                    "break_seconds": {"type": "integer", "description": "本次临时休息时长，单位秒"},
                    "break_minutes": {"type": "integer", "description": "本次临时休息时长，单位分钟；如已传 break_seconds 可不传"},
                    "reason": {"type": "string", "description": "进入休息的原因"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_break",
            "description": "结束当前休息。用户说休息好了、继续学习、开始下一轮时调用；默认直接开始下一轮学习。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_next": {"type": "boolean", "description": "是否结束休息后直接开始下一轮学习，默认 true"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_session_defaults",
            "description": "修改系统默认学习时长和默认休息时长。用户说修改默认学习时间、默认休息时间、物理按键默认时长时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "study_minutes": {"type": "integer", "description": "默认学习时长，单位分钟"},
                    "break_seconds": {"type": "integer", "description": "默认休息时长，单位秒"},
                    "break_minutes": {"type": "integer", "description": "默认休息时长，单位分钟；如已传 break_seconds 可不传"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_focus_session",
            "description": "结束当前学习任务并统计本次学习。用户说结束/停止学习时调用。",
            "parameters": {
                "type": "object",
                "properties": {"session_id": {"type": "string", "description": "可选会话 ID"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_return",
            "description": "确认学生回座并恢复学习状态。用户说我回来了、已回座、继续学习时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_study_log",
            "description": "查询学习日志。用于最近日志、今日日志、离座记录、提醒记录、环境记录、开始/结束记录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["recent", "today", "away", "alert", "env", "start", "end"],
                    },
                    "date": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_daily_report",
            "description": "生成学习日报。用于日报、总结、报告或今日学习概况。",
            "parameters": {
                "type": "object",
                "properties": {"date": {"type": "string"}},
            },
        },
    },
]


class OpenAICompatibleMCPAgent:
    def __init__(self, mcp_server, store, root):
        self.mcp_server = mcp_server
        self.store = store
        load_dotenv(Path(root) / ".env")
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.model = os.getenv("LLM_MODEL_ID", "gpt-4o-mini")
        self.base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com").rstrip("/")
        self.histories = {"student": [], "parent": []}

    def reply(self, text, role="student"):
        return asyncio.run(self.areply(text, role=role))

    def stream_reply(self, text, role="student"):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        generator = self.astream_reply(text, role=role)
        try:
            while True:
                yield loop.run_until_complete(generator.__anext__())
        except StopAsyncIteration:
            pass
        finally:
            try:
                loop.run_until_complete(generator.aclose())
            except Exception:
                pass
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                try:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
            loop.close()
            asyncio.set_event_loop(None)

    async def astream_reply(self, text, role="student"):
        role = role if role in self.histories else "student"
        if not self.api_key:
            yield {"type": "error", "error": "LLM API key 未配置，请在 studyguard/.env 中设置 LLM_API_KEY。"}
            return

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self.histories[role],
            {"role": "user", "content": f"当前入口：{role}端。用户问题：{text}"},
        ]

        try:
            final_text = ""
            async for event in self._agent_events(messages):
                if event.get("type") == "final":
                    final_text = event.get("content") or ""
                else:
                    yield event
            final_text = final_text or "我没有得到可用回复。"
            for chunk in chunk_text(final_text):
                yield {"type": "delta", "delta": chunk}
            self._append_history(role, text, final_text)
            yield {"type": "done", "content": final_text}
        except Exception as exc:
            yield {"type": "error", "error": str(exc) or exc.__class__.__name__}

    async def areply(self, text, role="student"):
        role = role if role in self.histories else "student"
        if not self.api_key:
            return "LLM API key 未配置，请在 studyguard/.env 中设置 LLM_API_KEY。"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self.histories[role],
            {"role": "user", "content": f"当前入口：{role}端。用户问题：{text}"},
        ]
        final_text = await self._agent_text(messages)
        self._append_history(role, text, final_text)
        return final_text

    async def _agent_text(self, messages):
        final_text = ""
        async for event in self._agent_events(messages):
            if event.get("type") == "final":
                final_text = event.get("content") or ""
        return final_text or "我没有得到可用回复。"

    async def _agent_events(self, messages, max_rounds=8):
        for round_index in range(max_rounds):
            response = await self._chat(messages, tools=TOOL_SCHEMAS)
            choice = response["choices"][0]["message"]
            tool_calls = choice.get("tool_calls") or []
            if not tool_calls:
                yield {"type": "final", "content": choice.get("content") or ""}
                return

            messages.append(choice)
            for index, tool_call in enumerate(tool_calls):
                function = tool_call.get("function") or {}
                tool_name = function.get("name")
                arguments = self._load_arguments(function.get("arguments"))
                event_id = tool_call.get("id") or f"tool-{round_index}-{index}"
                yield {"type": "tool", "id": event_id, "tool": tool_name, "args": arguments}
                try:
                    result = await self._call_mcp_tool(
                        tool_name,
                        arguments,
                        silent_student_tts=tool_name != "send_student_message",
                    )
                    yield {"type": "tool_result", "id": event_id, "tool": tool_name, "result": {"status": "success", **result}}
                except Exception as exc:
                    result = {"status": "error", "error": str(exc) or exc.__class__.__name__}
                    yield {"type": "tool_result", "id": event_id, "tool": tool_name, "result": result}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": event_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        final = await self._chat(
            [
                *messages,
                {
                    "role": "system",
                    "content": "工具调用轮数已达到上限，请基于已有工具结果给出简洁最终回答，不要继续调用工具。",
                },
            ]
        )
        yield {"type": "final", "content": final["choices"][0]["message"].get("content") or ""}

    async def _chat(self, messages, tools=None):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code == 404 and "/openai/v1/" in str(response.request.url):
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = response.text[:800]
                raise RuntimeError(f"LLM API 请求失败：{response.status_code} {detail}") from exc
            return response.json()

    async def _call_mcp_tool(self, tool_name, arguments, silent_student_tts=False):
        if not tool_name:
            return {"success": False, "error": "missing tool name"}
        previous_silent_tts = getattr(self.store.state, "silent_student_tts", False)
        self.store.state.silent_student_tts = bool(silent_student_tts)
        try:
            result = self.mcp_server.call_tool(tool_name, arguments or {})
            if asyncio.iscoroutine(result):
                result = await result
            return normalize_mcp_result(result)
        finally:
            self.store.state.silent_student_tts = previous_silent_tts

    def _load_arguments(self, raw):
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _append_history(self, role, user_text, assistant_text):
        self.histories.setdefault(role, [])
        self.histories[role].append({"role": "user", "content": user_text})
        self.histories[role].append({"role": "assistant", "content": assistant_text})
        self.histories[role] = self.histories[role][-12:]


def normalize_mcp_result(result):
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        values = []
        for item in result:
            if hasattr(item, "text"):
                try:
                    values.append(json.loads(item.text))
                except json.JSONDecodeError:
                    values.append(item.text)
            else:
                values.append(str(item))
        if len(values) == 1:
            return values[0]
        return {"result": values}
    return {"result": str(result)}


def chunk_text(text, size=12):
    for index in range(0, len(text), size):
        yield text[index : index + size]
