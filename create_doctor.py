"""Create a doctor/admin account for the local BoneAI website."""
import getpass
import sqlite3
from datetime import datetime
from pathlib import Path
from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "boneai.db"

def main():
    DB_PATH.parent.mkdir(exist_ok=True)
    # Ensure the application schema exists, without creating a demo user.
    from app import init_db
    init_db()
    username = input("Choose doctor username: ").strip()[:80]
    if len(username) < 3:
        print("Username must contain at least 3 characters.")
        return
    password = getpass.getpass("Choose password (at least 8 characters): ")
    confirm = getpass.getpass("Confirm password: ")
    if len(password) < 8:
        print("Password must contain at least 8 characters.")
        return
    if password != confirm:
        print("Passwords do not match.")
        return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, role, created_at) VALUES(?,?,?,?)",
                (username, generate_password_hash(password), "admin", datetime.now().isoformat(timespec="seconds"))
            )
        print("Doctor account created. You can now sign in to BoneAI.")
    except sqlite3.IntegrityError:
        print("That username already exists. Choose another username.")

if __name__ == "__main__":
    main()
