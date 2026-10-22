
import os
import io
import json
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    send_from_directory,
    abort,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# ============================================================
# 1. CONFIGURATION
# ============================================================

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
MODEL_PATH = BASE_DIR / "models" / "best.pt"
DB_PATH = INSTANCE_DIR / "boneai.db"

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_FILE_SIZE = 12 * 1024 * 1024

INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

app.config["SECRET_KEY"] = os.getenv(
    "BONEAI_SECRET_KEY",
    "CHANGE-THIS-TO-A-LONG-RANDOM-SECRET"
)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

# Model and Supabase are loaded only when required.
_model = None
_model_error = None
_supabase = None
_supabase_error = None


# ============================================================
# 2. DATABASE
# ============================================================

def db():
    """Create a SQLite database connection."""
    connection = sqlite3.connect(str(DB_PATH))
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    """Create the required database tables."""
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'patient',
                patient_code TEXT,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                patient_user_id INTEGER,
                patient_code TEXT,
                patient_name TEXT,
                original_filename TEXT,
                storage_path TEXT,
                result_filename TEXT,
                prediction TEXT,
                confidence REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(patient_user_id) REFERENCES users(id)
            )
        """)

        # Add missing columns when upgrading an older database.
        user_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }

        if "patient_code" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN patient_code TEXT")

        scan_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(scans)").fetchall()
        }

        migrations = {
            "patient_user_id": "INTEGER",
            "patient_code": "TEXT",
            "patient_name": "TEXT",
            "original_filename": "TEXT",
            "storage_path": "TEXT",
            "result_filename": "TEXT",
            "prediction": "TEXT",
            "confidence": "REAL DEFAULT 0",
            "created_at": "TEXT",
        }

        for column, definition in migrations.items():
            if column not in scan_columns:
                conn.execute(
                    f"ALTER TABLE scans ADD COLUMN {column} {definition}"
                )

        conn.commit()


# ============================================================
# 3. OPTIONAL SUPABASE STORAGE
# ============================================================

def get_supabase():
    """Connect to Supabase if credentials are configured."""
    global _supabase, _supabase_error

    if _supabase is not None:
        return _supabase

    if _supabase_error:
        return None

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SECRET_KEY")

    if not supabase_url or not supabase_key:
        _supabase_error = "Supabase credentials are not configured."
        return None

    try:
        from supabase import create_client

        _supabase = create_client(supabase_url, supabase_key)
        return _supabase
    except Exception as exc:
        _supabase_error = str(exc)
        return None


def storage_bucket():
    return os.getenv("SUPABASE_STORAGE_BUCKET", "boneai-xrays")


def upload_private_xray(file_bytes, storage_path, content_type):
    """Upload an X-ray to a private Supabase Storage bucket."""
    client = get_supabase()

    if client is None:
        raise RuntimeError(
            "Supabase is not configured. Set SUPABASE_URL and "
            "SUPABASE_SECRET_KEY in your .env file."
        )

    client.storage.from_(storage_bucket()).upload(
        storage_path,
        file_bytes,
        {
            "content-type": content_type or "application/octet-stream",
            "upsert": "false",
        },
    )

    return storage_path


def create_xray_signed_url(storage_path):
    """Generate a temporary link to a private stored X-ray."""
    if not storage_path:
        return None

    client = get_supabase()

    if client is None:
        return None

    try:
        response = client.storage.from_(storage_bucket()).create_signed_url(
            storage_path,
            60 * 10,
        )

        if isinstance(response, dict):
            return (
                response.get("signedURL")
                or response.get("signedUrl")
                or response.get("signed_url")
            )

        return None
    except Exception:
        return None


# ============================================================
# 4. YOLO MODEL
# ============================================================

def get_model():
    """Load the trained YOLO model only once."""
    global _model, _model_error

    if _model is not None:
        return _model

    if _model_error:
        raise RuntimeError(_model_error)

    if not MODEL_PATH.exists():
        _model_error = (
            f"Trained model not found: {MODEL_PATH}. "
            "Place your trained best.pt file in the models folder."
        )
        raise RuntimeError(_model_error)

    try:
        from ultralytics import YOLO

        _model = YOLO(str(MODEL_PATH))
        return _model
    except Exception as exc:
        _model_error = f"Unable to load YOLO model: {exc}"
        raise RuntimeError(_model_error)


def run_prediction(image_path):
    """Run YOLO prediction and save the annotated result."""
    model = get_model()

    results = model.predict(
        source=str(image_path),
        conf=0.25,
        save=False,
        verbose=False,
    )

    if not results:
        raise RuntimeError("The model did not return a prediction.")

    result = results[0]
    boxes = result.boxes

    detections = []
    highest_confidence = 0.0

    if boxes is not None and len(boxes) > 0:
        for box in boxes:
            class_id = int(box.cls[0].item())
            confidence = float(box.conf[0].item())

            if isinstance(model.names, dict):
                class_name = model.names.get(class_id, str(class_id))
            else:
                class_name = model.names[class_id]

            detections.append({
                "class": str(class_name),
                "confidence": round(confidence * 100, 2),
            })

            highest_confidence = max(
                highest_confidence,
                confidence,
            )

        prediction_text = ", ".join(
            item["class"] for item in detections
        )
    else:
        prediction_text = "No fracture detected by the model"

    # Save the annotated prediction image.
    result_name = f"{uuid.uuid4().hex}.jpg"
    result_path = RESULT_DIR / result_name

    plotted_image = result.plot()

    import cv2

    saved = cv2.imwrite(str(result_path), plotted_image)

    if not saved:
        raise RuntimeError("Could not save the prediction image.")

    return {
        "prediction": prediction_text,
        "confidence": round(highest_confidence * 100, 2),
        "detections": detections,
        "result_filename": result_name,
    }


# ============================================================
# 5. AUTHENTICATION HELPERS
# ============================================================

def login_required(view_function):
    @wraps(view_function)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))

        return view_function(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    def decorator(view_function):
        @wraps(view_function)
        @login_required
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                abort(403)

            return view_function(*args, **kwargs)

        return wrapped

    return decorator


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def scan_access_allowed(scan):
    """Check whether the signed-in user may access a scan."""
    role = session.get("role")
    user_id = session.get("user_id")

    if role in {"admin", "doctor"}:
        return True

    if scan["user_id"] == user_id:
        return True

    if scan["patient_user_id"] == user_id:
        return True

    return False


def get_scan_or_404(scan_id):
    with db() as conn:
        scan = conn.execute(
            "SELECT * FROM scans WHERE id = ?",
            (scan_id,),
        ).fetchone()

    if scan is None:
        abort(404)

    if not scan_access_allowed(scan):
        abort(403)

    return scan


# ============================================================
# 6. ERROR HANDLERS
# ============================================================

@app.errorhandler(413)
def file_too_large(error):
    flash("The uploaded file is too large. Maximum size is 12 MB.", "danger")
    return redirect(url_for("detect"))


@app.errorhandler(403)
def forbidden(error):
    return render_template(
        "error.html",
        code=403,
        message="You do not have permission to access this page.",
    ), 403


@app.errorhandler(404)
def not_found(error):
    return render_template(
        "error.html",
        code=404,
        message="The requested page or record was not found.",
    ), 404


# ============================================================
# 7. HOME
# ============================================================

@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    return render_template("index.html")


# ============================================================
# 8. LOGIN
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (email,),
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["email"] = user["email"]
            session["role"] = user["role"]
            session["patient_code"] = user["patient_code"]

            flash("Login successful.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "danger")

    return render_template("login.html")


# ============================================================
# 9. REGISTER
# ============================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please fill in all required fields.", "danger")
            return render_template("register.html")

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "danger")
            return render_template("register.html")

        password_hash = generate_password_hash(password)
        patient_code = f"PAT-{uuid.uuid4().hex[:8].upper()}"

        try:
            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO users
                    (name, email, password_hash, role, patient_code, created_at)
                    VALUES (?, ?, ?, 'patient', ?, ?)
                    """,
                    (
                        name,
                        email,
                        password_hash,
                        patient_code,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                conn.commit()

            flash("Account created. You can now sign in.", "success")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            flash("An account with this email already exists.", "danger")

    return render_template("register.html")


# ============================================================
# 10. LOGOUT
# ============================================================

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("index"))


