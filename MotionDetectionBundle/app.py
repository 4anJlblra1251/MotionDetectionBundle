from flask import Flask, Response, render_template, jsonify, request
from detector import MotionDetector
import threading
import argparse
import time
import curses
import json
from copy import deepcopy

app = Flask(__name__)
manager = None
SETUP_MODE = False

DEFAULT_CAMERA_CONFIG = {
    "rtsp_url": "rtsp://user:pass@127.0.0.1:554/stream",
    "min_area": 12000,
    "motion_frames_threshold": 5,
    "var_threshold": 60,
    "event_delay": 5,
    "blur_kernel": 7,
    "event_hold_seconds": 3,
    "gpio_enabled": False,
    "gpio_pin": 17,
    "gpio_hold_seconds": 3.0,
    "gpio_active_high": True,
    "reconnect_max_attempts": 0,
    "reconnect_retry_interval": 1.0,
}


class MultiCameraManager:
    def __init__(self, config_path="config.json", debug=False):
        self.config_path = config_path
        self.debug = debug
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.detectors = {}
        self.threads = {}
        self.config = self._load_config_file()
        self._sync_detectors_with_config(start_threads=False)

    def _normalize_config(self, raw):
        if "cameras" in raw:
            cameras = raw.get("cameras", [])
            active = raw.get("active_camera") or (cameras[0]["id"] if cameras else None)
            return {
                "active_camera": active,
                "cameras": cameras,
            }

        # Миграция старого формата
        migrated_camera = {
            "id": "camera-1",
            "name": "Camera 1",
            "config": {**DEFAULT_CAMERA_CONFIG, **raw},
        }
        return {
            "active_camera": "camera-1",
            "cameras": [migrated_camera],
        }

    def _load_config_file(self):
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return self._normalize_config(raw)

    def _save_config_file(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def _sync_detectors_with_config(self, start_threads=True):
        existing_ids = set(self.detectors.keys())
        config_ids = {camera["id"] for camera in self.config["cameras"]}

        for removed_id in existing_ids - config_ids:
            detector = self.detectors.pop(removed_id)
            detector.running = False
            if detector.cap is not None:
                detector.cap.release()
            self.threads.pop(removed_id, None)

        for camera in self.config["cameras"]:
            camera_id = camera["id"]
            merged_config = {**DEFAULT_CAMERA_CONFIG, **camera["config"]}

            if camera_id in self.detectors:
                self.detectors[camera_id].update_config(merged_config)
                continue

            detector = MotionDetector(camera_id=camera_id, config=merged_config, debug=self.debug)
            self.detectors[camera_id] = detector
            if start_threads:
                thread = threading.Thread(target=detector.process_loop, daemon=True)
                thread.start()
                self.threads[camera_id] = thread

    def start(self):
        with self.lock:
            for camera_id, detector in self.detectors.items():
                if camera_id in self.threads and self.threads[camera_id].is_alive():
                    continue
                thread = threading.Thread(target=detector.process_loop, daemon=True)
                thread.start()
                self.threads[camera_id] = thread

    def list_cameras(self):
        with self.lock:
            return deepcopy(self.config["cameras"])

    def add_camera(self, name, camera_config=None):
        with self.lock:
            next_number = len(self.config["cameras"]) + 1
            camera_id = f"camera-{next_number}"
            while any(c["id"] == camera_id for c in self.config["cameras"]):
                next_number += 1
                camera_id = f"camera-{next_number}"

            new_camera = {
                "id": camera_id,
                "name": name or f"Camera {next_number}",
                "config": {**DEFAULT_CAMERA_CONFIG, **(camera_config or {})},
            }
            self.config["cameras"].append(new_camera)
            if not self.config.get("active_camera"):
                self.config["active_camera"] = camera_id
            self._save_config_file()
            self._sync_detectors_with_config(start_threads=True)
            return deepcopy(new_camera)

    def get_camera(self, camera_id):
        with self.lock:
            for camera in self.config["cameras"]:
                if camera["id"] == camera_id:
                    return deepcopy(camera)
        return None

    def get_active_camera_id(self):
        with self.lock:
            return self.config.get("active_camera")

    def set_active_camera(self, camera_id):
        with self.lock:
            if not any(c["id"] == camera_id for c in self.config["cameras"]):
                return False
            self.config["active_camera"] = camera_id
            self._save_config_file()
            return True

    def get_detector(self, camera_id=None):
        with self.lock:
            effective_id = camera_id or self.config.get("active_camera")
            return self.detectors.get(effective_id)

    def get_status(self, camera_id=None):
        detector = self.get_detector(camera_id)
        if detector is None:
            return {"error": "camera not found"}
        return detector.get_status()

    def update_camera_config(self, camera_id, new_config):
        with self.lock:
            for camera in self.config["cameras"]:
                if camera["id"] == camera_id:
                    camera["config"] = {**DEFAULT_CAMERA_CONFIG, **new_config}
                    self._save_config_file()
                    self._sync_detectors_with_config(start_threads=False)
                    return True
        return False

    def get_uptime_display(self):
        uptime_seconds = max(0, int(time.time() - self.started_at))
        hours, rem = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def get_overview(self):
        with self.lock:
            cameras = deepcopy(self.config["cameras"])
            detectors = dict(self.detectors)

        camera_rows = []
        warning_logs = []
        critical_logs = []

        for camera in cameras:
            detector = detectors.get(camera["id"])
            status = detector.get_status() if detector else None

            if status is not None:
                gpio_state = status["gpio_state"]
                address = status.get("rtsp_display", status.get("rtsp_url", "-"))
                logs = status.get("logs", [])
            else:
                gpio_state = "N/A"
                address = "-"
                logs = []

            camera_rows.append({
                "name": camera["name"],
                "gpio_state": gpio_state,
                "address": address,
            })

            for entry in logs:
                lowered = entry.lower()
                if any(marker in lowered for marker in ["critical", "crit", "fatal"]):
                    critical_logs.append(entry)
                elif any(marker in lowered for marker in ["error", "failed", "warning", "warn"]):
                    warning_logs.append(entry)

        return {
            "uptime": self.get_uptime_display(),
            "camera_rows": camera_rows,
            "warning_logs": warning_logs[-20:],
            "critical_logs": critical_logs[-20:],
        }


@app.route("/")
def index():
    return render_template("index.html", setup_mode=SETUP_MODE)


@app.route("/video")
def video():
    mode = request.args.get("mode", "debug")
    camera_id = request.args.get("camera_id")

    def generate():
        while True:
            detector = manager.get_detector(camera_id)
            frame = detector.get_jpeg_frame(mode=mode) if detector else None
            if frame is None:
                time.sleep(0.05)
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            )

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/cameras", methods=["GET", "POST"])
def cameras_api():
    if request.method == "GET":
        return jsonify({
            "cameras": manager.list_cameras(),
            "active_camera": manager.get_active_camera_id(),
            "setup_mode": SETUP_MODE,
        })

    if not SETUP_MODE:
        return jsonify({"error": "adding cameras allowed only in --setup mode"}), 403

    payload = request.json or {}
    name = payload.get("name")
    camera_config = payload.get("config") or {}
    camera = manager.add_camera(name=name, camera_config=camera_config)
    return jsonify(camera)


