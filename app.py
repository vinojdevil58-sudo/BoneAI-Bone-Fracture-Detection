import os
import io
import json
import sqlite3
import tempfile
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory, abort
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "boneai.db"
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
MODEL_PATH = BASE_DIR / "models" / "best.pt"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_CONTENT_LENGTH = 12 * 1024 * 1024

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("BONEAI_SECRET_KEY", "local-development-only-change-before-deployment")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

UPLOAD_DIR.mkdir(exist_ok=True)
RESULT_DIR.mkdir(exist_ok=True)
DB_PATH.parent.mkdir(exist_ok=True)

_model = None
_model_error = None
_supabase = None
_supabase_error = None
SIGNED_URL_SECONDS = 60


def get_supabase():
    """Create the Supabase client lazily using a server-side secret key."""
    global _supabase, _supabase_error
    if _supabase is not None:
        return _supabase
    if _supabase_error:
        raise RuntimeError(_supabase_error)
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SECRET_KEY in your .env file.")
    try:
        from supabase import create_client
        _supabase = create_client(url, key)
        return _supabase
    except Exception as exc:
        _supabase_error = f"Could not initialize Supabase: {exc}"
        raise RuntimeError(_supabase_error)


def storage_bucket():
    return os.getenv("SUPABASE_BUCKET", "boneai-xrays").strip() or "boneai-xrays"


def upload_private_xray(image_bytes, user_id, extension, content_type):
    """Upload bytes to the private bucket under a server-generated object path."""
    mime_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    extension = extension.lower()
    expected = mime_types.get(extension)
    if not expected or content_type != expected:
        raise ValueError("Unsupported image format.")
    if not image_bytes or len(image_bytes) > MAX_CONTENT_LENGTH:
        raise ValueError("Image is empty or larger than 12 MB.")
    object_path = f"user-{int(user_id)}/{uuid.uuid4().hex}{extension}"
    get_supabase().storage.from_(storage_bucket()).upload(
        path=object_path,
        file=image_bytes,
        file_options={"content-type": expected, "upsert": "false"},
    )
    return object_path


def create_xray_signed_url(object_path):
    """Create a short-lived URL for a private storage object."""
    if not object_path or object_path.startswith(("http://", "https://")):
        raise ValueError("Invalid private storage path.")
    response = get_supabase().storage.from_(storage_bucket()).create_signed_url(
        object_path, SIGNED_URL_SECONDS
    )
    if isinstance(response, dict):
        url = response.get("signedURL") or response.get("signedUrl")
        if url:
            return url
    raise RuntimeError("Supabase did not return a signed URL.")


