import os
import io
import json
import sqlite3
import tempfile
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    session, jsonify, send_from_directory, abort, send_file
)
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
SIGNED_URL_SECONDS = 60

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("BONEAI_SECRET_KEY", "")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

if not app.config["SECRET_KEY"]:
    # Set BONEAI_SECRET_KEY in Render environment variables for deployment.
    app.logger.warning("BONEAI_SECRET_KEY is not set; set a strong secret in deployment settings.")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_model = None
_model_error = None
_supabase = None
_supabase_error = None


def db():
    """Open the configured SQLite database with foreign-key enforcement."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create required tables if absent and add supported legacy columns."""
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
        scan_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(scans)").fetchall()
        }
        migrations = {
            "patient_user_id": "INTEGER",
            "patient_code": "TEXT",
            "patient_name": "TEXT",
            "result_image": "TEXT",
            "predicted_class": "TEXT",
            "confidence": "REAL",
            "detections_json": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'uploaded'",
            "uploaded_by": "INTEGER",
            "created_at": "TEXT",
            "image_name": "TEXT",
        }
        for column, sql_type in migrations.items():
            if column not in scan_columns:
                conn.execute(f"ALTER TABLE scans ADD COLUMN {column} {sql_type}")


def get_supabase():
    """Initialize Supabase lazily from server-side environment variables."""
    global _supabase, _supabase_error
    if _supabase is not None:
        return _supabase
    if _supabase_error:
        raise RuntimeError(_supabase_error)

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    if not url or not key:
        raise RuntimeError(
            "Set SUPABASE_URL and SUPABASE_SECRET_KEY in your environment."
        )
    try:
        from supabase import create_client
        _supabase = create_client(url, key)
        return _supabase
    except Exception as exc:
        _supabase_error = f"Could not initialize Supabase: {exc}"
        raise RuntimeError(_supabase_error) from exc


def storage_bucket():
    return os.getenv("SUPABASE_BUCKET", "boneai-xrays").strip() or "boneai-xrays"


def upload_private_xray(image_bytes, user_id, extension, content_type):
    """Upload an X-ray into a private Supabase Storage bucket."""
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    extension = extension.lower()
    expected_type = mime_types.get(extension)
    if not expected_type or content_type != expected_type:
        raise ValueError("Unsupported image format.")
    if not image_bytes or len(image_bytes) > MAX_CONTENT_LENGTH:
        raise ValueError("Image is empty or larger than 12 MB.")

    object_path = f"user-{int(user_id)}/{uuid.uuid4().hex}{extension}"
    get_supabase().storage.from_(storage_bucket()).upload(
        path=object_path,
        file=image_bytes,
        file_options={"content-type": expected_type, "upsert": "false"},
    )
    return object_path


def create_xray_signed_url(object_path):
    """Return a short-lived signed URL for an object in the private bucket."""
    if not object_path or object_path.startswith(("http://", "https://")):
        raise ValueError("Invalid private storage path.")
    response = get_supabase().storage.from_(storage_bucket()).create_signed_url(
        object_path, SIGNED_URL_SECONDS
    )
    if isinstance(response, dict):
        signed_url = response.get("signedURL") or response.get("signedUrl")
        if signed_url:
            return signed_url
    raise RuntimeError("Supabase did not return a signed URL.")


def get_model():
    """Load the YOLO model only when a scan is submitted."""
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
        raise RuntimeError(_model_error) from exc


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in to open this page.", "warning")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def scan_access_allowed(scan):
    """Patients can only access scans linked to their own account."""
    if not scan:
        return False
    if session.get("role") != "patient":
        return True
    return (
        scan["patient_user_id"] == session.get("user_id")
        or scan["patient_code"] == session.get("username")
    )


@app.errorhandler(413)
def too_large(_error):
    flash("Image is too large. Maximum upload size is 12 MB.", "danger")
    if "user_id" in session:
        return redirect(url_for("detect"))
    return "Image too large (12 MB maximum).", 413


