import cv2
import json
import sqlite3
import numpy as np
import tkinter as tk

from tkinter import ttk, messagebox
from pathlib import Path
from datetime import datetime


# =====================================================
# CONFIGURATION
# =====================================================

BASE_DIR = Path(__file__).resolve().parent

DATABASE_FILE = BASE_DIR / "dataset" / "smart_attendance.db"
FEATURES_FILE = BASE_DIR / "dataset" / "face_features.json"

DETECTOR_MODEL = (
    BASE_DIR / "models" / "face_detection_yunet_2023mar.onnx"
)

RECOGNIZER_MODEL = (
    BASE_DIR / "models" / "face_recognition_sface_2021dec.onnx"
)

RECOGNITION_THRESHOLD = 0.45


# =====================================================
# DATABASE HELPERS
# =====================================================

def get_connection():
    if not DATABASE_FILE.exists():
        raise FileNotFoundError(
            f"Database not found:\n{DATABASE_FILE}"
        )

    connection = sqlite3.connect(str(DATABASE_FILE))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def get_classes():
    """Return existing classes with their category and course."""

    with get_connection() as connection:
        return connection.execute("""
            SELECT
                s.id AS section_id,
                ec.name AS category,
                c.name AS course,
                al.name AS academic_level,
                COALESCE(sem.name, 'No Semester') AS semester,
                s.name AS section
            FROM sections s
            JOIN academic_levels al
                ON s.academic_level_id = al.id
            JOIN courses c
                ON al.course_id = c.id
            JOIN education_categories ec
                ON c.category_id = ec.id
            LEFT JOIN semesters sem
                ON s.semester_id = sem.id
            ORDER BY
                ec.name,
                c.name,
                al.level_order,
                sem.semester_order,
                s.name
        """).fetchall()


def get_active_students(section_id):
    """Return active students belonging to the selected section."""

    with get_connection() as connection:
        return connection.execute("""
            SELECT id, roll_number, full_name
            FROM students
            WHERE section_id = ?
              AND is_active = 1
            ORDER BY roll_number
        """, (section_id,)).fetchall()


def create_session(section_id, subject):
    """Create a new attendance session in SQLite."""

    now = datetime.now()
    session_date = now.strftime("%Y-%m-%d")
    start_time = now.strftime("%H:%M:%S")

    with get_connection() as connection:
        cursor = connection.execute("""
            INSERT INTO attendance_sessions (
                section_id,
                subject,
                session_date,
                start_time,
                end_time,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            section_id,
            subject,
            session_date,
            start_time,
            start_time,
            "in_progress"
        ))

        return cursor.lastrowid


def mark_student_present(session_id, student_id):
    """
    Record attendance for a student.

    Rechecks the student's active status and section inside
    the database before inserting the attendance record.
    """

    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                s.id,
                s.is_active,
                s.section_id,
                a.section_id AS session_section
            FROM students s
            JOIN attendance_sessions a
                ON a.id = ?
            WHERE s.id = ?
        """, (session_id, student_id))

        row = cursor.fetchone()

        if row is None:
            return False, "Student or session not found."

        if row["is_active"] != 1:
            return False, "Student is inactive."

        if row["section_id"] != row["session_section"]:
            return False, "Student does not belong to this section."

        try:
            cursor.execute("""
                INSERT INTO attendance_records (
                    session_id,
                    student_id,
                    status
                )
                VALUES (?, ?, ?)
            """, (session_id, student_id, "Present"))

            return True, "Attendance marked."

        except sqlite3.IntegrityError:
            # UNIQUE(session_id, student_id) prevents duplicates.
            return False, "Attendance already marked."


def finish_session(session_id):
    """Mark the session as completed."""

    now = datetime.now().strftime("%H:%M:%S")

    with get_connection() as connection:
        connection.execute("""
            UPDATE attendance_sessions
            SET end_time = ?, status = ?
            WHERE id = ?
        """, (now, "completed", session_id))


def get_session_count(session_id):
    with get_connection() as connection:
        row = connection.execute("""
            SELECT COUNT(*) AS total
            FROM attendance_records
            WHERE session_id = ?
              AND status = 'Present'
        """, (session_id,)).fetchone()

        return row["total"]


