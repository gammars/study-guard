import asyncio
import hashlib
import os
import subprocess
import threading
from pathlib import Path


class TTSService:
    def __init__(self, root):
        self.root = Path(root)
        self.cache_dir = self.root / "logs" / "tts"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.voice = os.getenv("STUDYGUARD_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
        self.rate = os.getenv("STUDYGUARD_TTS_RATE", "-8%")
        self.pitch = os.getenv("STUDYGUARD_TTS_PITCH", "+0Hz")
        default_device = os.getenv("STUDYGUARD_ALSA_DEVICE", "plughw:3,0")
        self.output_device = os.getenv("STUDYGUARD_AUDIO_OUTPUT_DEVICE", default_device)
        self.pi_output_enabled = os.getenv("STUDYGUARD_PI_AUDIO_OUTPUT", "1").lower() not in {"0", "false", "no"}

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

    def synthesize_wav(self, text):
        mp3_result = self.synthesize(text)
        if not mp3_result.get("success"):
            return mp3_result
        mp3_path = Path(mp3_result["path"])
        wav_path = mp3_path.with_suffix(".wav")
        if not wav_path.exists():
            completed = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(mp3_path),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    str(wav_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                return {
                    "success": False,
                    "error": (completed.stderr or completed.stdout or "ffmpeg convert failed").strip(),
                    "path": str(mp3_path),
                }
        return {**mp3_result, "path": str(wav_path), "source_path": str(mp3_path), "format": "wav"}

    def play_on_device(self, text, blocking=False):
        if not self.pi_output_enabled:
            return {"success": True, "skipped": True, "reason": "pi audio output disabled"}
        if blocking:
            return self._play_on_device(text)
        thread = threading.Thread(target=self._play_on_device, args=(text,), daemon=True)
        thread.start()
        return {"success": True, "queued": True, "device": self.output_device}

    def _play_on_device(self, text):
        result = self.synthesize_wav(text)
        if not result.get("success"):
            return result
        completed = subprocess.run(
            ["aplay", "-D", self.output_device, result["path"]],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return {
                "success": False,
                "error": (completed.stderr or completed.stdout or "aplay failed").strip(),
                "device": self.output_device,
                "path": result["path"],
            }
        return {"success": True, "device": self.output_device, "path": result["path"]}

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