@app.errorhandler(403)
def forbidden(_error):
    return "You do not have permission to access this file.", 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("404.html"), 404


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()[:80]
        password = request.form.get("password", "")
        with db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()

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
            flash(
                "Use a username of at least 3 characters and a password of at least 8 characters.",
                "warning",
            )
        elif password != confirm:
            flash("Passwords do not match.", "warning")
        else:
            try:
                with db() as conn:
                    conn.execute(
                        """INSERT INTO users(username, password_hash, role, created_at)
                           VALUES (?, ?, ?, ?)""",
                        (
                            username,
                            generate_password_hash(password),
                            "patient",
                            datetime.now().isoformat(timespec="seconds"),
                        ),
                    )
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
        total = conn.execute("SELECT COUNT(*) AS n FROM scans").fetchone()["n"]
        completed = conn.execute(
            "SELECT COUNT(*) AS n FROM scans WHERE status='complete'"
        ).fetchone()["n"]
        recent = conn.execute(
            "SELECT * FROM scans ORDER BY id DESC LIMIT 8"
        ).fetchall()
        fracture_count = conn.execute(
            """SELECT COUNT(*) AS n FROM scans
               WHERE status='complete'
               AND lower(COALESCE(predicted_class, '')) LIKE '%fracture%'"""
        ).fetchone()["n"]

    return render_template(
        "dashboard.html",
        total=total,
        completed=completed,
        fracture_count=fracture_count,
        recent=recent,
        model_ready=MODEL_PATH.is_file(),
        now_date=datetime.now().strftime("%d %b %Y"),
    )


@app.route("/detect", methods=["GET", "POST"])
@login_required
def detect():
    if request.method != "POST":
        return render_template(
            "detect.html",
            model_ready=MODEL_PATH.is_file(),
            is_patient=session.get("role") == "patient",
        )

    uploaded = request.files.get("xray")
    if not uploaded or not uploaded.filename:
        flash("Choose an X-ray image first.", "warning")
        return redirect(url_for("detect"))
    if not allowed_file(uploaded.filename):
        flash("Supported formats: PNG, JPG, JPEG, WEBP.", "danger")
        return redirect(url_for("detect"))

    original = secure_filename(uploaded.filename)
    extension = Path(original).suffix.lower()
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    if extension not in mime_types:
        flash("Unsupported image format.", "danger")
        return redirect(url_for("detect"))

    image_bytes = uploaded.read()
    if not image_bytes or len(image_bytes) > MAX_CONTENT_LENGTH:
        flash("Image is empty or larger than 12 MB.", "danger")
        return redirect(url_for("detect"))

    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Unsupported image contents")
            image.verify()
    except Exception:
        flash("The uploaded file is not a valid JPG, PNG, or WEBP image.", "danger")
        return redirect(url_for("detect"))

    patient_code = request.form.get("patient_code", "").strip()[:80]
    patient_name = request.form.get("patient_name", "").strip()[:120]
    patient_user_id = None

    # Use the authenticated database ID directly for patient uploads.
    # This avoids searching by a form field that might contain an email or different value.
    