def scan_access_allowed(scan):
    if not scan:
        return False
    if session.get("role") != "patient":
        return True
    return (scan["patient_code"] == session.get("username") or
            scan["patient_user_id"] == session.get("user_id"))

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'admin',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_user_id INTEGER,
            patient_code TEXT,
            patient_name TEXT,
            image_name TEXT NOT NULL,
            result_image TEXT,
            predicted_class TEXT,
            confidence REAL,
            detections_json TEXT,
            status TEXT NOT NULL DEFAULT 'uploaded',
            uploaded_by INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(uploaded_by) REFERENCES users(id)
        );
        """)
        # Migrate databases created by earlier versions.
        scan_columns = {row[1] for row in conn.execute("PRAGMA table_info(scans)").fetchall()}
        if "patient_user_id" not in scan_columns:
            conn.execute("ALTER TABLE scans ADD COLUMN patient_user_id INTEGER")
        # No default/demo administrator is created. Use create_doctor.py to add a doctor account.

def get_model():
    global _model, _model_error
    if _model is not None:
        return _model
    if _model_error:
        raise RuntimeError(_model_error)
    if not MODEL_PATH.is_file():
        _model_error = "Model file is missing: models/best.pt"
        raise RuntimeError(_model_error)
    try:
        from ultralytics import YOLO
        _model = YOLO(str(MODEL_PATH))
        return _model
    except Exception as exc:
        _model_error = f"Could not load models/best.pt: {exc}"
        raise RuntimeError(_model_error)

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in to open the dashboard.", "warning")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.errorhandler(413)
def too_large(_):
    flash("Image is too large. Maximum upload size is 12 MB.", "danger")
    return redirect(url_for("detect")) if "user_id" in session else ("Image too large (12 MB maximum).", 413)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            if user["role"] == "patient":
                return redirect(url_for("patient_dashboard"))
            return redirect(url_for("dashboard"))
        flash("Incorrect username or password.", "danger")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register_patient():
    if request.method == "POST":
        username = request.form.get("username", "").strip()[:80]
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(username) < 3 or len(password) < 8:
            flash("Use a username of at least 3 characters and a password of at least 8 characters.", "warning")
        elif password != confirm:
            flash("Passwords do not match.", "warning")
        else:
            try:
                with db() as conn:
                    conn.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",
                                 (username, generate_password_hash(password), "patient", datetime.now().isoformat(timespec="seconds")))
                flash("Patient account created. You can now sign in.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That username is already taken. Choose another one.", "danger")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    if session.get("role") == "patient":
        return redirect(url_for("patient_dashboard"))
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) n FROM scans").fetchone()["n"]
        completed = conn.execute("SELECT COUNT(*) n FROM scans WHERE status='complete'").fetchone()["n"]
        recent = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 8").fetchall()
        fracture_count = conn.execute(
            "SELECT COUNT(*) n FROM scans WHERE status='complete' AND lower(COALESCE(predicted_class,'')) LIKE '%fracture%'"
        ).fetchone()["n"]
    return render_template("dashboard.html", total=total, completed=completed, fracture_count=fracture_count, recent=recent, model_ready=MODEL_PATH.is_file(), now_date=datetime.now().strftime("%d %b %Y"))

@app.route("/detect", methods=["GET", "POST"])
@login_required
def detect():
    if request.method == "POST":
        uploaded = request.files.get("xray")
        if not uploaded or not uploaded.filename:
            flash("Choose an X-ray image first.", "warning")
            return redirect(url_for("detect"))
        if not allowed_file(uploaded.filename):
            flash("Supported formats: PNG, JPG, JPEG, WEBP.", "danger")
            return redirect(url_for("detect"))

        original = secure_filename(uploaded.filename)
        extension = Path(original).suffix.lower()
        mime_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
        if extension not in mime_types:
            flash("Unsupported image format.", "danger")
            return redirect(url_for("detect"))

        image_bytes = uploaded.read()
        if not image_bytes or len(image_bytes) > MAX_CONTENT_LENGTH:
            flash("Image is empty or larger than 12 MB.", "danger")
            return redirect(url_for("detect"))
        try:
            from PIL import Image
            with Image.open(io.BytesIO(image_bytes)) as im:
                if im.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Unsupported image contents")
                im.verify()
        except Exception:
            flash("The uploaded file is not a valid JPG, PNG, or WEBP image.", "danger")
            return redirect(url_for("detect"))

        patient_code = request.form.get("patient_code", "").strip()[:80]
        patient_name = request.form.get("patient_name", "").strip()[:120]
        if session.get("role") == "patient":
            patient_code = session.get("username", "")
            patient_name = patient_name or session.get("username", "")

        try:
            storage_path = upload_private_xray(
                image_bytes, session["user_id"], extension, mime_types[extension]
            )
        except Exception:
            app.logger.exception("Private Supabase upload failed")
            flash("Could not upload to private Supabase Storage. Check .env, bucket name, and Supabase key.", "danger")
            return redirect(url_for("detect"))

        with db() as conn:
            patient_user = conn.execute(
                "SELECT id FROM users WHERE username=? AND role='patient'", (patient_code,)
            ).fetchone() if patient_code else None
            cur = conn.execute(
                """INSERT INTO scans(patient_user_id, patient_code, patient_name, image_name, status, uploaded_by, created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (patient_user["id"] if patient_user else (session.get("user_id") if session.get("role") == "patient" else None),
                 patient_code or None, patient_name or None, storage_path, "uploaded", session.get("user_id"),
                 datetime.now().isoformat(timespec="seconds"))
            )
            scan_id = cur.lastrowid

        temp_path = None
        try:
            model = get_model()
            with tempfile.NamedTemporaryFile(suffix=extension, dir=UPLOAD_DIR, delete=False) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(image_bytes)
            results = model.predict(source=str(temp_path), verbose=False)
            result = results[0]
            detections = []
            for box in result.boxes:
                class_id = int(box.cls[0].item())
                confidence_value = float(box.conf[0].item())
                bbox = [round(float(v), 2) for v in box.xyxy[0].tolist()]
                detections.append({"class_name": str(result.names.get(class_id, class_id)),
                                   "confidence": round(confidence_value, 4), "bbox": bbox})
            if detections:
                best = max(detections, key=lambda item: item["confidence"])
                predicted_class, confidence_value = best["class_name"], best["confidence"]
            else:
                predicted_class, confidence_value = "No objects detected", None
            result_name = f"scan_{scan_id}.jpg"
            plotted = result.plot()
            Image.fromarray(plotted[:, :, ::-1]).save(RESULT_DIR / result_name, quality=92)
            with db() as conn:
                conn.execute(
                    """UPDATE scans SET result_image=?, predicted_class=?, confidence=?, detections_json=?, status='complete' WHERE id=?""",
                    (result_name, predicted_class, confidence_value, json.dumps(detections), scan_id)
                )
            flash("Analysis finished. This is an AI prediction, not a diagnosis.", "success")
        except Exception:
            app.logger.exception("YOLO analysis failed")
            with db() as conn:
                conn.execute("UPDATE scans SET status='error' WHERE id=?", (scan_id,))
            flash("The X-ray was stored, but analysis failed. Check models/best.pt and Python dependencies.", "danger")
        finally:
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    app.logger.warning("Could not remove temporary X-ray file")
        return redirect(url_for("result", scan_id=scan_id))

    return render_template("detect.html", model_ready=MODEL_PATH.is_file(), is_patient=session.get("role") == "patient")

