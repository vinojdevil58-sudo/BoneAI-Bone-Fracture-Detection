
import os
import uuid
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(__file__).resolve().parent / ".env")

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_SECRET_KEY")
bucket = os.getenv("SUPABASE_BUCKET", "boneai-xrays")

if not url or not key:
    raise RuntimeError("Supabase credentials are missing from .env")

client = create_client(url.strip(), key.strip())

filename = f"connection-test-{uuid.uuid4().hex}.txt"
object_path = f"connection-tests/{filename}"

try:
    client.storage.from_(bucket).upload(
        path=object_path,
        file=b"BoneAI cloud storage test successful",
        file_options={
            "content-type": "text/plain",
            "upsert": "false",
        },
    )
    print("SUCCESS: File uploaded to Supabase!")
    print("Bucket:", bucket)
    print("Object path:", object_path)
except Exception as exc:
    print("UPLOAD FAILED:", exc)