# =====================================================
# FACE RECOGNITION
# =====================================================

def load_face_features():
    if not FEATURES_FILE.exists():
        raise FileNotFoundError(
            f"Face feature file not found:\n{FEATURES_FILE}"
        )

    with open(FEATURES_FILE, "r", encoding="utf-8") as file:
        features = json.load(file)

    return features


def load_models():
    if not DETECTOR_MODEL.exists():
        raise FileNotFoundError(
            f"Face detector model not found:\n{DETECTOR_MODEL}"
        )

    if not RECOGNIZER_MODEL.exists():
        raise FileNotFoundError(
            f"Face recognizer model not found:\n{RECOGNIZER_MODEL}"
        )

    detector = cv2.FaceDetectorYN_create(
        str(DETECTOR_MODEL),
        "",
        (320, 320),
        0.8,
        0.3,
        5000
    )

    recognizer = cv2.FaceRecognizerSF_create(
        str(RECOGNIZER_MODEL),
        ""
    )

    return detector, recognizer


# =====================================================
# TKINTER APPLICATION
# =====================================================

class ClassAttendanceApp:

    def __init__(self, root):
        self.root = root

        self.root.title("Smart Attendance - Class Attendance")
        self.root.geometry("1050x720")
        self.root.configure(bg="#101827")

        self.classes = []
        self.selected_class = None

        self.session_id = None
        self.session_running = False

        self.cap = None
        self.detector = None
        self.recognizer = None

        self.students_by_roll = {}
        self.features = {}
        self.marked_student_ids = set()

        self.setup_ui()
        self.load_classes()

        self.root.protocol("WM_DELETE_WINDOW", self.close_application)

    # -------------------------------------------------
    # USER INTERFACE
    # -------------------------------------------------

    def setup_ui(self):

        tk.Label(
            self.root,
            text="CLASS-WISE ATTENDANCE",
            font=("Arial", 23, "bold"),
            bg="#101827",
            fg="white"
        ).pack(pady=20)

        form = tk.Frame(
            self.root,
            bg="#182235",
            padx=20,
            pady=20
        )

        form.pack(fill="x", padx=25, pady=10)

        tk.Label(
            form,
            text="Select Class / Section",
            font=("Arial", 11),
            bg="#182235",
            fg="white"
        ).grid(row=0, column=0, sticky="w", padx=10, pady=10)

        self.class_var = tk.StringVar()

        self.class_box = ttk.Combobox(
            form,
            textvariable=self.class_var,
            state="readonly",
            width=65
        )

        self.class_box.grid(
            row=0,
            column=1,
            sticky="w",
            padx=10,
            pady=10
        )

        tk.Label(
            form,
            text="Subject",
            font=("Arial", 11),
            bg="#182235",
            fg="white"
        ).grid(row=1, column=0, sticky="w", padx=10, pady=10)

        self.subject_var = tk.StringVar()

        ttk.Entry(
            form,
            textvariable=self.subject_var,
            width=40
        ).grid(
            row=1,
            column=1,
            sticky="w",
            padx=10,
            pady=10
        )

        buttons = tk.Frame(self.root, bg="#101827")
        buttons.pack(pady=12)

        self.start_button = tk.Button(
            buttons,
            text="Start Attendance",
            command=self.start_attendance,
            bg="#16a34a",
            fg="white",
            font=("Arial", 11, "bold"),
            padx=18,
            pady=10,
            relief="flat"
        )

        self.start_button.grid(row=0, column=0, padx=8)

        self.stop_button = tk.Button(
            buttons,
            text="Stop & Finish Session",
            command=self.stop_attendance,
            bg="#dc2626",
            fg="white",
            font=("Arial", 11, "bold"),
            padx=18,
            pady=10,
            relief="flat",
            state="disabled"
        )

        self.stop_button.grid(row=0, column=1, padx=8)

        self.status_var = tk.StringVar(
            value="Select a class and subject to begin."
        )

        tk.Label(
            self.root,
            textvariable=self.status_var,
            font=("Arial", 12, "bold"),
            bg="#101827",
            fg="#93c5fd"
        ).pack(pady=10)

        self.count_var = tk.StringVar(
            value="Present: 0"
        )

        tk.Label(
            self.root,
            textvariable=self.count_var,
            font=("Arial", 14, "bold"),
            bg="#101827",
            fg="#4ade80"
        ).pack(pady=5)

        table_frame = tk.Frame(self.root, bg="#101827")
        table_frame.pack(
            fill="both",
            expand=True,
            padx=25,
            pady=15
        )

        columns = ("roll", "name", "status", "time")

        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=12
        )

        headings = {
            "roll": "Roll Number",
            "name": "Student Name",
            "status": "Attendance Status",
            "time": "Marked At"
        }

        widths = {
            "roll": 180,
            "name": 280,
            "status": 200,
            "time": 180
        }

        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                anchor="center"
            )

        self.tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree.yview
        )

        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

    # -------------------------------------------------
    # LOAD CLASSES
    # -------------------------------------------------

    def load_classes(self):

        try:
            self.classes = get_classes()

            labels = []

            for row in self.classes:
                label = (
                    f"{row['category']} | "
                    f"{row['course']} | "
                    f"{row['academic_level']} | "
                    f"{row['semester']} | "
                    f"Section {row['section']} "
                    f"(ID: {row['section_id']})"
                )

                labels.append(label)

            self.class_box["values"] = labels

            if labels:
                self.class_box.current(0)
            else:
                self.status_var.set(
                    "No classes found. Add a class first."
                )

        except Exception as error:
            messagebox.showerror(
                "Class Loading Error",
                str(error)
            )

    # -------------------------------------------------
    # START SESSION
    # -------------------------------------------------

    def start_attendance(self):

        if self.session_running:
            return

        selected_index = self.class_box.current()

        if selected_index < 0 or selected_index >= len(self.classes):
            messagebox.showwarning(
                "Select Class",
                "Please select a class or section."
            )
            return

        subject = self.subject_var.get().strip()

        if not subject:
            messagebox.showwarning(
                "Subject Required",
                "Enter the subject name."
            )
            return

        selected_class = self.classes[selected_index]
        section_id = selected_class["section_id"]

        try:
            active_students = get_active_students(section_id)

            if not active_students:
                messagebox.showwarning(
                    "No Active Students",
                    "There are no active students in this section."
                )
                return

            all_features = load_face_features()

            self.students_by_roll = {
                str(row["roll_number"]).strip(): row
                for row in active_students
            }

            self.features = {
                roll: all_features[roll]
                for roll in self.students_by_roll
                if roll in all_features
            }

            if not self.features:
                messagebox.showwarning(
                    "Face Features Missing",
                    "None of the active students in this section "
                    "have matching face features.\n\n"
                    "Check roll numbers in SQLite and "
                    "dataset/face_features.json."
                )
                return

            self.detector, self.recognizer = load_models()

            self.cap = cv2.VideoCapture(0)

            if not self.cap.isOpened():
                self.cap.release()
                self.cap = None

                messagebox.showerror(
                    "Camera Error",
                    "Could not open webcam."
                )
                return

            self.session_id = create_session(
                section_id,
                subject
            )

            self.selected_class = selected_class
            self.marked_student_ids.clear()

            for item in self.tree.get_children():
                self.tree.delete(item)

            self.session_running = True

            self.start_button.config(state="disabled")
            self.stop_button.config(state="normal")
            self.class_box.config(state="disabled")

            self.status_var.set(
                f"Session #{self.session_id} running: {subject}"
            )

            self.update_attendance_frame()

        except Exception as error:
            self.release_camera()

            if self.session_id is not None:
                try:
                    finish_session(self.session_id)
                except Exception:
                    pass

                self.session_id = None

            messagebox.showerror(
                "Start Attendance Error",
                str(error)
            )

    # -------------------------------------------------
    # CAMERA LOOP
    # -------------------------------------------------

    def update_attendance_frame(self):

        if not self.session_running or self.cap is None:
            return

        success, frame = self.cap.read()

        if not success:
            self.status_var.set(
                "Camera frame unavailable."
            )

            self.root.after(100, self.update_attendance_frame)
            return

        height, width = frame.shape[:2]

        self.detector.setInputSize((width, height))

        _, faces = self.detector.detect(frame)

        if faces is not None:

            for face in faces:

                aligned_face = self.recognizer.alignCrop(
                    frame,
                    face
                )

                live_feature = self.recognizer.feature(
                    aligned_face
                )

                best_roll = None
                best_score = -1.0

                for roll, student in self.features.items():

                    stored_feature = np.asarray(
                        student["feature"],
                        dtype=np.float32
                    ).reshape(1, -1)

                    score = self.recognizer.match(
                        live_feature,
                        stored_feature,
                        cv2.FaceRecognizerSF_FR_COSINE
                    )

                    if score > best_score:
                        best_score = score
                        best_roll = roll

                x, y, w, h = map(int, face[:4])

                label = "Unknown"
                color = (0, 0, 255)

                if (
                    best_roll is not None
                    and best_score >= RECOGNITION_THRESHOLD
                ):

                    student_row = self.students_by_roll[best_roll]

                    # Recheck current active status and section
                    # in the database before marking attendance.
                    success_mark, result = mark_student_present(
                        self.session_id,
                        student_row["id"]
                    )

                    if success_mark:

                        label = (
                            f"{student_row['full_name']} "
                            f"{best_score:.2f}"
                        )

                        color = (0, 255, 0)

                        if student_row["id"] not in self.marked_student_ids:

                            self.marked_student_ids.add(
                                student_row["id"]
                            )

                            marked_time = datetime.now().strftime(
                                "%H:%M:%S"
                            )

                            self.tree.insert(
                                "",
                                "end",
                                values=(
                                    student_row["roll_number"],
                                    student_row["full_name"],
                                    "Present",
                                    marked_time
                                )
                            )

                            self.status_var.set(
                                f"Attendance marked: "
                                f"{student_row['full_name']}"
                            )

                    else:

                        if result == "Attendance already marked.":
                            label = (
                                f"{student_row['full_name']} "
                                "(Already marked)"
                            )

                            color = (0, 255, 255)

                        else:
                            label = "Inactive / Not in section"
                            color = (0, 0, 255)

                else:
                    label = f"Unknown ({best_score:.2f})"

                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + w, y + h),
                    color,
                    2
                )

                cv2.putText(
                    frame,
                    label,
                    (x, max(y - 10, 25)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    color,
                    2
                )

        present_count = get_session_count(self.session_id)

        self.count_var.set(
            f"Present: {present_count} / "
            f"{len(self.students_by_roll)} active students"
        )

        cv2.imshow(
            "Class-Wise Smart Attendance - Press Q to stop",
            frame
        )

        # Q in the camera window stops the session.
        if cv2.waitKey(1) & 0xFF == ord("q"):
            self.stop_attendance()
            return

        if self.session_running:
            self.root.after(10, self.update_attendance_frame)

    # -------------------------------------------------
    # STOP SESSION
    # -------------------------------------------------

    def stop_attendance(self):

        if not self.session_running:
            return

        self.session_running = False

        session_id = self.session_id
        self.session_id = None

        try:
            if session_id is not None:
                finish_session(session_id)

            present_count = (
                get_session_count(session_id)
                if session_id is not None
                else 0
            )

        except Exception as error:
            messagebox.showerror(
                "Session Error",
                f"Could not finish attendance session:\n{error}"
            )

            present_count = 0

        finally:
            self.release_camera()

            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.class_box.config(state="readonly")

            self.status_var.set("Attendance session completed.")

            self.count_var.set(
                f"Present: {present_count}"
            )

        messagebox.showinfo(
            "Session Completed",
            f"Attendance session finished.\n\n"
            f"Session ID: {session_id}\n"
            f"Present: {present_count}"
        )

    # -------------------------------------------------
    # CAMERA CLEANUP
    # -------------------------------------------------

    def release_camera(self):

        if self.cap is not None:
            self.cap.release()
            self.cap = None

        cv2.destroyAllWindows()

    def close_application(self):

        if self.session_running:
            if messagebox.askyesno(
                "Finish Session",
                "An attendance session is running. "
                "Stop it and close the application?"
            ):
                self.stop_attendance()
            else:
                return

        self.release_camera()
        self.root.destroy()


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    root = tk.Tk()

    app = ClassAttendanceApp(root)

    root.mainloop()
