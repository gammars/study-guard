import os
import random
import json
import subprocess
import sys
import threading
import time
from pathlib import Path


class Hardware:
    def __init__(self, photos_dir):
        self.photos_dir = Path(photos_dir)
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        self.root = self.photos_dir.parent
        self.demo_mode = os.getenv("STUDYGUARD_DEMO", "auto").lower()
        self._cap = None
        self._camera_index = self._configured_camera_index()
        self._camera_lock = threading.Lock()
        self._distance_sensor = None
        self._dht_sensor = None
        self._leds = None
        self._button = None
        self._yolo_net = None
        self._yolo_model_path = Path(os.getenv("STUDYGUARD_YOLO_MODEL", self.root / "models" / "yolov5n.onnx"))
        self._yolo_confidence = float(os.getenv("STUDYGUARD_YOLO_CONFIDENCE", "0.35"))
        self._yolo_nms = float(os.getenv("STUDYGUARD_YOLO_NMS", "0.45"))
        self._dht_retries = max(1, int(os.getenv("STUDYGUARD_DHT_RETRIES", "3")))
        self._dht_retry_delay = float(os.getenv("STUDYGUARD_DHT_RETRY_DELAY", "0.35"))
        self.led_color = "off"
        self.demo_present = True
        self._last_button_press_at = 0
        self._button_pressed_at = 0
        self._button_hold_active = False

    def _allow_real(self):
        return self.demo_mode != "1"

    def _configured_camera_index(self):
        raw = os.getenv("STUDYGUARD_CAMERA_INDEX")
        if raw is None or raw == "":
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _read_camera_frame(self):
        import cv2

        with self._camera_lock:
            if self._cap is None or not self._cap.isOpened():
                candidates = [self._camera_index] if self._camera_index is not None else [0, 1, 2, 3]
                for index in candidates:
                    cap = cv2.VideoCapture(index)
                    if cap.isOpened():
                        self._cap = cap
                        self._camera_index = index
                        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                        break
                    cap.release()
            if self._cap is None or not self._cap.isOpened():
                return False, None
            return self._cap.read()

    def take_photo(self, reason="manual"):
        if self._allow_real():
            try:
                import cv2

                success, frame = self._read_camera_frame()
                if success:
                    filename = f"photo_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                    path = self.photos_dir / filename
                    cv2.imwrite(str(path), frame)
                    return {
                        "success": True,
                        "image_path": str(path),
                        "time": now_text(),
                        "reason": reason,
                    }
            except Exception as exc:
                return {
                    "success": False,
                    "image_path": None,
                    "time": now_text(),
                    "error": str(exc),
                    "demo": True,
                }
        return {
            "success": True,
            "image_path": "demo:no-camera-photo",
            "time": now_text(),
            "reason": reason,
            "demo": True,
        }

    def read_distance(self):
        if not self._allow_real():
            return self._demo_distance()

        try:
            from gpiozero import DistanceSensor

            if self._distance_sensor is None:
                self._distance_sensor = DistanceSensor(
                    24,
                    23,
                    max_distance=1,
                    threshold_distance=0.40,
                )
            distance_cm = round(self._distance_sensor.distance * 100, 1)
            return {
                "distance_cm": distance_cm,
                "distance_present": distance_cm <= 40,
                "threshold_cm": 40,
                "demo": False,
            }
        except Exception as exc:
            return {
                "distance_cm": None,
                "distance_present": False,
                "threshold_cm": 40,
                "demo": False,
                "available": False,
                "error": str(exc),
            }

    def read_environment(self, use_demo_fallback=True, timeout_seconds=8):
        if self._allow_real():
            result = self._read_real_environment_with_retries(timeout_seconds=timeout_seconds)
            if result.get("success"):
                env = environment_result(result["temperature"], result["humidity"], demo=False)
                env["attempts"] = result.get("attempts", 1)
                return env
            if not use_demo_fallback:
                return {
                    "temperature": None,
                    "humidity": None,
                    "level": "unavailable",
                    "suggestion": "温湿度传感器读取失败。",
                    "demo": False,
                    "available": False,
                    "attempts": result.get("attempts", self._dht_retries),
                    "error": result.get("error", "unknown"),
                }
            fallback = environment_result(26.0, 48.0, demo=True)
            fallback["available"] = False
            fallback["attempts"] = result.get("attempts", self._dht_retries)
            fallback["error"] = result.get("error", "unknown")
            return fallback
        return environment_result(random.choice([24.5, 25.0, 26.0, 28.5]), random.choice([42, 48, 55]), demo=True)

    def _read_real_environment_with_retries(self, timeout_seconds=8):
        last_result = {"success": False, "error": "DHT11 read failed"}
        for attempt in range(1, self._dht_retries + 1):
            result = self._read_real_environment_once(timeout_seconds=timeout_seconds)
            result["attempts"] = attempt
            if result.get("success"):
                return result
            last_result = result
            if attempt < self._dht_retries:
                time.sleep(self._dht_retry_delay)
        return last_result

    def _read_real_environment_once(self, timeout_seconds=8):
        code = """
import json
import board
import adafruit_dht

sensor = adafruit_dht.DHT11(board.D3)
try:
    temperature = sensor.temperature
    humidity = sensor.humidity
    if temperature is None or humidity is None:
        raise RuntimeError("DHT11 returned no data")
    print(json.dumps({"success": True, "temperature": temperature, "humidity": humidity}))
finally:
    sensor.exit()
"""
        try:
            completed = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"DHT11 read timed out after {timeout_seconds}s"}

        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "DHT11 read failed").strip()
            return {"success": False, "error": error}
        try:
            return json.loads(completed.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            return {"success": False, "error": f"DHT11 invalid output: {exc}"}

    def detect_person(self, image_path=None):
        if not self._allow_real():
            return self._demo_vision(image_path=image_path)

        return self._detect_person_yolov5n(image_path=image_path)

    def _detect_person_yolov5n(self, image_path=None):
        if not self._yolo_model_path.exists():
            return {
                "person_detected": False,
                "confidence": 0.0,
                "method": "yolov5n_onnx_person",
                "image_path": image_path,
                "available": False,
                "error": f"model not found: {self._yolo_model_path}",
            }

        try:
            import cv2
            import numpy as np

            frame = None
            if image_path:
                frame = cv2.imread(str(image_path))
            if frame is None:
                success, frame = self._read_camera_frame()
                if not success or frame is None:
                    return {
                        "person_detected": False,
                        "confidence": 0.0,
                        "method": "yolov5n_onnx_person",
                        "image_path": image_path,
                        "available": False,
                        "error": "camera read failed",
                    }

            if self._yolo_net is None:
                self._yolo_net = cv2.dnn.readNetFromONNX(str(self._yolo_model_path))
                self._yolo_net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                self._yolo_net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

            height, width = frame.shape[:2]
            input_size = 640
            blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (input_size, input_size), swapRB=True, crop=False)
            self._yolo_net.setInput(blob)
            output = self._yolo_net.forward()
            predictions = output[0] if output.ndim == 3 else output

            boxes = []
            confidences = []
            x_scale = width / input_size
            y_scale = height / input_size
            for row in predictions:
                objectness = float(row[4])
                person_score = float(row[5])
                confidence = objectness * person_score
                if confidence < self._yolo_confidence:
                    continue
                cx, cy, box_w, box_h = row[:4]
                left = int((cx - box_w / 2) * x_scale)
                top = int((cy - box_h / 2) * y_scale)
                boxes.append([left, top, int(box_w * x_scale), int(box_h * y_scale)])
                confidences.append(confidence)

            indices = cv2.dnn.NMSBoxes(boxes, confidences, self._yolo_confidence, self._yolo_nms)
            if len(indices) == 0:
                person_count = 0
                best_confidence = 0.0
            else:
                flat_indices = np.array(indices).flatten()
                person_count = len(flat_indices)
                best_confidence = max(confidences[index] for index in flat_indices)

            return {
                "person_detected": person_count > 0,
                "confidence": round(float(best_confidence), 3) if person_count else 0.12,
                "method": "yolov5n_onnx_person",
                "persons": person_count,
                "model_path": str(self._yolo_model_path),
                "threshold": self._yolo_confidence,
                "image_path": image_path,
                "available": True,
            }
        except Exception as exc:
            return {
                "person_detected": False,
                "confidence": 0.0,
                "method": "yolov5n_onnx_person",
                "image_path": image_path,
                "available": False,
                "error": str(exc),
            }

    def set_led_color(self, color):
        color = color if color in {"green", "blue", "red", "off"} else "off"
        self.led_color = color
        if self._allow_real():
            try:
                from gpiozero import PWMLED

                if self._leds is None:
                    self._leds = {
                        "red": PWMLED(5),
                        "green": PWMLED(6),
                        "blue": PWMLED(11),
                    }
                for name, led in self._leds.items():
                    led.value = 1 if name == color else 0
                if color == "off":
                    for led in self._leds.values():
                        led.value = 0
                return {"success": True, "current_color": color, "demo": False, "meaning": led_meaning(color)}
            except Exception as exc:
                return {"success": True, "current_color": color, "demo": True, "error": str(exc), "meaning": led_meaning(color)}
        return {"success": True, "current_color": color, "demo": True, "meaning": led_meaning(color)}

    def set_demo_present(self, present):
        self.demo_present = bool(present)
        return {"success": True, "demo_present": self.demo_present}

    def iter_video_frames(self):
        if not self._allow_real():
            return
        import cv2

        while True:
            success, frame = self._read_camera_frame()
            if not success or frame is None:
                break
            ok, buffer = cv2.imencode(".jpg", frame)
            if not ok:
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"

    def use_real_presence(self):
        self.demo_present = True
        return {"success": True, "demo_present": self.demo_present}

    def setup_button(self, on_short_press, on_hold_start=None, on_hold_end=None):
        if not self._allow_real():
            return {"success": True, "demo": True, "message": "button disabled in demo mode"}
        try:
            from gpiozero import Button

            if self._button is None:
                self._button = Button(26, pull_up=False, bounce_time=0.25, hold_time=1.0, hold_repeat=False)

            def handle_press():
                self._button_pressed_at = time.time()
                self._button_hold_active = False

            def handle_hold():
                self._button_hold_active = True
                if on_hold_start:
                    on_hold_start()

            def handle_release():
                now = time.time()
                if now - self._last_button_press_at < 0.8:
                    return
                self._last_button_press_at = now
                if self._button_hold_active:
                    if on_hold_end:
                        on_hold_end()
                    return
                on_short_press()

            self._button.when_pressed = handle_press
            self._button.when_held = handle_hold
            self._button.when_released = handle_release
            return {"success": True, "demo": False, "button_pin": 26, "hold_time": 1.0}
        except Exception as exc:
            return {"success": False, "demo": True, "error": str(exc), "button_pin": 26}

    def _demo_distance(self):
        distance_cm = random.choice([38, 42, 47, 55]) if self.demo_present else random.choice([112, 135, 160])
        return {
            "distance_cm": distance_cm,
            "distance_present": distance_cm <= 40,
            "threshold_cm": 40,
            "demo": True,
        }

    def _demo_vision(self, image_path=None):
        return {
            "person_detected": self.demo_present,
            "confidence": 0.88 if self.demo_present else 0.18,
            "method": "demo_state",
            "image_path": image_path,
            "available": True,
        }


def environment_result(temperature, humidity, demo):
    if temperature >= 30:
        level = "hot"
        suggestion = "当前温度偏高，建议通风或适当休息。"
    elif humidity >= 75:
        level = "humid"
        suggestion = "当前湿度偏高，注意保持空气流通。"
    else:
        level = "normal"
        suggestion = "当前环境适合学习。"
    return {
        "temperature": round(float(temperature), 1),
        "humidity": round(float(humidity), 1),
        "level": level,
        "suggestion": suggestion,
        "demo": demo,
    }


def led_meaning(color):
    return {
        "green": "学习中",
        "blue": "休息中",
        "red": "提醒/异常",
        "off": "未开始",
    }.get(color, "未知")


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")
