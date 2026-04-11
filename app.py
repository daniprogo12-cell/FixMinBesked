from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from services.ai_client import rewrite_text

load_dotenv()

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


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

    return jsonify({"result": result})


if __name__ == "__main__":
    app.run(debug=True)