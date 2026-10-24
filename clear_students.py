import json
from pathlib import Path
from database import get_connection

BASE_DIR = Path(__file__).resolve().parent
FEATURES_FILE = BASE_DIR / "dataset" / "face_features.json"

confirmation = input(
    "This will permanently delete all student records and face "
    "embeddings. Type DELETE to continue: "
)

if confirmation != "DELETE":
    print("Operation cancelled. No data was deleted.")
    raise SystemExit

# Back up the face database before deleting anything.
if FEATURES_FILE.exists():
    backup_file = FEATURES_FILE.with_name(
        "face_features.backup.json"
    )
    backup_file.write_text(
        FEATURES_FILE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    print(f"Face database backup saved: {backup_file}")

try:
    # Remove student records from SQLite.
    with get_connection() as connection:
        connection.execute("BEGIN")

        connection.execute("DELETE FROM attendance_records")
        connection.execute("DELETE FROM students")

    # Clear registered face embeddings.
    FEATURES_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    FEATURES_FILE.write_text(
        json.dumps({}, indent=4),
        encoding="utf-8",
    )

    print("All student records have been deleted.")
    print("All registered face embeddings have been cleared.")
    print("Classes, courses, and sections have been preserved.")

except Exception as error:
    print(f"Error: {error}")
    print("Check the database before attempting the operation again.")