@app.route("/patients")
@login_required
def patients():
    if session.get("role") == "patient":
        return redirect(url_for("patient_history"))
    with db() as conn:
        rows = conn.execute("""SELECT patient_code, MAX(patient_name) patient_name, COUNT(*) scan_count,
                            MAX(created_at) last_visit FROM scans WHERE patient_code IS NOT NULL
                            GROUP BY patient_code ORDER BY last_visit DESC""").fetchall()
    return render_template("patients.html", patients=rows)

@app.route("/patients/<path:patient_code>")
@login_required
def patient_details(patient_code):
    if session.get("role") == "patient" and patient_code != session.get("username"):
        flash("You can only view your own patient details.", "danger")
        return redirect(url_for("patient_dashboard"))
    with db() as conn:
        scans = conn.execute("SELECT * FROM scans WHERE patient_code=? ORDER BY id DESC LIMIT 250", (patient_code,)).fetchall()
    if not scans and session.get("role") != "patient":
        flash("No scan records found for that patient code yet.", "warning")
    return render_template("patient_details.html", patient_code=patient_code, scans=scans,
                           patient_name=next((x["patient_name"] for x in scans if x["patient_name"]), patient_code))

@app.route("/patient/dashboard")
@login_required
def patient_dashboard():
    if session.get("role") != "patient":
        return redirect(url_for("dashboard"))
    username = session.get("username", "")
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) n FROM scans WHERE patient_code=? OR patient_user_id=?", (username, session["user_id"])).fetchone()["n"]
        completed = conn.execute("SELECT COUNT(*) n FROM scans WHERE (patient_code=? OR patient_user_id=?) AND status='complete'", (username, session["user_id"])).fetchone()["n"]
        recent = conn.execute("SELECT * FROM scans WHERE patient_code=? OR patient_user_id=? ORDER BY id DESC LIMIT 8", (username, session["user_id"])).fetchall()
    return render_template("patient_dashboard.html", total=total, completed=completed, recent=recent)

@app.route("/patient/history")
@login_required
def patient_history():
    if session.get("role") != "patient":
        return redirect(url_for("patients"))
    username = session.get("username", "")
    with db() as conn:
        scans = conn.execute("SELECT * FROM scans WHERE patient_code=? OR patient_user_id=? ORDER BY id DESC LIMIT 250", (username, session["user_id"])).fetchall()
    return render_template("patient_history.html", scans=scans)

