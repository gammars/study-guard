import asyncio
import hashlib
import os
from pathlib import Path


class TTSService:
    def __init__(self, root):
        self.root = Path(root)
        self.cache_dir = self.root / "logs" / "tts"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.voice = os.getenv("STUDYGUARD_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
        self.rate = os.getenv("STUDYGUARD_TTS_RATE", "-8%")
        self.pitch = os.getenv("STUDYGUARD_TTS_PITCH", "+0Hz")

    def synthesize(self, text):
        text = " ".join(str(text or "").split())
        if not text:
            return {"success": False, "error": "empty text"}
        text = text[:300]
        path = self._cache_path(text)
        if not path.exists():
            asyncio.run(self._synthesize_edge(text, path))
        return {
            "success": True,
            "path": str(path),
            "voice": self.voice,
            "rate": self.rate,
            "pitch": self.pitch,
        }

    async def _synthesize_edge(self, text, path):
        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError("edge-tts 未安装，请先安装 edge-tts。") from exc

        communicate = edge_tts.Communicate(text, voice=self.voice, rate=self.rate, pitch=self.pitch)
        await communicate.save(str(path))

    def _cache_path(self, text):
        key = hashlib.sha1(f"{self.voice}|{self.rate}|{self.pitch}|{text}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.mp3"