@app.route("/api/active_camera", methods=["POST"])
def set_active_camera():
    payload = request.json or {}
    camera_id = payload.get("camera_id")
    if not camera_id:
        return jsonify({"error": "camera_id is required"}), 400

    ok = manager.set_active_camera(camera_id)
    if not ok:
        return jsonify({"error": "camera not found"}), 404

    return jsonify({"status": "ok"})


@app.route("/api/status")
def status():
    camera_id = request.args.get("camera_id")
    return jsonify(manager.get_status(camera_id))


@app.route("/api/config", methods=["GET", "POST"])
def config():
    camera_id = request.args.get("camera_id") or manager.get_active_camera_id()
    if not camera_id:
        return jsonify({"error": "no cameras configured"}), 404

    if request.method == "GET":
        camera = manager.get_camera(camera_id)
        if camera is None:
            return jsonify({"error": "camera not found"}), 404
        return jsonify(camera["config"])

    new_config = request.json or {}
    ok = manager.update_camera_config(camera_id, new_config)
    if not ok:
        return jsonify({"error": "camera not found"}), 404
    return jsonify({"status": "ok"})


def draw_box(stdscr, y, x, h, w, title):
    stdscr.addstr(y, x + 2, f" {title} ", curses.A_BOLD)
    for i in range(w):
        stdscr.addch(y + 1, x + i, curses.ACS_HLINE)
        stdscr.addch(y + h - 1, x + i, curses.ACS_HLINE)
    for i in range(1, h):
        stdscr.addch(y + i, x, curses.ACS_VLINE)
        stdscr.addch(y + i, x + w - 1, curses.ACS_VLINE)

    stdscr.addch(y + 1, x, curses.ACS_ULCORNER)
    stdscr.addch(y + 1, x + w - 1, curses.ACS_URCORNER)
    stdscr.addch(y + h - 1, x, curses.ACS_LLCORNER)
    stdscr.addch(y + h - 1, x + w - 1, curses.ACS_LRCORNER)


