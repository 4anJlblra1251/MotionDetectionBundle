import cv2
import time
import json
import threading


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
        self.last_event_time = 0

        self.running = False
        self.cap = None
        self.fgbg = None

        self.load_config()
        self.init_detector()

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

    def init_detector(self):
        self.fgbg = cv2.createBackgroundSubtractorMOG2(
            history=700,
            varThreshold=self.config["var_threshold"],
            detectShadows=False
        )

    def open_stream(self):
        if self.cap is not None:
            self.cap.release()

        self.cap = cv2.VideoCapture(self.config["rtsp_url"], cv2.CAP_FFMPEG)
        return self.cap.isOpened()

    def process_loop(self):
        self.running = True

        if not self.open_stream():
            print("Не удалось открыть RTSP поток")
            return

        while self.running:
            ret, frame = self.cap.read()

            if not ret:
                print("Ошибка чтения кадра")
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
                        print(
                            f"[EVENT] motion detected | "
                            f"max_area={int(max_area)} | "
                            f"contours={contours_count} | "
                            f"motion_frames={self.motion_frames}"
                        )

                if self.debug:
                    cv2.putText(debug_frame, f"max_area={int(max_area)}", (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    cv2.putText(debug_frame, f"contours={contours_count}", (10, 55),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    cv2.putText(debug_frame, f"motion_frames={self.motion_frames}", (10, 85),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                    cv2.putText(debug_frame, f"event={self.event_detected}", (10, 115),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

                if self.debug:
                    self.last_frame = frame
                    self.last_debug_frame = debug_frame
                    self.last_mask = mask
                    self.last_thresh = thresh
                else:
                    self.last_frame = None
                    self.last_debug_frame = None
                    self.last_mask = None
                    self.last_thresh = None

    def get_status(self):
        with self.lock:
            return {
                "max_area": int(self.max_area),
                "contours_count": int(self.contours_count),
                "motion_frames": int(self.motion_frames),
                "event_detected": bool(self.event_detected),
                "last_event_time": float(self.last_event_time),
                "config": self.config
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