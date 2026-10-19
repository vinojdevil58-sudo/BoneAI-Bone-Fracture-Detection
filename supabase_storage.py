
import os
import uuid

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "boneai-xrays")

if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
    raise RuntimeError(
        "Set SUPABASE_URL and SUPABASE_SECRET_KEY in your .env file."
    )

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)


def upload_xray(image_bytes, user_id, extension, content_type):
    """Upload an X-ray and return its private storage path."""

    allowed_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }

    extension = extension.lower()

    if extension not in allowed_types:
        raise ValueError("Only JPG, JPEG, and PNG images are allowed.")

    if content_type != allowed_types[extension]:
        raise ValueError("Image extension and content type do not match.")

    if not image_bytes:
        raise ValueError("The uploaded image is empty.")

    if len(image_bytes) > 10 * 1024 * 1024:
        raise ValueError("The image must be 10 MB or smaller.")

    # Use an authenticated user's internal ID, not a name supplied
    # by the browser. The caller must enforce authentication.
    if not user_id or "/" in str(user_id) or "\\" in str(user_id):
        raise ValueError("A valid authenticated user ID is required.")

    storage_path = f"{user_id}/{uuid.uuid4().hex}{extension}"

    supabase.storage.from_(SUPABASE_BUCKET).upload(
        storage_path,
        image_bytes,
        {
            "content-type": content_type,
            "upsert": "false",
        },
    )

    return storage_path
