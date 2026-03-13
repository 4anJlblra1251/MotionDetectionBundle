from flask import Flask, Response, render_template, jsonify, request
from detector import MotionDetector
import threading
import argparse
import time
import curses

app = Flask(__name__)
detector = None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/video")
def video():
    mode = request.args.get("mode", "debug")

    def generate():
        while True:
            frame = detector.get_jpeg_frame(mode=mode)
            if frame is None:
                time.sleep(0.05)
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            )

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/status")
def status():
    return jsonify(detector.get_status())

        
@app.route("/api/config", methods=["GET", "POST"])
def config():
    if request.method == "GET":
        return jsonify(detector.config)

    new_config = request.json
    detector.save_config(new_config)
    return jsonify({"status": "ok"})


def draw_console_ui(stdscr, detector_obj):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(500)

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        status = detector_obj.get_status()
        cfg = status["config"]

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        title = " Motion Detection Service "
        stdscr.addstr(0, max(0, (w - len(title)) // 2), title, curses.A_REVERSE)

        line = 2
        stdscr.addstr(line, 2, f"Текущее время:      {now_str}")
        line += 1
        stdscr.addstr(line, 2, f"RTSP:               {cfg.get('rtsp_url', '')}")
        line += 1
        stdscr.addstr(line, 2, f"Min area:           {cfg.get('min_area')}")
        line += 1
        stdscr.addstr(line, 2, f"Motion frames:      {cfg.get('motion_frames_threshold')}")
        line += 1
        stdscr.addstr(line, 2, f"Var threshold:      {cfg.get('var_threshold')}")
        line += 1
        stdscr.addstr(line, 2, f"Event delay:        {cfg.get('event_delay')} sec")
        line += 1
        stdscr.addstr(line, 2, f"GPIO enabled:       {cfg.get('gpio_enabled')}")
        line += 1
        stdscr.addstr(line, 2, f"GPIO pin (BCM):     {cfg.get('gpio_pin')}")
        line += 1
        stdscr.addstr(line, 2, f"GPIO hold seconds:  {cfg.get('gpio_hold_seconds')}")
        line += 1
        stdscr.addstr(line, 2, f"GPIO initialized:   {status.get('gpio_initialized')}")
        line += 1
        stdscr.addstr(line, 2, f"GPIO busy:          {status.get('gpio_busy')}")
        line += 1
        stdscr.addstr(line, 2, f"Contours count:     {status.get('contours_count')}")
        line += 1
        stdscr.addstr(line, 2, f"Max area:           {status.get('max_area')}")
        line += 1
        stdscr.addstr(line, 2, f"Motion frames:      {status.get('motion_frames')}")
        line += 1
        stdscr.addstr(line, 2, f"Event detected:     {status.get('event_detected')}")

        log_title_line = line + 2
        stdscr.addstr(log_title_line, 2, "Лог событий:", curses.A_BOLD)

        logs = status.get("logs", [])
        max_log_lines = max(3, h - log_title_line - 3)
        visible_logs = logs[-max_log_lines:]

        for idx, entry in enumerate(visible_logs):
            y = log_title_line + 1 + idx
            if y < h - 1:
                stdscr.addstr(y, 4, entry[:w - 8])

        footer = "Q - выход"
        stdscr.addstr(h - 1, max(0, (w - len(footer)) // 2), footer, curses.A_REVERSE)

        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (ord('q'), ord('Q')):
            break


def run_normal_console(detector_obj):
    curses.wrapper(draw_console_ui, detector_obj)


def main():
    global detector

    parser = argparse.ArgumentParser(description="Motion detection service")
    parser.add_argument("--debug", action="store_true", help="run in debug mode with web UI")
    args = parser.parse_args()

    detector = MotionDetector("config.json", debug=args.debug)

    t = threading.Thread(target=detector.process_loop, daemon=True)
    t.start()

    if args.debug:
        print("Запуск в режиме отладки")
        app.run(host="0.0.0.0", port=5000, debug=False)
    else:
        print("Запуск в обычном режиме")
        try:
            run_normal_console(detector)
        except KeyboardInterrupt:
            print("Остановка")


if __name__ == "__main__":
    main()