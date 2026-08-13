"""
Task 3: mini audio collection app.

Routes
------
GET  /             submission form (record in browser or upload a file)
POST /submit       store the audio, extract properties, write a DB row
GET  /submissions  list every submission with a player and its properties
GET  /audio/<name> serve a stored file back for playback

Connection to Task 1
--------------------
The phone number a worker types is put through the SAME normalise_phone()
used by the merge pipeline, then looked up against person.primary_phone.
A match links the recording to an existing golden record; no match still
stores the submission with person_id NULL. We never invent a person here -
creating identities from an unverified web form would poison the merged
database that Task 1 worked to keep clean.
"""

import os
import sqlite3
import sys
import uuid
from pathlib import Path

from flask import (Flask, g, jsonify, redirect, render_template,
                   request, send_from_directory, url_for)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from audio_meta import extract          # noqa: E402
from normalize import normalise_phone   # noqa: E402

DB_PATH = ROOT / "consultbae.db"
UPLOAD_DIR = ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_BYTES = 25 * 1024 * 1024            # reject anything over 25 MB
ALLOWED_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac", ".aac", ".opus"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES


# ------------------------------------------------------------------ db
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def find_person(phone10):
    """Look up a golden record by normalised phone. Returns a Row or None."""
    if not phone10:
        return None
    return db().execute(
        "SELECT person_id, full_name, city FROM person WHERE primary_phone = ?",
        (phone10,),
    ).fetchone()


# ------------------------------------------------------------------ views
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/submit", methods=["POST"])
def submit():
    name = (request.form.get("name") or "").strip()
    phone_raw = (request.form.get("phone") or "").strip()
    audio = request.files.get("audio")

    if not name or not phone_raw:
        return jsonify(ok=False, error="Name and phone are both required."), 400
    if audio is None or not audio.filename:
        return jsonify(ok=False, error="No audio was attached."), 400

    # normalise_phone returns (clean_value, issue_or_None) - same contract
    # as every other normaliser in the merge pipeline.
    phone10, phone_issue = normalise_phone(phone_raw)
    if not phone10:
        return jsonify(ok=False,
                       error=f"That phone number does not look valid ({phone_issue})."), 400

    ext = os.path.splitext(audio.filename)[1].lower() or ".webm"
    if ext not in ALLOWED_EXT:
        return jsonify(ok=False, error=f"Unsupported file type '{ext}'."), 400

    # uuid filename: two workers submitting "recording.webm" must not collide,
    # and a user-supplied filename must never touch the filesystem path.
    stored = f"{uuid.uuid4().hex}{ext}"
    path = UPLOAD_DIR / stored
    audio.save(path)

    if path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        return jsonify(ok=False, error="The recording was empty."), 400

    # Extract from the ORIGINAL upload, not a transcode: the bitrate of a
    # re-encoded copy is not the bitrate the worker actually recorded at.
    meta = extract(str(path))

    person = find_person(phone10)
    conn = db()
    cur = conn.execute(
        "INSERT INTO audio_submission (person_id, submitted_name, submitted_phone,"
        " file_path, duration_sec, sample_rate_khz, bitrate_kbps, loudness_db,"
        " noise_estimate) VALUES (?,?,?,?,?,?,?,?,?)",
        (person["person_id"] if person else None,
         name, phone10, stored,
         meta["duration_sec"], meta["sample_rate_khz"], meta["bitrate_kbps"],
         meta["loudness_db"], meta["noise_estimate"]),
    )
    conn.commit()

    return jsonify(
        ok=True,
        submission_id=cur.lastrowid,
        matched_person=(dict(person) if person else None),
        properties=meta,
        warning=meta.get("error"),
    )


@app.route("/submissions")
def submissions():
    rows = db().execute(
        "SELECT s.*, p.full_name AS person_name, p.city AS person_city"
        " FROM audio_submission s"
        " LEFT JOIN person p ON p.person_id = s.person_id"
        " ORDER BY s.submission_id DESC"
    ).fetchall()
    return render_template("submissions.html", rows=rows)


@app.route("/audio/<path:name>")
def audio_file(name):
    # send_from_directory refuses paths that escape the directory.
    return send_from_directory(UPLOAD_DIR, name)


@app.errorhandler(413)
def too_large(e):
    return jsonify(ok=False, error="File is larger than 25 MB."), 413


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"No database at {DB_PATH}. Run: python src/init_db.py")
        sys.exit(1)
    app.run(debug=True, port=5000)