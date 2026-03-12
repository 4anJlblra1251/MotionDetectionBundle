from flask import Flask, Response, render_template, jsonify, request
from detector import MotionDetector
import threading
import argparse
import time

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
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Остановка")


if __name__ == "__main__":
    main()