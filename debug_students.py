import sqlite3
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DB_FILE = BASE_DIR / "dataset" / "smart_attendance.db"
FEATURES_FILE = BASE_DIR / "dataset" / "face_features.json"

print("\n========== DATABASE CHECK ==========")

if DB_FILE.exists():
    connection = sqlite3.connect(str(DB_FILE))
    cursor = connection.cursor()

    cursor.execute("""
        SELECT roll_number, full_name, is_active
        FROM students
    """)

    database_students = cursor.fetchall()

    for roll, name, active in database_students:
        print(
            f"Roll: {roll} | Name: {name} | "
            f"Active: {active}"
        )

    connection.close()
else:
    print("Database file not found:", DB_FILE)
    database_students = []

print("\n========== FACE FEATURES CHECK ==========")

if FEATURES_FILE.exists():
    with open(FEATURES_FILE, "r", encoding="utf-8") as file:
        features = json.load(file)

    for roll, student in features.items():
        print(f"Roll: {roll} | Name: {student.get('name')}")

    print(f"\nTotal registered face features: {len(features)}")
else:
    print("Face features file not found:", FEATURES_FILE)
    features = {}

print("\n========== MATCHING CHECK ==========")

active_rolls = {
    str(roll).strip()
    for roll, _, active in database_students
    if active == 1
}

feature_rolls = {
    str(roll).strip()
    for roll in features
}

print("Active student roll numbers:", active_rolls)
print("Face feature roll numbers:", feature_rolls)
print("Matching active students:", active_rolls & feature_rolls)

print("\n====================================")