@app.route("/result/<int:scan_id>")
@login_required
def result(scan_id):
    import json
    with db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if not scan:
        flash("Scan not found.", "warning")
        return redirect(url_for("dashboard"))
    if not scan_access_allowed(scan):
        flash("You can only view your own scan results.", "danger")
        return redirect(url_for("patient_dashboard"))
    try:
        detections = json.loads(scan["detections_json"] or "[]")
    except Exception:
        detections = []
    return render_template("result.html", scan=scan, detections=detections)

@app.route("/records")
@login_required
def records():
    if session.get("role") == "patient":
        return redirect(url_for("patient_history"))
    q = request.args.get("q", "").strip()[:100]
    with db() as conn:
        if q:
            rows = conn.execute(
                """SELECT * FROM scans WHERE patient_code LIKE ? OR patient_name LIKE ?
                   OR predicted_class LIKE ? ORDER BY id DESC LIMIT 250""",
                (f"%{q}%", f"%{q}%", f"%{q}%")
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 250").fetchall()
    return render_template("records.html", scans=rows, q=q)

@app.route("/reports")
@login_required
def reports():
    if session.get("role") == "patient":
        return redirect(url_for("patient_history"))
    with db() as conn:
        scans = conn.execute("SELECT * FROM scans WHERE status='complete' ORDER BY id DESC LIMIT 250").fetchall()
    return render_template("reports.html", scans=scans)

@app.route("/report/<int:scan_id>/pdf")
@login_required
def report_pdf(scan_id):
    with db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    if not scan:
        flash("Scan not found.", "warning")
        return redirect(url_for("reports"))
    if not scan_access_allowed(scan):
        flash("You can only download your own report.", "danger")
        return redirect(url_for("patient_history"))
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from io import BytesIO
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(55, 745, "BoneAI - AI Prediction Report")
        c.setFont("Helvetica", 11)
        lines = [
            f"Scan ID: {scan['id']}",
            f"Patient code: {scan['patient_code'] or 'Not provided'}",
            f"Patient name: {scan['patient_name'] or 'Not provided'}",
            f"Date: {scan['created_at']}",
            f"Status: {scan['status']}",
            f"Model output: {scan['predicted_class'] or 'Not available'}",
            f"Confidence: {scan['confidence'] if scan['confidence'] is not None else 'Not available'}",
            "",
            "Important: This is an AI prediction from an educational prototype, not a diagnosis.",
            "A qualified medical professional must review the original image and findings."
        ]
        y = 710
        for line in lines:
            c.drawString(55, y, line[:105])
            y -= 22
        c.save()
        buf.seek(0)
        from flask import send_file
        return send_file(buf, as_attachment=True, download_name=f"boneai_report_{scan_id}.pdf", mimetype="application/pdf")
    except Exception as exc:
        flash(f"Could not create PDF report: {exc}", "danger")
        return redirect(url_for("reports"))

@app.route("/uploads/<path:filename>")
@login_required
def uploaded_image(filename):
    # image_name stores the Supabase object path, not a local filename.
    with db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE image_name=?", (filename,)).fetchone()
    if not scan:
        abort(404)
    if not scan_access_allowed(scan):
        abort(403)
    try:
        return redirect(create_xray_signed_url(scan["image_name"]), code=302)
    except Exception:
        app.logger.exception("Could not create private Supabase signed URL")
        abort(503)

@app.route("/results/<path:filename>")
@login_required
def result_image(filename):
    with db() as conn:
        scan = conn.execute("SELECT * FROM scans WHERE result_image=?", (filename,)).fetchone()
    if not scan:
        abort(404)
    if not scan_access_allowed(scan):
        abort(403)
    return send_from_directory(RESULT_DIR, filename)

@app.route("/assistant")
@login_required
def assistant():
    if session.get("role") == "patient":
        return redirect(url_for("patient_dashboard"))
    return render_template("assistant.html")

@app.route("/settings")
@login_required
def settings():
    if session.get("role") == "patient":
        return redirect(url_for("patient_dashboard"))
    return render_template("settings.html")

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "model_file_present": MODEL_PATH.is_file(), "supabase_configured": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SECRET_KEY"))})

@app.errorhandler(403)
def forbidden(_):
    return "You do not have permission to access this file.", 403

@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404

init_db()

if __name__ == "__main__":
    # Local development only; use a production WSGI server when deployed.
    app.run(host="127.0.0.1", port=5000, debug=False)
