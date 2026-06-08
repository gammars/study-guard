import base64
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


class MimoASRService:
    def __init__(self, root):
        load_dotenv(Path(root) / ".env")
        load_dotenv(Path.home() / ".env")
        self.api_key = (
            os.getenv("MIMO_API_KEY")
            or os.getenv("XIAOMIMIMO_API_KEY")
            or os.getenv("XIAOMI_MIMO_API_KEY")
            or os.getenv("MI_MIMO_API_KEY")
            or os.getenv("ASR_API_KEY")
            or ""
        ).strip()
        self.base_url = os.getenv("MIMO_ASR_URL", "https://api.xiaomimimo.com/v1/chat/completions").strip()
        self.model = os.getenv("MIMO_ASR_MODEL", "mimo-v2.5-asr").strip()
        self.language = os.getenv("MIMO_ASR_LANGUAGE", "zh").strip()
        self.auth_scheme = os.getenv("MIMO_AUTH_SCHEME", "auto").strip().lower()

    def transcribe(self, audio_bytes, mime_type="audio/wav"):
        if not self.api_key:
            return {"success": False, "error": "MIMO_API_KEY 未配置"}
        if not audio_bytes:
            return {"success": False, "error": "empty audio"}
        if mime_type not in {"audio/wav", "audio/mpeg", "audio/mp3"}:
            mime_type = "audio/wav"

        data_url = f"data:{mime_type};base64,{base64.b64encode(audio_bytes).decode('ascii')}"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data_url},
                        }
                    ],
                }
            ],
            "asr_options": {"language": self.language},
            "stream": False,
        }
        with httpx.Client(timeout=45) as client:
            response = None
            for scheme in self._auth_schemes():
                response = client.post(self.base_url, headers=self._headers(scheme), json=payload)
                if response.status_code != 401:
                    break
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = response.text[:800]
                raise RuntimeError(
                    f"MiMo ASR 请求失败：{response.status_code} {detail}；"
                    f"已尝试认证方式：{', '.join(self._auth_schemes())}"
                ) from exc
            data = response.json()

        text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return {
            "success": True,
            "text": text,
            "model": data.get("model", self.model),
            "usage": data.get("usage"),
        }

    def _auth_schemes(self):
        if self.auth_scheme in {"api-key", "bearer"}:
            return [self.auth_scheme]
        return ["api-key", "bearer"]

    def _headers(self, scheme):
        headers = {"Content-Type": "application/json"}
        if scheme == "bearer":
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            headers["api-key"] = self.api_key
        return headers
