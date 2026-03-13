import cv2
import time
import json
import threading
from collections import deque

try:
    from gpiozero import OutputDevice
except Exception:
    OutputDevice = None


class MotionDetector:
    def __init__(self, config_path="config.json", debug=False):
        self.config_path = config_path
        self.debug = debug
        self.lock = threading.Lock()

        self.last_frame = None
        self.last_debug_frame = None
        self.last_mask = None
        self.last_thresh = None

        self.max_area = 0
        self.contours_count = 0
        self.motion_frames = 0
        self.event_detected = False
        self.last_event_time = 0.0

        self.running = False
        self.cap = None
        self.fgbg = None

        self.gpio_device = None
        self.gpio_busy = False

        self.log_buffer = deque(maxlen=12)

        self.load_config()
        self.init_detector()

    def add_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        with self.lock:
            self.log_buffer.append(f"[{timestamp}] {message}")

    def load_config(self):
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

    def save_config(self, new_config):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(new_config, f, indent=2, ensure_ascii=False)
        self.reload_config()

    def reload_config(self):
        with self.lock:
            self.load_config()
            self.init_detector()
        self.add_log("Конфиг перечитан")

    def init_detector(self):
        self.fgbg = cv2.createBackgroundSubtractorMOG2(
            history=700,
            varThreshold=int(self.config["var_threshold"]),
            detectShadows=False
        )
        self.init_gpio()

    def init_gpio(self):
        if self.gpio_device is not None:
            try:
                self.gpio_device.off()
                self.gpio_device.close()
            except Exception:
                pass
            self.gpio_device = None

        if not self.config.get("gpio_enabled", False):
            self.add_log("GPIO отключен в конфиге")
            return

        if OutputDevice is None:
            self.add_log("gpiozero недоступен, GPIO не инициализирован")
            return

        pin = int(self.config.get("gpio_pin", 17))
        active_high = bool(self.config.get("gpio_active_high", True))

        try:
            self.gpio_device = OutputDevice(
                pin,
                active_high=active_high,
                initial_value=False
            )
            self.add_log(f"GPIO инициализирован: BCM {pin}, active_high={active_high}")
        except Exception as e:
            self.add_log(f"Ошибка инициализации GPIO: {e}")
            self.gpio_device = None

    def trigger_gpio(self):
        if not self.config.get("gpio_enabled", False):
            return

        if self.gpio_device is None:
            return

        if self.gpio_busy:
            return

        hold_seconds = float(self.config.get("gpio_hold_seconds", 3.0))

        def worker():
            self.gpio_busy = True
            try:
                self.add_log(f"GPIO ON на {hold_seconds:.1f} сек")
                self.gpio_device.on()
                time.sleep(hold_seconds)
            except Exception as e:
                self.add_log(f"Ошибка работы GPIO: {e}")
            finally:
                try:
                    self.gpio_device.off()
                except Exception:
                    pass
                self.gpio_busy = False
                self.add_log("GPIO OFF")

        threading.Thread(target=worker, daemon=True).start()

    def open_stream(self):
        if self.cap is not None:
            self.cap.release()

        self.cap = cv2.VideoCapture(self.config["rtsp_url"], cv2.CAP_FFMPEG)
        ok = self.cap.isOpened()

        if ok:
            self.add_log("RTSP поток открыт")
        else:
            self.add_log("Не удалось открыть RTSP поток")

        return ok

    def process_loop(self):
        self.running = True

        if not self.open_stream():
            return

        while self.running:
            ret, frame = self.cap.read()

            if not ret:
                self.add_log("Ошибка чтения кадра, повторное открытие потока")
                time.sleep(1)
                self.open_stream()
                continue

            with self.lock:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

                blur_size = int(self.config["blur_kernel"])
                if blur_size % 2 == 0:
                    blur_size += 1

                blur = cv2.GaussianBlur(gray, (blur_size, blur_size), 0)

                mask = self.fgbg.apply(blur)
                _, thresh = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)

                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
                thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
                thresh = cv2.morphologyEx(thresh, cv2.MORPH_DILATE, kernel)

                contours, _ = cv2.findContours(
                    thresh,
                    cv2.RETR_EXTERNAL,
                    cv2.CHAIN_APPROX_SIMPLE
                )

                debug_frame = frame.copy() if self.debug else None
                motion = False
                max_area = 0
                contours_count = len(contours)

                for cnt in contours:
                    area = cv2.contourArea(cnt)

                    if area > max_area:
                        max_area = area

                    if area > self.config["min_area"]:
                        motion = True

                        if self.debug:
                            x, y, w, h = cv2.boundingRect(cnt)
                            cv2.rectangle(debug_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                            cv2.putText(
                                debug_frame,
                                f"area={int(area)}",
                                (x, max(20, y - 10)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.6,
                                (0, 255, 0),
                                2
                            )

                if motion:
                    self.motion_frames += 1
                else:
                    self.motion_frames = 0

                now = time.time()
                self.event_detected = False

                if self.motion_frames >= self.config["motion_frames_threshold"]:
                    if now - self.last_event_time > self.config["event_delay"]:
                        self.event_detected = True
                        self.last_event_time = now

                        self.add_log(
                            f"EVENT area={int(max_area)} contours={contours_count} "
                            f"frames={self.motion_frames}"
                        )

                        self.trigger_gpio()

                if self.debug:
                    cv2.putText(debug_frame, f"max_area={int(max_area)}", (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    cv2.putText(debug_frame, f"contours={contours_count}", (10, 55),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    cv2.putText(debug_frame, f"motion_frames={self.motion_frames}", (10, 85),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    cv2.putText(debug_frame, f"event={self.event_detected}", (10, 115),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

                    self.last_frame = frame
                    self.last_debug_frame = debug_frame
                    self.last_mask = mask
                    self.last_thresh = thresh
                else:
                    self.last_frame = None
                    self.last_debug_frame = None
                    self.last_mask = None
                    self.last_thresh = None

                self.max_area = max_area
                self.contours_count = contours_count

    def get_status(self):
        with self.lock:
            return {
                "max_area": int(self.max_area),
                "contours_count": int(self.contours_count),
                "motion_frames": int(self.motion_frames),
                "event_detected": bool(self.event_detected),
                "last_event_time": float(self.last_event_time),
                "gpio_busy": bool(self.gpio_busy),
                "gpio_initialized": self.gpio_device is not None,
                "config": self.config,
                "logs": list(self.log_buffer)
            }

    def get_jpeg_frame(self, mode="debug"):
        with self.lock:
            if mode == "raw":
                frame = self.last_frame
            elif mode == "mask":
                frame = self.last_mask
            elif mode == "thresh":
                frame = self.last_thresh
            else:
                frame = self.last_debug_frame

            if frame is None:
                return None

            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

            ret, jpeg = cv2.imencode(".jpg", frame)
            if not ret:
                return None

            return jpeg.tobytes()