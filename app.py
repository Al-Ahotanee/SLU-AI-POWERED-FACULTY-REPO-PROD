import os, json, re, math, uuid, logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from collections import defaultdict

from flask import Flask, request, jsonify, send_from_directory, g
from flask_cors import CORS
import sqlite3, jwt, bcrypt
import requests

# ── CONFIG ─────────────────────────────────────────
def _utcnow(): return datetime.now(timezone.utc)

BASE_DIR   = Path(__file__).parent
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(BASE_DIR / "uploads")))
DB_PATH    = Path(os.environ.get("DB_PATH", str(BASE_DIR / "repository.db")))
SECRET     = os.environ.get("SECRET_KEY", "super-secret-key")
PORT       = int(os.environ.get("PORT", 5000))

# ✅ HuggingFace API URL (CHANGE THIS)
HF_API = os.environ.get("HF_API", "https://your-space-name.hf.space")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── APP INIT ───────────────────────────────────────
app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")
CORS(app)

# ── DATABASE ───────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db: db.close()

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        filename TEXT,
        embedding TEXT
    );
    """)
    db.commit()

# ✅ Ensure DB runs on Render
with app.app_context():
    init_db()

# ── AUTH (Simple) ──────────────────────────────────
def make_token():
    return jwt.encode({"exp": _utcnow() + timedelta(days=1)}, SECRET, algorithm="HS256")

# ── AI: CALL HUGGINGFACE ──────────────────────────
def get_embedding(text):
    try:
        res = requests.post(
            f"{HF_API}/embed",
            json={"text": text[:2000]},
            timeout=10
        )
        return res.json().get("embedding")
    except Exception as e:
        print("HF ERROR:", e)
        return None

def cosine(v1, v2):
    if not v1 or not v2:
        return 0
    import numpy as np
    v1, v2 = np.array(v1), np.array(v2)
    return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9))

# ── ROUTES ─────────────────────────────────────────

# ✅ SPA frontend
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def spa(path):
    return send_from_directory(str(BASE_DIR), "index.html")

# ✅ Health check
@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

# ✅ Upload + embedding
@app.route("/api/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file"}), 400

    filename = f"{uuid.uuid4().hex}_{file.filename}"
    path = UPLOAD_DIR / filename
    file.save(path)

    text = file.filename
    embedding = get_embedding(text)

    db = get_db()
    db.execute(
        "INSERT INTO documents (title, filename, embedding) VALUES (?, ?, ?)",
        (file.filename, filename, json.dumps(embedding) if embedding else "")
    )
    db.commit()

    return jsonify({"message": "uploaded"})

# ✅ Search using AI
@app.route("/api/search")
def search():
    query = request.args.get("q", "")
    db = get_db()

    rows = db.execute("SELECT * FROM documents").fetchall()
    docs = [dict(r) for r in rows]

    q_emb = get_embedding(query)

    scored = []
    for d in docs:
        emb = json.loads(d["embedding"]) if d["embedding"] else None
        sim = cosine(q_emb, emb)
        d["score"] = sim
        scored.append(d)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return jsonify(scored[:10])


# ✅ Run (for local only)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
