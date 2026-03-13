import cv2
import time
import threading
import os
from collections import deque
from urllib.parse import urlparse

try:
    from gpiozero import OutputDevice
except Exception:
    OutputDevice = None




def mask_rtsp_for_ui(rtsp_url: str) -> str:
    expanded = os.path.expandvars(rtsp_url or "")
    try:
        parsed = urlparse(expanded)
        if not parsed.hostname:
            return expanded
        if parsed.port:
            return f"{parsed.hostname}:{parsed.port}"
        return parsed.hostname
    except Exception:
        return expanded


class MotionDetector:
    def __init__(self, camera_id: str, config: dict, debug=False):
        self.camera_id = camera_id
        self.debug = debug
        self.lock = threading.Lock()

        self.last_frame = None
        self.last_debug_frame = None
        self.last_mask = None
        self.last_thresh = None

        self.event_detected = False
        self.last_event_time = 0.0
        self.last_motion_seen_time = 0.0
        self.motion_frames = 0

        self.running = False
        self.cap = None
        self.fgbg = None

        self.gpio_device = None
        self.gpio_busy = False
        self.gpio_state = "LOW"

        self.log_buffer = deque(maxlen=12)
        self.config = {}

        self.update_config(config, add_log=False)

    def add_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_buffer.append(f"[{timestamp}] [{self.camera_id}] {message}")

    def update_config(self, new_config: dict, add_log=True):
        with self.lock:
            self.config = dict(new_config)
            self.init_detector()
        if add_log:
            self.add_log("Конфиг обновлен")

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

        self.gpio_state = "LOW"

        if not self.config.get("gpio_enabled", False):
            self.add_log("GPIO disabled")
            return

        if OutputDevice is None:
            self.add_log("gpiozero unavailable")
            return

        pin = int(self.config.get("gpio_pin", 17))
        active_high = bool(self.config.get("gpio_active_high", True))

        try:
            self.gpio_device = OutputDevice(
                pin,
                active_high=active_high,
                initial_value=False
            )
            self.add_log(f"GPIO init: BCM {pin}")
        except Exception as e:
            self.add_log(f"GPIO init error: {e}")
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
                self.gpio_device.on()
                self.gpio_state = "HIGH"
                self.add_log(f"GPIO HIGH for {hold_seconds:.1f}s")
                time.sleep(hold_seconds)
            except Exception as e:
                self.add_log(f"GPIO runtime error: {e}")
            finally:
                try:
                    self.gpio_device.off()
                except Exception:
                    pass
                self.gpio_state = "LOW"
                self.gpio_busy = False
                self.add_log("GPIO LOW")

        threading.Thread(target=worker, daemon=True).start()

    def open_stream(self):
        if self.cap is not None:
            self.cap.release()

        rtsp_url = os.path.expandvars(self.config["rtsp_url"])
        self.cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        ok = self.cap.isOpened()

        if ok:
            self.add_log("RTSP opened")
        else:
            self.add_log("RTSP open failed")

        return ok

    def process_loop(self):
        self.running = True

        if not self.open_stream():
            return

        while self.running:
            ret, frame = self.cap.read()

            if not ret:
                self.add_log("Frame read error, reconnect")
                time.sleep(1)
                self.open_stream()
                continue

            should_fire_event = False
            event_log_message = None

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

                if self.motion_frames >= self.config["motion_frames_threshold"]:
                    self.last_motion_seen_time = now

                    if now - self.last_event_time > self.config["event_delay"]:
                        self.last_event_time = now
                        should_fire_event = True
                        event_log_message = f"EVENT area={int(max_area)}"

                hold_time = float(self.config.get("event_hold_seconds", 3))
                self.event_detected = (now - self.last_motion_seen_time) < hold_time

                if self.debug:
                    cv2.putText(debug_frame, f"event={self.event_detected}", (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    cv2.putText(debug_frame, f"gpio={self.gpio_state}", (10, 55),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

                    self.last_frame = frame
                    self.last_debug_frame = debug_frame
                    self.last_mask = mask
                    self.last_thresh = thresh
                else:
                    self.last_frame = None
                    self.last_debug_frame = None
                    self.last_mask = None
                    self.last_thresh = None

            if should_fire_event:
                self.add_log(event_log_message)
                self.trigger_gpio()

    def get_status(self):
        with self.lock:
            return {
                "camera_id": self.camera_id,
                "current_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "rtsp_url": self.config.get("rtsp_url", ""),
                "rtsp_display": mask_rtsp_for_ui(self.config.get("rtsp_url", "")),
                "gpio_enabled": bool(self.config.get("gpio_enabled", False)),
                "gpio_pin": int(self.config.get("gpio_pin", 17)),
                "gpio_state": self.gpio_state,
                "event_status": bool(self.event_detected),
                "last_event_time": (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_event_time))
                    if self.last_event_time > 0 else "-"
                ),
                "config": dict(self.config),
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
