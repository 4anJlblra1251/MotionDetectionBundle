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


def draw_console_ui(stdscr, detector_obj):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(500)

    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_YELLOW, -1)

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        status = detector_obj.get_status()

        title = " Motion Detection Service "
        stdscr.addstr(0, max(0, (w - len(title)) // 2), title, curses.A_REVERSE)

        top_h = 11
        left_w = max(50, w // 2)
        right_w = w - left_w - 4

        draw_box(stdscr, 1, 1, top_h, left_w, "SYSTEM")
        draw_box(stdscr, 1, left_w + 2, top_h, right_w, "EVENT / GPIO")
        draw_box(stdscr, top_h + 1, 1, h - top_h - 3, w - 2, "LOGS")

        # SYSTEM
        stdscr.addstr(3, 3, f"Time: {status['current_time']}")
        stdscr.addstr(4, 3, f"RTSP: {status['rtsp_url'][:left_w - 10]}")
        stdscr.addstr(5, 3, f"GPIO enabled: {status['gpio_enabled']}")
        stdscr.addstr(6, 3, f"GPIO pin: {status['gpio_pin']}")

        # EVENT / GPIO
        event_str = "TRUE" if status["event_status"] else "FALSE"
        gpio_str = status["gpio_state"]

        event_color = curses.color_pair(2) if status["event_status"] else curses.color_pair(3)
        gpio_color = curses.color_pair(2) if gpio_str == "HIGH" else curses.color_pair(4)

        stdscr.addstr(3, left_w + 4, "Event status: ")
        stdscr.addstr(event_str, event_color | curses.A_BOLD)

        stdscr.addstr(5, left_w + 4, "GPIO state: ")
        stdscr.addstr(gpio_str, gpio_color | curses.A_BOLD)

        stdscr.addstr(7, left_w + 4, f"Last event: {status['last_event_time']}")

        # LOGS
        logs = status.get("logs", [])
        log_y = top_h + 3
        log_max_lines = h - log_y - 2

        for i, entry in enumerate(logs[-log_max_lines:]):
            stdscr.addstr(log_y + i, 3, entry[:w - 6])

        footer = "Q - exit"
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