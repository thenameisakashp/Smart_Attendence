"""Create a lecturer account for Phase 1 testing. Run: python create_user.py"""
from getpass import getpass
from pathlib import Path
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash

DB_PATH = Path(__file__).resolve().parent / "dataset" / "dashboard_admin.db"

def main():
    if not DB_PATH.exists():
        print("Dashboard database not found. Run app.py and complete the HOD setup first.")
        return
    username = input("Lecturer username: ").strip()
    display_name = input("Lecturer full name: ").strip()
    password = getpass("Temporary password (12+ characters): ")
    if not username or not display_name or len(password) < 12:
        print("Username and name are required; password must be at least 12 characters.")
        return
    with sqlite3.connect(DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE role='hod'").fetchone()[0]
        if not count:
            print("Create the HOD account first at http://127.0.0.1:5000/setup.")
            return
        try:
            conn.execute("INSERT INTO users(username,password_hash,role,display_name,created_at) VALUES(?,?,?,?,?)",
                         (username, generate_password_hash(password), "lecturer", display_name, datetime.now().isoformat(timespec="seconds")))
        except sqlite3.IntegrityError:
            print("That username already exists.")
            return
    print(f"Lecturer account '{username}' created. Share credentials securely; do not commit them to Git.")

if __name__ == "__main__":
    main()
