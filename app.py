from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from services.ai_client import rewrite_text
import os
import json
from datetime import datetime
from threading import Lock

load_dotenv()

app = Flask(__name__)

ANALYTICS_FILE = "analytics.json"
analytics_lock = Lock()


def ensure_analytics_file():
    if not os.path.exists(ANALYTICS_FILE):
        with open(ANALYTICS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)


def read_analytics():
    ensure_analytics_file()
    with open(ANALYTICS_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []


def write_analytics(data):
    with open(ANALYTICS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log_event(event_type, button):
    if not event_type or not button:
        return

    event = {
        "event_type": event_type,
        "button": button,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

    with analytics_lock:
        analytics = read_analytics()
        analytics.append(event)
        write_analytics(analytics)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    if request.args.get("key") != "hemmelig123":
        return "Unauthorized", 403
    return render_template("dashboard.html")


@app.route("/rewrite", methods=["POST"])
def rewrite():
    data = request.get_json(silent=True) or {}

    text = (data.get("text") or "").strip()
    tone = (data.get("tone") or "").strip()

    if not text:
        return jsonify({"error": "Du skal indsætte en besked."}), 400

    if not tone:
        return jsonify({"error": "Du skal vælge en stil."}), 400

    result = rewrite_text(text, tone)

    log_event("rewrite", tone)

    return jsonify({"result": result})


@app.route("/track-event", methods=["POST"])
def track_event():
    data = request.get_json(silent=True) or {}

    event_type = (data.get("event_type") or "").strip()
    button = (data.get("button") or "").strip()

    if not event_type or not button:
        return jsonify({"error": "Manglende eventdata."}), 400

    log_event(event_type, button)

    return jsonify({"success": True})


@app.route("/dashboard-data")
def dashboard_data():
    analytics = read_analytics()
    return jsonify({"events": analytics})


if __name__ == "__main__":
    ensure_analytics_file()
    app.run(debug=True)