def draw_console_ui(stdscr, manager_obj):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(500)

    selected_idx = 0
    selected_tab = "overview"

    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_YELLOW, -1)

    while True:
        cameras = manager_obj.list_cameras()
        if not cameras:
            stdscr.erase()
            stdscr.addstr(0, 0, "Нет камер в config.json. Используйте --setup.")
            stdscr.refresh()
            time.sleep(0.5)
            continue

        selected_idx = max(0, min(selected_idx, len(cameras) - 1))
        selected_camera = cameras[selected_idx]

        if selected_tab == "camera":
            manager_obj.set_active_camera(selected_camera["id"])
            status = manager_obj.get_status(selected_camera["id"])
        else:
            status = None
            overview = manager_obj.get_overview()

        stdscr.erase()
        h, w = stdscr.getmaxyx()

        tab_items = ["[Overview]" if selected_tab == "overview" else "Overview"]
        for i, cam in enumerate(cameras):
            is_active = selected_tab == "camera" and i == selected_idx
            tab_items.append(f"[{cam['name']}]" if is_active else cam["name"])
        tabs = " | ".join(tab_items)
        stdscr.addstr(0, 1, tabs[:w - 2], curses.A_REVERSE)

        if selected_tab == "overview":
            top_h = 8
            split_h = max(6, (h - top_h - 4) // 2)
            log_h = h - top_h - split_h - 4

            draw_box(stdscr, 1, 1, top_h, w - 2, "SYSTEM OVERVIEW")
            draw_box(stdscr, top_h + 1, 1, split_h, w - 2, "CAMERAS")
            draw_box(stdscr, top_h + split_h + 1, 1, log_h + 2, w - 2, "WARN / CRITICAL LOGS")

            stdscr.addstr(3, 3, f"Current time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            stdscr.addstr(4, 3, f"Uptime: {overview['uptime']}")
            stdscr.addstr(5, 3, f"Active cameras: {len(overview['camera_rows'])}")

            camera_max_lines = max(1, split_h - 3)
            for i, row in enumerate(overview["camera_rows"][:camera_max_lines]):
                camera_line = f"- {row['name']}: GPIO={row['gpio_state']}, addr={row['address']}"
                stdscr.addstr(top_h + 3 + i, 3, camera_line[:w - 6])

            combined_logs = [("CRIT", item) for item in overview["critical_logs"]] + [
                ("WARN", item) for item in overview["warning_logs"]
            ]
            combined_logs = combined_logs[-max(1, log_h):]

            for i, (level, entry) in enumerate(combined_logs):
                color = curses.color_pair(3) if level == "CRIT" else curses.color_pair(4)
                stdscr.addstr(top_h + split_h + 3 + i, 3, f"[{level}] ", color | curses.A_BOLD)
                stdscr.addstr(entry[:w - 16])
        else:
            top_h = 11
            left_w = max(50, w // 2)
            right_w = w - left_w - 4

            draw_box(stdscr, 1, 1, top_h, left_w, f"SYSTEM ({selected_camera['id']})")
            draw_box(stdscr, 1, left_w + 2, top_h, right_w, "EVENT / GPIO")
            draw_box(stdscr, top_h + 1, 1, h - top_h - 3, w - 2, "LOGS")

            stdscr.addstr(3, 3, f"Time: {status['current_time']}")
            stdscr.addstr(4, 3, f"RTSP: {status.get('rtsp_display', status['rtsp_url'])[:left_w - 10]}")
            stdscr.addstr(5, 3, f"GPIO enabled: {status['gpio_enabled']}")
            stdscr.addstr(6, 3, f"GPIO pin: {status['gpio_pin']}")

            event_str = "TRUE" if status["event_status"] else "FALSE"
            gpio_str = status["gpio_state"]

            event_color = curses.color_pair(2) if status["event_status"] else curses.color_pair(3)
            gpio_color = curses.color_pair(2) if gpio_str == "HIGH" else curses.color_pair(4)

            stdscr.addstr(3, left_w + 4, "Event status: ")
            stdscr.addstr(event_str, event_color | curses.A_BOLD)

            stdscr.addstr(5, left_w + 4, "GPIO state: ")
            stdscr.addstr(gpio_str, gpio_color | curses.A_BOLD)

            stdscr.addstr(7, left_w + 4, f"Last event: {status['last_event_time']}")

            logs = status.get("logs", [])
            log_y = top_h + 3
            log_max_lines = h - log_y - 2

            for i, entry in enumerate(logs[-log_max_lines:]):
                stdscr.addstr(log_y + i, 3, entry[:w - 6])

        footer = "TAB - overview/camera | ←/→ - смена камеры | Q - exit"
        stdscr.addstr(h - 1, max(0, (w - len(footer)) // 2), footer, curses.A_REVERSE)

        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (ord('q'), ord('Q')):
            break
        if ch == 9:
            selected_tab = "camera" if selected_tab == "overview" else "overview"
        if ch == curses.KEY_RIGHT:
            if selected_tab != "camera":
                selected_tab = "camera"
            selected_idx = (selected_idx + 1) % len(cameras)
        if ch == curses.KEY_LEFT:
            if selected_tab != "camera":
                selected_tab = "camera"
            selected_idx = (selected_idx - 1) % len(cameras)


def run_normal_console(manager_obj):
    curses.wrapper(draw_console_ui, manager_obj)


def main():
    global manager, SETUP_MODE

    parser = argparse.ArgumentParser(description="Motion detection service")
    parser.add_argument("--debug", action="store_true", help="run in debug mode with web UI")
    parser.add_argument("--setup", action="store_true", help="run setup mode with web UI and camera creation")
    args = parser.parse_args()

    SETUP_MODE = args.setup

    manager = MultiCameraManager("config.json", debug=(args.debug or args.setup))
    manager.start()

    if args.debug or args.setup:
        mode_name = "setup" if args.setup else "debug"
        print(f"Запуск в режиме {mode_name}")
        app.run(host="0.0.0.0", port=5000, debug=False)
    else:
        print("Запуск в обычном режиме")
        try:
            run_normal_console(manager)
        except KeyboardInterrupt:
            print("Остановка")


if __name__ == "__main__":
    main()