if session.get("role") == "patient":
    with db() as conn:
        # First verify the patient using the logged-in username.
        patient_user = conn.execute(
            """
            SELECT id, username
            FROM users
            WHERE username = ? AND role = 'patient'
            """,
            (session.get("username"),),
        ).fetchone()

    if not patient_user:
        app.logger.error(
            "Patient account not found for username: %s",
            session.get("username"),
        )
        session.clear()
        flash(
            "Patient account not found. Please register or sign in again.",
            "danger",
        )
        return redirect(url_for("login"))

    # Synchronize the session with the verified database account.
    patient_user_id = patient_user["id"]
    session["user_id"] = patient_user_id
    patient_code = patient_user["username"]
    patient_name = patient_name or patient_user["username"]


    # Upload to private storage only after the session's patient identity is checked.
    try:
        storage_path = upload_private_xray(
            image_bytes, session["user_id"], extension, mime_types[extension]
        )
    except Exception:
        app.logger.exception("Private Supabase upload failed")
        flash(
            "Could not upload to private Supabase Storage. Check Render environment variables, bucket name, and Supabase key.",
            "danger",
        )
        return redirect(url_for("detect"))

    try:
        with db() as conn:
            # For doctor/admin uploads, patient_user_id is intentionally left NULL
            # unless the form is later extended with a validated patient selector.
            cur = conn.execute(
                """INSERT INTO scans(
                       patient_user_id, patient_code, patient_name, image_name,
                       status, uploaded_by, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    patient_user_id,
                    patient_code or None,
                    patient_name or None,
                    storage_path,
                    "uploaded",
                    session.get("user_id"),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            scan_id = cur.lastrowid
    except sqlite3.IntegrityError:
        app.logger.exception("Database rejected scan record")
        flash(
            "The scan could not be saved because of a database relationship error. No scan was created.",
            "danger",
        )
        return redirect(url_for("detect"))

    temp_path = None
    try:
        from PIL import Image
        model = get_model()

        with tempfile.NamedTemporaryFile(
            suffix=extension, dir=UPLOAD_DIR, delete=False
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(image_bytes)

        results = model.predict(source=str(temp_path), verbose=False)
        result = results[0]
        detections = []

        for box in result.boxes:
            class_id = int(box.cls[0].item())
            confidence_value = float(box.conf[0].item())
            bbox = [round(float(value), 2) for value in box.xyxy[0].tolist()]
            detections.append(
                {
                    "class_name": str(result.names.get(class_id, class_id)),
                    "confidence": round(confidence_value, 4),
                    "bbox": bbox,
                }
            )

        if detections:
            best = max(detections, key=lambda item: item["confidence"])
            predicted_class = best["class_name"]
            confidence_value = best["confidence"]
        else:
            predicted_class = "No objects detected"
            confidence_value = None

        result_name = f"scan_{scan_id}.jpg"
        plotted = result.plot()
        Image.fromarray(plotted[:, :, ::-1]).save(
            RESULT_DIR / result_name, format="JPEG", quality=92
        )

        with db() as conn:
            conn.execute(
                """UPDATE scans
                   SET result_image=?, predicted_class=?, confidence=?,
                       detections_json=?, status='complete'
                   WHERE id=?""",
                (
                    result_name,
                    predicted_class,
                    confidence_value,
                    json.dumps(detections),
                    scan_id,
                ),
            )

        flash(
            "Analysis finished. This is an AI prediction, not a medical diagnosis.",
            "success",
        )
    except Exception:
        app.logger.exception("YOLO analysis failed")
        with db() as conn:
            conn.execute("UPDATE scans SET status='error' WHERE id=?", (scan_id,))
        flash(
            "The X-ray was stored, but analysis failed. Check models/best.pt and the Python dependencies in Render logs.",
            "danger",
        )
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                app.logger.warning("Could not remove temporary X-ray file")

    return redirect(url_for("result", scan_id=scan_id))


@app.route("/patients")
@login_required
def patients():
    if session.get("role") == "patient":
        return redirect(url_for("patient_history"))
    with db() as conn:
        rows = conn.execute(
            """SELECT patient_code, MAX(patient_name) AS patient_name,
                      COUNT(*) AS scan_count, MAX(created_at) AS last_visit
               FROM scans
               WHERE patient_code IS NOT NULL
               GROUP BY patient_code
               ORDER BY last_visit DESC"""
        ).fetchall()
    return render_template("patients.html", patients=rows)


@app.route("/patients/<path:patient_code>")
@login_required
def patient_details(patient_code):
    if (
        session.get("role") == "patient"
        and patient_code != session.get("username")
    ):
        flash("You can only view your own patient details.", "danger")
        return redirect(url_for("patient_dashboard"))

    with db() as conn:
        scans = conn.execute(
            "SELECT * FROM scans WHERE patient_code=? ORDER BY id DESC LIMIT 250",
            (patient_code,),
        ).fetchall()

    if not scans and session.get("role") != "patient":
        flash("No scan records found for that patient code yet.", "warning")

    patient_name = next(
        (scan["patient_name"] for scan in scans if scan["patient_name"]),
        patient_code,
    )
    return render_template(
        "patient_details.html",
        patient_code=patient_code,
        scans=scans,
        patient_name=patient_name,
    )


@app.route("/patient/dashboard")
@login_required
def patient_dashboard():
    if session.get("role") != "patient":
        return redirect(url_for("dashboard"))

    username = session.get("username", "")
    user_id = session["user_id"]
    with db() as conn:
        total = conn.execute(
            """SELECT COUNT(*) AS n FROM scans
               WHERE patient_code=? OR patient_user_id=?""",
            (username, user_id),
        ).fetchone()["n"]
        completed = conn.execute(
            """SELECT COUNT(*) AS n FROM scans
               WHERE (patient_code=? OR patient_user_id=?) AND status='complete'""",
            (username, user_id),
        ).fetchone()["n"]
        recent = conn.execute(
            """SELECT * FROM scans
               WHERE patient_code=? OR patient_user_id=?
               ORDER BY id DESC LIMIT 8""",
            (username, user_id),
        ).fetchall()

    return render_template(
        "patient_dashboard.html", total=total, completed=completed, recent=recent
    )


@app.route("/patient/history")
@login_required
def patient_history():
    if session.get("role") != "patient":
        return redirect(url_for("patients"))

    username = session.get("username", "")
    user_id = session["user_id"]
    with db() as conn:
        scans = conn.execute(
            """SELECT * FROM scans
               WHERE patient_code=? OR patient_user_id=?
               ORDER BY id DESC LIMIT 250""",
            (username, user_id),
        ).fetchall()

    return render_template("patient_history.html", scans=scans)


@app.route("/result/<int:scan_id>")
@login_required
def result(scan_id):
    with db() as conn:
        scan = conn.execute(
            "SELECT * FROM scans WHERE id=?", (scan_id,)
        ).fetchone()

    if not scan:
        flash("Scan not found.", "warning")
        return redirect(url_for("dashboard"))
    if not scan_access_allowed(scan):
        flash("You can only view your own scan results.", "danger")
        return redirect(url_for("patient_dashboard"))

    try:
        detections = json.loads(scan["detections_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        detections = []

    return render_template("result.html", scan=scan, detections=detections)


@app.route("/records")
@login_required
def records():
    if session.get("role") == "patient":
        return redirect(url_for("patient_history"))

    query = request.args.get("q", "").strip()[:100]
    with db() as conn:
        if query:
            like = f"%{query}%"
            rows = conn.execute(
                """SELECT * FROM scans
                   WHERE patient_code LIKE ? OR patient_name LIKE ?
                      OR predicted_class LIKE ?
                   ORDER BY id DESC LIMIT 250""",
                (like, like, like),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scans ORDER BY id DESC LIMIT 250"
            ).fetchall()

    return render_template("records.html", scans=rows, q=query)


@app.route("/reports")
@login_required
def reports():
    if session.get("role") == "patient":
        return redirect(url_for("patient_history"))
    with db() as conn:
        scans = conn.execute(
            "SELECT * FROM scans WHERE status='complete' ORDER BY id DESC LIMIT 250"
        ).fetchall()
    return render_template("reports.html", scans=scans)


@app.route("/report/<int:scan_id>/pdf")
@login_required
def report_pdf(scan_id):
    with db() as conn:
        scan = conn.execute(
            "SELECT * FROM scans WHERE id=?", (scan_id,)
        ).fetchone()

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

        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(55, 745, "BoneAI - AI Prediction Report")
        pdf.setFont("Helvetica", 11)
        lines = [
            f"Scan ID: {scan['id']}",
            f"Patient code: {scan['patient_code'] or 'Not provided'}",
            f"Patient name: {scan['patient_name'] or 'Not provided'}",
            f"Date: {scan['created_at']}",
            f"Status: {scan['status']}",
            f"Model output: {scan['predicted_class'] or 'Not available'}",
            (
                "Confidence: "
                + (
                    str(scan["confidence"])
                    if scan["confidence"] is not None
                    else "Not available"
                )
            ),
            "",
            "Important: This is an AI prediction from an educational prototype, not a diagnosis.",
            "A qualified medical professional must review the original image and findings.",
        ]
        y = 710
        for line in lines:
            pdf.drawString(55, y, line[:105])
            y -= 22
        pdf.save()
        buffer.seek(0)
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"boneai_report_{scan_id}.pdf",
            mimetype="application/pdf",
        )
    except Exception:
        app.logger.exception("Could not create PDF report")
        flash("Could not create the PDF report. Check the Render logs.", "danger")
        return redirect(url_for("reports"))


@app.route("/uploads/<path:filename>")
@login_required
def uploaded_image(filename):
    # image_name contains the Supabase object path, not a local filename.
    with db() as conn:
        scan = conn.execute(
            "SELECT * FROM scans WHERE image_name=?", (filename,)
        ).fetchone()

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
        scan = conn.execute(
            "SELECT * FROM scans WHERE result_image=?", (filename,)
        ).fetchone()

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
    return jsonify(
        {
            "status": "ok",
            "model_file_present": MODEL_PATH.is_file(),
            "supabase_configured": bool(
                os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SECRET_KEY")
            ),
        }
    )


# Initialize the schema on app startup.
init_db()


if __name__ == "__main__":
    # Local development only; use Gunicorn on Render.
    app.run(host="127.0.0.1", port=5000, debug=False)
