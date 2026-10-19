
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


