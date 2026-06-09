import os
import subprocess
import threading
import time
from pathlib import Path


class PiButtonAudioService:
    def __init__(self, root, store, asr_service, agent):
        self.root = Path(root)
        self.store = store
        self.asr_service = asr_service
        self.agent = agent
        self.record_dir = self.root / "logs" / "voice"
        self.record_dir.mkdir(parents=True, exist_ok=True)
        default_device = os.getenv("STUDYGUARD_ALSA_DEVICE", "plughw:3,0")
        self.input_device = os.getenv("STUDYGUARD_AUDIO_INPUT_DEVICE", default_device)
        self.sample_rate = os.getenv("STUDYGUARD_AUDIO_SAMPLE_RATE", "16000")
        self._process = None
        self._path = None
        self._lock = threading.Lock()

    def start_recording(self):
        with self._lock:
            if self._process and self._process.poll() is None:
                return {"success": True, "already_recording": True, "path": str(self._path)}
            self._path = self.record_dir / f"button_voice_{time.strftime('%Y%m%d_%H%M%S')}.wav"
            command = [
                "arecord",
                "-D",
                self.input_device,
                "-f",
                "S16_LE",
                "-r",
                str(self.sample_rate),
                "-c",
                "1",
                str(self._path),
            ]
            try:
                self._process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                self._process = None
                self.store.add_log("ERROR", f"实体按键录音启动失败：{exc}")
                return {"success": False, "error": str(exc), "device": self.input_device}

        self.store.add_log("BUTTON", f"按键长按：树莓派本地录音开始，输入设备 {self.input_device}")
        return {"success": True, "event": "pi_recording_start", "path": str(self._path), "device": self.input_device}

    def stop_recording_and_send(self):
        with self._lock:
            process = self._process
            path = self._path
            self._process = None
            self._path = None

        if not process:
            return {"success": False, "error": "no active recording", "event": "pi_recording_missing"}

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

        thread = threading.Thread(target=self._transcribe_and_send, args=(path,), daemon=True)
        thread.start()
        self.store.add_log("BUTTON", f"按键松开：树莓派本地录音结束，准备识别 {path}")
        message = "正在识别语音..."
        self.store.add_message("student", message, source="pi_audio")
        self.store.add_audio_event(message, source="pi_audio")
        return {"success": True, "event": "pi_recording_stop", "path": str(path)}

    def _transcribe_and_send(self, path):
        try:
            if not path or not Path(path).exists() or Path(path).stat().st_size < 1024:
                self.store.add_log("ERROR", f"实体按键录音文件无效：{path}")
                return
            audio_bytes = Path(path).read_bytes()
            result = self.asr_service.transcribe(audio_bytes, "audio/wav")
            if not result.get("success"):
                raise RuntimeError(result.get("error", "ASR failed"))
            text = str(result.get("text") or "").strip()
            if not text:
                self.store.add_log("BUTTON", "实体按键语音识别为空")
                return

            self.store.add_log("BUTTON", f"实体按键语音识别：{text}")
            self.store.add_voice_event("send_chat", source="pi_button", text=text)
        except Exception as exc:
            self.store.add_log("ERROR", f"实体按键语音识别/发送失败：{exc}")
