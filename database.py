import sqlite3
from pathlib import Path

# Project paths

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset"
DATABASE_PATH = DATASET_DIR / "smart_attendance.db"

def get_connection():

    DATASET_DIR.mkdir(parents=True, exist_ok=True)


    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def initialize_database():
  with get_connection() as connection:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS institution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS education_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY (category_id)
                REFERENCES education_categories(id),
            UNIQUE(category_id, name)
        );

        CREATE TABLE IF NOT EXISTS academic_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            level_order INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (course_id)
                REFERENCES courses(id),
            UNIQUE(course_id, name)
        );

        CREATE TABLE IF NOT EXISTS semesters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            academic_level_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            semester_order INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (academic_level_id)
                REFERENCES academic_levels(id),
            UNIQUE(academic_level_id, name)
        );

        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            academic_level_id INTEGER NOT NULL,
            semester_id INTEGER,
            name TEXT NOT NULL,
            FOREIGN KEY (academic_level_id)
                REFERENCES academic_levels(id),
            FOREIGN KEY (semester_id)
                REFERENCES semesters(id),
            UNIQUE(academic_level_id, semester_id, name)
        );

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roll_number TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            section_id INTEGER NOT NULL,
            face_embedding TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (section_id)
                REFERENCES sections(id)
        );

        CREATE TABLE IF NOT EXISTS attendance_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            session_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            FOREIGN KEY (section_id)
                REFERENCES sections(id)
        );

        CREATE TABLE IF NOT EXISTS attendance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            marked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id)
                REFERENCES attendance_sessions(id),
            FOREIGN KEY (student_id)
                REFERENCES students(id),
            UNIQUE(session_id, student_id)
        );
        """
    )


def seed_default_categories():
   categories = [
    "School",
    "PUC / Higher Secondary",
    "Engineering",
    "Medical & Healthcare",
    "Diploma / Polytechnic",
    "Arts & Science",
    "Commerce & Management",
    "Other Courses",
]

   with get_connection() as connection:
    connection.executemany(
        """
        INSERT OR IGNORE INTO education_categories (name)
        VALUES (?)
        """,
        [(category,) for category in categories],
    )

def get_categories():


 with get_connection() as connection:
    return connection.execute(
        """
        SELECT id, name
        FROM education_categories
        ORDER BY name
        """
    ).fetchall()


if __name__ == "__main__":
    initialize_database()
    seed_default_categories()

print("\nDatabase initialized successfully!")
print(f"Database location: {DATABASE_PATH}")

print("\nAvailable education categories:")

for category in get_categories():
    print(f"- {category['name']}")

print("\nYou can now proceed to the next development step.")