# ============================================================
# 11. DASHBOARD
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    role = session.get("role")

    with db() as conn:
        if role in {"admin", "doctor"}:
            total_scans = conn.execute(
                "SELECT COUNT(*) FROM scans"
            ).fetchone()[0]

            recent_scans = conn.execute(
                """
                SELECT * FROM scans
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()

            total_patients = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'patient'"
            ).fetchone()[0]
        else:
            total_scans = conn.execute(
                """
                SELECT COUNT(*) FROM scans
                WHERE user_id = ? OR patient_user_id = ?
                """,
                (user_id, user_id),
            ).fetchone()[0]

            recent_scans = conn.execute(
                """
                SELECT * FROM scans
                WHERE user_id = ? OR patient_user_id = ?
                ORDER BY id DESC
                LIMIT 10
                """,
                (user_id, user_id),
            ).fetchall()

            total_patients = 1

    return render_template(
        "dashboard.html",
        total_scans=total_scans,
        recent_scans=recent_scans,
        total_patients=total_patients,
    )


# ============================================================
# 12. X-RAY DETECTION
# ============================================================

@app.route("/detect", methods=["GET", "POST"])
@login_required
def detect():

    if request.method == "GET":
        return render_template("detect.html")

    # Validate the uploaded file.
    uploaded = request.files.get("xray")

    if uploaded is None or not uploaded.filename:
        flash("Please select an X-ray image.", "danger")
        return redirect(url_for("detect"))

    if not allowed_file(uploaded.filename):
        flash(
            "Unsupported file type. Use PNG, JPG, JPEG, or WEBP.",
            "danger",
        )
        return redirect(url_for("detect"))

    file_bytes = uploaded.read()

    if not file_bytes:
        flash("The selected file is empty.", "danger")
        return redirect(url_for("detect"))

    if len(file_bytes) > MAX_FILE_SIZE:
        flash("The image must be smaller than 12 MB.", "danger")
        return redirect(url_for("detect"))

    # --------------------------------------------------------
    # PATIENT INFORMATION
    # This entire section must remain inside detect().
    # --------------------------------------------------------

    patient_code = request.form.get(
        "patient_code", ""
    ).strip()[:80]

    patient_name = request.form.get(
        "patient_name", ""
    ).strip()[:120]

    patient_user_id = None

    # Patients can only submit scans for their own account.
    if session.get("role") == "patient":
        with db() as conn:
            patient_user = conn.execute(
                "SELECT id, name, patient_code FROM users WHERE id = ?",
                (session["user_id"],),
            ).fetchone()

        if patient_user is None:
            session.clear()
            flash("Your account could not be found. Please sign in again.", "danger")
            return redirect(url_for("login"))

        patient_user_id = patient_user["id"]
        patient_code = patient_user["patient_code"] or ""
        patient_name = patient_user["name"]

    else:
        # Doctor/admin may associate a scan with an existing patient.
        if patient_code:
            with db() as conn:
                patient_user = conn.execute(
                    """
                    SELECT id, name, patient_code
                    FROM users
                    WHERE patient_code = ? AND role = 'patient'
                    """,
                    (patient_code,),
                ).fetchone()

            if patient_user:
                patient_user_id = patient_user["id"]

                if not patient_name:
                    patient_name = patient_user["name"]

        if not patient_name:
            patient_name = "Unspecified patient"

    # --------------------------------------------------------
    # SAVE TEMPORARY IMAGE FOR MODEL PREDICTION
    # --------------------------------------------------------

    safe_filename = secure_filename(uploaded.filename)
    file_extension = Path(safe_filename).suffix.lower()

    if file_extension not in {".png", ".jpg", ".jpeg", ".webp"}:
        flash("Invalid image extension.", "danger")
        return redirect(url_for("detect"))

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=file_extension,
            delete=False,
        ) as temporary_file:
            temporary_file.write(file_bytes)
            temporary_path = Path(temporary_file.name)

        # ----------------------------------------------------
        # RUN YOLO PREDICTION
        # ----------------------------------------------------

        prediction_result = run_prediction(temporary_path)

        # ----------------------------------------------------
        # UPLOAD ORIGINAL X-RAY TO PRIVATE SUPABASE STORAGE
        # ----------------------------------------------------

        storage_path = (
            f"{session['user_id']}/"
            f"{uuid.uuid4().hex}{file_extension}"
        )

        content_type = uploaded.mimetype or "application/octet-stream"

        upload_private_xray(
            file_bytes,
            storage_path,
            content_type,
        )

        # ----------------------------------------------------
        # SAVE SCAN RECORD
        # ----------------------------------------------------

        created_at = datetime.now().isoformat(timespec="seconds")

        with db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scans (
                    user_id,
                    patient_user_id,
                    patient_code,
                    patient_name,
                    original_filename,
                    storage_path,
                    result_filename,
                    prediction,
                    confidence,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["user_id"],
                    patient_user_id,
                    patient_code,
                    patient_name,
                    safe_filename,
                    storage_path,
                    prediction_result["result_filename"],
                    prediction_result["prediction"],
                    prediction_result["confidence"],
                    created_at,
                ),
            )

            scan_id = cursor.lastrowid
            conn.commit()

        flash("X-ray analysis completed.", "success")
        return redirect(url_for("result", scan_id=scan_id))

    except Exception as exc:
        app.logger.exception("X-ray detection failed")

        flash(
            "Detection failed. Check your model file, Supabase settings, "
            f"and server terminal. Details: {exc}",
            "danger",
        )

        return redirect(url_for("detect"))

    finally:
        if temporary_path and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                app.logger.warning(
                    "Could not remove temporary image: %s",
                    temporary_path,
                )


# ============================================================
# 13. PATIENT LIST
# ============================================================

@app.route("/patients")
@roles_required("doctor", "admin")
def patients():
    with db() as conn:
        patient_list = conn.execute(
            """
            SELECT
                u.id,
                u.name,
                u.email,
                u.patient_code,
                u.created_at,
                COUNT(s.id) AS scan_count
            FROM users u
            LEFT JOIN scans s ON s.patient_user_id = u.id
            WHERE u.role = 'patient'
            GROUP BY u.id
            ORDER BY u.id DESC
            """
        ).fetchall()

    return render_template("patients.html", patients=patient_list)


# ============================================================
# 14. PATIENT DETAILS
# ============================================================

@app.route("/patients/<path:patient_code>")
@login_required
def patient_details(patient_code):
    role = session.get("role")

    with db() as conn:
        if role in {"doctor", "admin"}:
            patient = conn.execute(
                """
                SELECT * FROM users
                WHERE patient_code = ? AND role = 'patient'
                """,
                (patient_code,),
            ).fetchone()
        else:
            patient = conn.execute(
                """
                SELECT * FROM users
                WHERE id = ? AND patient_code = ? AND role = 'patient'
                """,
                (session["user_id"], patient_code),
            ).fetchone()

        if patient is None:
            abort(404)

        patient_scans = conn.execute(
            """
            SELECT * FROM scans
            WHERE patient_user_id = ?
            ORDER BY id DESC
            """,
            (patient["id"],),
        ).fetchall()

    return render_template(
        "patient_details.html",
        patient=patient,
        scans=patient_scans,
    )


# ============================================================
# 15. PATIENT DASHBOARD
# ============================================================

@app.route("/patient/dashboard")
@login_required
def patient_dashboard():
    if session.get("role") != "patient":
        return redirect(url_for("dashboard"))

    return redirect(url_for("dashboard"))


# ============================================================
# 16. PATIENT HISTORY
# ============================================================

@app.route("/patient/history")
@login_required
def patient_history():
    user_id = session["user_id"]

    with db() as conn:
        scans = conn.execute(
            """
            SELECT * FROM scans
            WHERE user_id = ? OR patient_user_id = ?
            ORDER BY id DESC
            """,
            (user_id, user_id),
        ).fetchall()

    return render_template("history.html", scans=scans)


# ============================================================
# 17. SINGLE SCAN RESULT
# ============================================================

@app.route("/result/<int:scan_id>")
@login_required
def result(scan_id):
    scan = get_scan_or_404(scan_id)
    signed_url = create_xray_signed_url(scan["storage_path"])

    return render_template(
        "result.html",
        scan=scan,
        signed_url=signed_url,
    )


# ============================================================
# 18. ALL RECORDS
# ============================================================

@app.route("/records")
@roles_required("doctor", "admin")
def records():
    with db() as conn:
        scans = conn.execute(
            """
            SELECT * FROM scans
            ORDER BY id DESC
            """
        ).fetchall()

    return render_template("records.html", scans=scans)


# ============================================================
# 19. REPORTS
# ============================================================

@app.route("/reports")
@login_required
def reports():
    role = session.get("role")
    user_id = session["user_id"]

    with db() as conn:
        if role in {"doctor", "admin"}:
            scans = conn.execute(
                "SELECT * FROM scans ORDER BY id DESC"
            ).fetchall()
        else:
            scans = conn.execute(
                """
                SELECT * FROM scans
                WHERE user_id = ? OR patient_user_id = ?
                ORDER BY id DESC
                """,
                (user_id, user_id),
            ).fetchall()

    return render_template("reports.html", scans=scans)


# ============================================================
# 20. PDF REPORT
# ============================================================

@app.route("/report/<int:scan_id>/pdf")
@login_required
def report_pdf(scan_id):
    scan = get_scan_or_404(scan_id)

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
        )
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        flash(
            "PDF report support is unavailable. Install reportlab first.",
            "danger",
        )
        return redirect(url_for("result", scan_id=scan_id))

    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4)
    styles = getSampleStyleSheet()

    story = [
        Paragraph("BoneAI - X-ray Analysis Report", styles["Title"]),
        Spacer(1, 18),
        Paragraph(f"Scan ID: {scan['id']}", styles["Normal"]),
        Paragraph(
            f"Patient: {scan['patient_name'] or 'Not specified'}",
            styles["Normal"],
        ),
        Paragraph(
            f"Patient Code: {scan['patient_code'] or 'Not specified'}",
            styles["Normal"],
        ),
        Paragraph(
            f"Original File: {scan['original_filename'] or 'Not available'}",
            styles["Normal"],
        ),
        Paragraph(
            f"Analysis Date: {scan['created_at'] or 'Not available'}",
            styles["Normal"],
        ),
        Spacer(1, 12),
        Paragraph("Model Output", styles["Heading2"]),
        Paragraph(
            f"Prediction: {scan['prediction'] or 'Not available'}",
            styles["Normal"],
        ),
        Paragraph(
            f"Confidence: {scan['confidence'] or 0}%",
            styles["Normal"],
        ),
        Spacer(1, 18),
        Paragraph(
            "Important: This report contains an AI-generated screening "
            "result only. It is not a confirmed medical diagnosis. "
            "A qualified medical professional must review the X-ray.",
            styles["BodyText"],
        ),
    ]

    document.build(story)
    output.seek(0)

    from flask import send_file

    return send_file(
        output,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"BoneAI_Report_{scan_id}.pdf",
    )


# ============================================================
# 21. LOCAL UPLOAD / RESULT FILES
# ============================================================

@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    # Original X-rays are stored in private Supabase Storage.
    # This route serves only files explicitly present in local uploads.
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/results/<path:filename>")
@login_required
def result_file(filename):
    # Only allow result images that belong to a saved scan the user can access.
    with db() as conn:
        scan = conn.execute(
            """
            SELECT * FROM scans
            WHERE result_filename = ?
            """,
            (filename,),
        ).fetchone()

    if scan is None:
        abort(404)

    if not scan_access_allowed(scan):
        abort(403)

    return send_from_directory(RESULT_DIR, filename)


# ============================================================
# 22. ASSISTANT PAGE
# ============================================================

@app.route("/assistant", methods=["GET", "POST"])
@login_required
def assistant():
    answer = None

    if request.method == "POST":
        question = request.form.get("question", "").strip()

        if question:
            answer = (
                "BoneAI can provide AI-assisted X-ray screening results. "
                "Upload an X-ray through the Detection page to run the "
                "configured model. For diagnosis or treatment decisions, "
                "consult a qualified medical professional."
            )

    return render_template("assistant.html", answer=answer)


# ============================================================
# 23. SETTINGS
# ============================================================

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        name = request.form.get("name", "").strip()

        if not name:
            flash("Name cannot be empty.", "danger")
            return redirect(url_for("settings"))

        with db() as conn:
            conn.execute(
                "UPDATE users SET name = ? WHERE id = ?",
                (name, session["user_id"]),
            )
            conn.commit()

        session["name"] = name
        flash("Settings updated.", "success")
        return redirect(url_for("settings"))

    with db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (session["user_id"],),
        ).fetchone()

    return render_template("settings.html", user=user)


# ============================================================
# 24. HEALTH CHECK
# ============================================================

@app.route("/api/health")
def health():
    model_status = "not_loaded"

    if _model is not None:
        model_status = "loaded"
    elif _model_error:
        model_status = "error"

    supabase_status = "configured" if get_supabase() else "not_configured"

    return jsonify({
        "status": "ok",
        "application": "BoneAI",
        "model_status": model_status,
        "model_file_exists": MODEL_PATH.exists(),
        "supabase_status": supabase_status,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    })


# ============================================================
# 25. START APPLICATION
# ============================================================

init_db()

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )
