"""Reset an existing BoneAI account password from the local terminal.

Run from the project folder: python reset_doctor_password.py
This uses instance/boneai.db, matching the supplied BoneAI app.py.
"""
import getpass
import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "boneai.db"

if not DB_PATH.is_file():
    raise SystemExit(f"Database not found: {DB_PATH}\nCheck DB_PATH in app.py and update this script if needed.")

with sqlite3.connect(DB_PATH) as conn:
    conn.row_factory = sqlite3.Row
    users = conn.execute("SELECT id, username, role FROM users ORDER BY username").fetchall()
    if not users:
        raise SystemExit("No user accounts found in the database.")
    print("Accounts (no passwords are displayed):")
    for user in users:
        print(f"- {user['username']} (role: {user['role']})")
    username = input("Enter the exact doctor username to reset: ").strip()
    user = conn.execute(
        "SELECT id, username, role FROM users WHERE username = ?", (username,)
    ).fetchone()
    if not user:
        raise SystemExit("That username was not found.")
    if user["role"] == "patient":
        raise SystemExit("That account is marked as a patient, not a doctor/admin account.")
    password = getpass.getpass("New password (minimum 8 characters): ")
    confirm = getpass.getpass("Confirm new password: ")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters long.")
    if password != confirm:
        raise SystemExit("Passwords do not match. No changes were made.")
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(password), user["id"]),
    )
    conn.commit()
    print(f"Password reset successfully for {user['username']}. You can now sign in.")
