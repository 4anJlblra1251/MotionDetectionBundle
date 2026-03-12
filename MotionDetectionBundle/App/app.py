from flask import Flask, Response, render_template, jsonify, request
from detector import MotionDetector
import threading

app = Flask(__name__)
detector = MotionDetector("config.json")


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


if __name__ == "__main__":
    t = threading.Thread(target=detector.process_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False)