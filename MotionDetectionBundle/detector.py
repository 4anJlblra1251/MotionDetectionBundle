import os
import contextlib
import time
import threading
from collections import deque
from urllib.parse import urlparse

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
os.environ.setdefault("FFMPEG_LOG_LEVEL", "quiet")

import cv2

_STDERR_SILENCE_LOCK = threading.Lock()


@contextlib.contextmanager
def silence_stderr():
    if not hasattr(os, "dup") or not hasattr(os, "dup2"):
        yield
        return

    with _STDERR_SILENCE_LOCK:
        saved_stderr = None
        devnull = None
        try:
            saved_stderr = os.dup(2)
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 2)
            yield
        finally:
            if saved_stderr is not None:
                os.dup2(saved_stderr, 2)
                os.close(saved_stderr)
            if devnull is not None:
                os.close(devnull)

try:
    from gpiozero import OutputDevice
except Exception:
    OutputDevice = None


try:
    if hasattr(cv2, "setLogLevel") and hasattr(cv2, "LOG_LEVEL_ERROR"):
        cv2.setLogLevel(cv2.LOG_LEVEL_ERROR)
    elif hasattr(cv2, "utils") and hasattr(cv2.utils, "logging"):
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except Exception:
    pass


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
        self.event_detection_enabled = True
        self.last_event_time = 0.0
        self.last_motion_seen_time = 0.0
        self.motion_frames = 0

        self.running = False
        self.cap = None
        self.fgbg = None
        self.connection_ok = False
        self.safety_disabled = False

        self.gpio_device = None
        self.gpio_busy = False
        self.gpio_state = "LOW"

        self.log_buffer = deque(maxlen=30)
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

    def _force_gpio_low(self):
        if self.gpio_device is not None:
            try:
                self.gpio_device.off()
            except Exception:
                pass
        self.gpio_state = "LOW"

    def _enter_safety_mode(self, reason: str):
        if self.safety_disabled:
            return

        self.safety_disabled = True
        self.connection_ok = False
        self.event_detection_enabled = False
        self.event_detected = False
        self.motion_frames = 0
        self.last_motion_seen_time = 0.0
        self._force_gpio_low()

        self.add_log(f"{reason}. Safety mode enabled")
        self.add_log("GPIO forced LOW due to connection error")

    def _exit_safety_mode(self):
        if not self.safety_disabled:
            return

        self.safety_disabled = False
        self.connection_ok = True
        self.event_detection_enabled = True
        self.event_detected = False
        self.motion_frames = 0
        self.last_motion_seen_time = 0.0
        self.add_log("Camera stream restored, safety mode disabled")

    def trigger_gpio(self):
        if not self.event_detection_enabled:
            return

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
        with silence_stderr():
            self.cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        ok = self.cap.isOpened()

        if ok:
            self.add_log("RTSP opened")
        return ok

    def reconnect_with_limit(self):
        max_attempts = int(self.config.get("reconnect_max_attempts", 0))
        retry_interval = max(0.1, float(self.config.get("reconnect_retry_interval", 1.0)))
        attempt = 0

        while self.running:
            attempt += 1
            if self.open_stream():
                if attempt > 1:
                    self.add_log(f"Reconnect success on attempt {attempt}")
                return True

            self._enter_safety_mode("RTSP open failed")

            if max_attempts > 0 and attempt >= max_attempts:
                self.add_log(f"Reconnect failed after {max_attempts} attempts")
                time.sleep(retry_interval)
                return False

            if attempt == 1 or attempt % 5 == 0:
                self.add_log(f"Reconnect attempt {attempt} failed")
            time.sleep(retry_interval)

        return False

    def process_loop(self):
        self.running = True

        while self.running:
            if not self.reconnect_with_limit():
                continue

            while self.running:
                ret, frame = self.cap.read()

                if not ret or frame is None:
                    self._enter_safety_mode("Frame read error")
                    time.sleep(max(0.1, float(self.config.get("reconnect_retry_interval", 1.0))))
                    break

                if not self.connection_ok:
                    self._exit_safety_mode()

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

                    if self.event_detection_enabled and self.motion_frames >= self.config["motion_frames_threshold"]:
                        self.last_motion_seen_time = now

                        if now - self.last_event_time > self.config["event_delay"]:
                            self.last_event_time = now
                            should_fire_event = True
                            event_log_message = f"EVENT area={int(max_area)}"

                    hold_time = float(self.config.get("event_hold_seconds", 3))
                    if self.event_detection_enabled:
                        self.event_detected = (now - self.last_motion_seen_time) < hold_time
                    else:
                        self.event_detected = False

                    if self.debug:
                        cv2.putText(debug_frame, f"event={self.event_detected}", (10, 25),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                        cv2.putText(debug_frame, f"gpio={self.get_gpio_state_label()}", (10, 55),
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

    def get_gpio_state_label(self):
        if self.safety_disabled:
            return "Safety disabled"
        return self.gpio_state

    def get_status(self):
        with self.lock:
            return {
                "camera_id": self.camera_id,
                "current_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "rtsp_url": self.config.get("rtsp_url", ""),
                "rtsp_display": mask_rtsp_for_ui(self.config.get("rtsp_url", "")),
                "gpio_enabled": bool(self.config.get("gpio_enabled", False)),
                "gpio_pin": int(self.config.get("gpio_pin", 17)),
                "gpio_state": self.get_gpio_state_label(),
                "event_status": bool(self.event_detected),
                "event_detection_enabled": bool(self.event_detection_enabled),
                "camera_connected": bool(self.connection_ok),
                "safety_disabled": bool(self.safety_disabled),
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
