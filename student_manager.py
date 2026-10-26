import tkinter as tk
from tkinter import ttk, messagebox
import json
import sqlite3
import cv2
import numpy as np
from pathlib import Path
from datetime import datetime
from camera_utils import open_camera

from database import (
    initialize_database,
    seed_default_categories,
    get_connection,
)

# ==================================================
# PATHS AND DATABASE INITIALIZATION
# ==================================================

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset"
FEATURES_FILE = DATASET_DIR / "face_features.json"
DASHBOARD_DATABASE_FILE = DATASET_DIR / "dashboard_admin.db"

DETECTOR_MODEL = BASE_DIR / "models" / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = BASE_DIR / "models" / "face_recognition_sface_2021dec.onnx"

DATASET_DIR.mkdir(parents=True, exist_ok=True)

initialize_database()
seed_default_categories()


# ==================================================
# FACE DATABASE HELPERS
# ==================================================

def load_face_database():
    if not FEATURES_FILE.exists():
        return {}

    try:
        with open(FEATURES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")

        return data

    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(
            f"Unable to read face database:\n{error}"
        ) from error


def save_face_database(data):
    """Save face embeddings atomically to reduce the risk of JSON corruption."""
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    temporary_file = FEATURES_FILE.with_suffix(".json.tmp")

    try:
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
            file.flush()
            import os
            os.fsync(file.fileno())
        temporary_file.replace(FEATURES_FILE)
    finally:
        if temporary_file.exists():
            try:
                temporary_file.unlink()
            except OSError:
                pass


def assign_registered_student_to_web_class(roll_number, section_id):
    """Link a newly registered student to the web class mirrored into this section."""
    if not DASHBOARD_DATABASE_FILE.exists():
        return False
    connection = None
    try:
        connection = sqlite3.connect(str(DASHBOARD_DATABASE_FILE), timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        mapping = connection.execute(
            "SELECT web_class_id FROM web_class_sync WHERE section_id=?",
            (section_id,),
        ).fetchone()
        if mapping is None:
            return False
        connection.execute(
            "INSERT OR IGNORE INTO class_students(class_id,roll,assigned_at) VALUES(?,?,?)",
            (mapping[0], roll_number, datetime.now().isoformat(timespec="seconds")),
        )
        connection.commit()
        return True
    except sqlite3.Error:
        # Registration remains successful if the dashboard database is unavailable.
        return False
    finally:
        if connection is not None:
            connection.close()


def _normalize_embedding(value):
    """Return a finite, normalized 1-D embedding or None if invalid."""
    try:
        vector = np.asarray(value, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None
    if vector.size == 0 or not np.all(np.isfinite(vector)):
        return None
    norm = float(np.linalg.norm(vector))
    if norm < 1e-8:
        return None
    return vector / norm


def find_similar_registered_face(new_embedding, face_database, threshold=0.363):
    """Compare a new SFace embedding against JSON and SQLite records.

    Returns (roll_number, name, similarity) for the strongest match when
    similarity reaches threshold, otherwise None. Inactive students are
    checked too, preventing re-registration under a different roll number.
    """
    new_vector = _normalize_embedding(new_embedding)
    if new_vector is None:
        raise RuntimeError("The captured face embedding is invalid.")

    candidates = {}

    # Read embeddings from JSON.
    for roll_number, record in (face_database or {}).items():
        if not isinstance(record, dict):
            continue
        vector = _normalize_embedding(record.get("feature"))
        if vector is not None:
            candidates[str(roll_number)] = {
                "name": str(record.get("name", "Unknown")),
                "feature": vector,
            }

    # Also read SQLite, in case the JSON and database are out of sync.
    try:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT roll_number, full_name, face_embedding
                FROM students
                """
            ).fetchall()
    except Exception as error:
        raise RuntimeError(
            f"Could not check existing student embeddings in SQLite: {error}"
        ) from error

    for row in rows:
        roll_number = str(row["roll_number"])
        raw_feature = row["face_embedding"]
        try:
            stored_feature = json.loads(raw_feature) if isinstance(raw_feature, str) else raw_feature
        except (json.JSONDecodeError, TypeError):
            continue
        vector = _normalize_embedding(stored_feature)
        if vector is not None:
            candidates[roll_number] = {
                "name": str(row["full_name"] or "Unknown"),
                "feature": vector,
            }

    best_match = None
    best_similarity = -1.0
    for roll_number, record in candidates.items():
        stored_vector = record["feature"]
        if stored_vector.shape != new_vector.shape:
            print(f"[DUPLICATE CHECK] Skipping {roll_number}: embedding dimensions do not match.")
            continue
        similarity = float(np.dot(new_vector, stored_vector))
        print(f"[DUPLICATE CHECK] New face vs {roll_number}: {similarity:.4f}")
        if similarity > best_similarity:
            best_similarity = similarity
            best_match = (roll_number, record["name"], similarity)

    print(f"[DUPLICATE CHECK] Best similarity: {best_similarity:.4f}")
    if best_match is not None and best_similarity >= threshold:
        print("[DUPLICATE CHECK] POSSIBLE DUPLICATE DETECTED")
        return best_match
    print("[DUPLICATE CHECK] No duplicate above threshold.")
    return None


# ==================================================
# CLASS DATA
# ==================================================

def get_classes():
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT
                s.id AS section_id,
                ec.name AS category,
                c.name AS course,
                al.name AS academic_level,
                COALESCE(sem.name, 'No semester') AS semester,
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
            """
        ).fetchall()


# ==================================================
# FACE CAPTURE
# ==================================================

def capture_face_embedding():
    if not DETECTOR_MODEL.exists():
        raise FileNotFoundError(
            f"Face detector model not found:\n{DETECTOR_MODEL}"
        )

    if not RECOGNIZER_MODEL.exists():
        raise FileNotFoundError(
            f"Face recognition model not found:\n{RECOGNIZER_MODEL}"
        )

    detector = cv2.FaceDetectorYN_create(
        str(DETECTOR_MODEL),
        "",
        (320, 320),
        0.8,
        0.3,
        5000,
    )

    recognizer = cv2.FaceRecognizerSF_create(
        str(RECOGNIZER_MODEL),
        "",
    )

    cap = open_camera(cv2)

    features = []

    try:
        while len(features) < 30:
            ret, frame = cap.read()

            if not ret:
                raise RuntimeError("Could not read webcam frame.")

            height, width = frame.shape[:2]
            detector.setInputSize((width, height))

            _, faces = detector.detect(frame)

            if faces is not None and len(faces) == 1:
                face = faces[0]

                # Require a reasonably large face for a usable embedding.
                if face[2] < 80 or face[3] < 80:
                    text = "Move closer to the camera"
                    color = (0, 165, 255)
                    cv2.putText(frame, text, (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                    cv2.putText(frame, "Press Q to cancel", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
                    cv2.imshow("Student Face Registration", frame)
                    if cv2.waitKey(100) & 0xFF == ord("q"):
                        break
                    continue

                aligned_face = recognizer.alignCrop(frame, face)
                feature = recognizer.feature(aligned_face)
                features.append(feature.flatten().tolist())

                x, y, w, h = map(int, face[:4])

                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + w, y + h),
                    (0, 255, 0),
                    2,
                )

                text = f"Samples: {len(features)}/30"
                color = (0, 255, 0)

            else:
                if faces is not None and len(faces) > 1:
                    text = "Only one person should be in frame"
                    color = (0, 0, 255)
                else:
                    text = "No face detected"
                    color = (0, 0, 255)

            cv2.putText(
                frame,
                text,
                (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                color,
                2,
            )

            cv2.putText(
                frame,
                "Press Q to cancel",
                (20, 70),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )

            cv2.imshow("Student Face Registration", frame)

            if cv2.waitKey(100) & 0xFF == ord("q"):
                break

    finally:
        cap.release()
        cv2.destroyAllWindows()

    if len(features) < 5:
        return None

    average_feature = np.mean(
        np.asarray(features, dtype=np.float32),
        axis=0,
    )
    normalized_feature = _normalize_embedding(average_feature)
    if normalized_feature is None:
        raise RuntimeError("Could not create a valid face embedding. Please try again.")
    return normalized_feature.tolist()


# ==================================================
# STUDENT MANAGEMENT APPLICATION
# ==================================================

class StudentManager:

    def __init__(self, root):
        self.root = root
        self.root.title("Smart Attendance - Student Management")
        self.root.geometry("1150x720")
        self.root.minsize(950, 600)
        self.root.configure(bg="#0f172a")

        self.class_lookup = {}

        self.build_ui()
        self.refresh_classes()
        self.refresh_students()

    # --------------------------------------------------
    # USER INTERFACE
    # --------------------------------------------------

    def build_ui(self):
        tk.Label(
            self.root,
            text="STUDENT MANAGEMENT",
            font=("Segoe UI", 22, "bold"),
            bg="#0f172a",
            fg="#38bdf8",
        ).pack(pady=(18, 5))

        tk.Label(
            self.root,
            text="Register, view, deactivate, and reactivate students",
            font=("Segoe UI", 10),
            bg="#0f172a",
            fg="#cbd5e1",
        ).pack(pady=(0, 15))

        form = tk.Frame(self.root, bg="#1e293b")
        form.pack(fill="x", padx=25, pady=5)

        labels = ["Roll Number", "Full Name", "Class / Section"]

        for column, label in enumerate(labels):
            tk.Label(
                form,
                text=label,
                bg="#1e293b",
                fg="white",
                font=("Segoe UI", 10, "bold"),
            ).grid(
                row=0,
                column=column,
                padx=12,
                pady=(15, 5),
                sticky="w",
            )

        self.roll_entry = ttk.Entry(form, width=22)
        self.roll_entry.grid(
            row=1, column=0, padx=12, pady=(0, 15), sticky="ew"
        )

        self.name_entry = ttk.Entry(form, width=28)
        self.name_entry.grid(
            row=1, column=1, padx=12, pady=(0, 15), sticky="ew"
        )

        self.class_combo = ttk.Combobox(
            form,
            state="readonly",
            width=45,
        )
        self.class_combo.grid(
            row=1, column=2, padx=12, pady=(0, 15), sticky="ew"
        )

        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(2, weight=2)

        buttons = tk.Frame(self.root, bg="#0f172a")
        buttons.pack(fill="x", padx=25, pady=12)

        ttk.Button(
            buttons,
            text="Register Student + Face",
            command=self.register_student,
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            buttons,
            text="Deactivate Student",
            command=self.deactivate_student,
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="Reactivate Student",
            command=self.reactivate_student,
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="Refresh Classes",
            command=self.refresh_classes,
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="Refresh Students",
            command=self.refresh_students,
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="Close",
            command=self.root.destroy,
        ).pack(side="right")

        filter_frame = tk.Frame(self.root, bg="#0f172a")
        filter_frame.pack(fill="x", padx=25, pady=(5, 0))

        tk.Label(
            filter_frame,
            text="Show:",
            bg="#0f172a",
            fg="white",
        ).pack(side="left", padx=(0, 8))

        self.status_filter = ttk.Combobox(
            filter_frame,
            state="readonly",
            values=("All Students", "Active Students", "Inactive Students"),
            width=20,
        )
        self.status_filter.current(0)
        self.status_filter.pack(side="left")
        self.status_filter.bind(
            "<<ComboboxSelected>>",
            lambda event: self.refresh_students(),
        )

        table_frame = tk.Frame(self.root, bg="#1e293b")
        table_frame.pack(
            fill="both",
            expand=True,
            padx=25,
            pady=(10, 15),
        )

        tk.Label(
            table_frame,
            text="STUDENT RECORDS",
            font=("Segoe UI", 12, "bold"),
            bg="#1e293b",
            fg="#38bdf8",
        ).pack(anchor="w", padx=12, pady=12)

        columns = (
            "roll_number",
            "full_name",
            "category",
            "course",
            "academic_level",
            "semester",
            "section",
            "status",
        )

        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=14,
        )

        headings = {
            "roll_number": "Roll Number",
            "full_name": "Student Name",
            "category": "Category",
            "course": "Course",
            "academic_level": "Year / Class",
            "semester": "Semester",
            "section": "Section",
            "status": "Status",
        }

        widths = {
            "roll_number": 100,
            "full_name": 150,
            "category": 140,
            "course": 125,
            "academic_level": 110,
            "semester": 90,
            "section": 75,
            "status": 90,
        }

        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                minwidth=65,
                anchor="w",
            )

        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree.yview,
        )

        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(12, 0),
            pady=(0, 12),
        )

        scrollbar.pack(
            side="right",
            fill="y",
            padx=(0, 12),
            pady=(0, 12),
        )

        self.status_label = tk.Label(
            self.root,
            text="Ready",
            bg="#0f172a",
            fg="#94a3b8",
            anchor="w",
        )
        self.status_label.pack(fill="x", padx=27, pady=(0, 10))

    # --------------------------------------------------
    # LOAD CLASSES
    # --------------------------------------------------

    def refresh_classes(self):
        try:
            classes = get_classes()
            self.class_lookup.clear()
            display_values = []

            for row in classes:
                label = (
                    f"{row['category']} | {row['course']} | "
                    f"{row['academic_level']} | {row['semester']} | "
                    f"Section {row['section']}"
                )

                display_values.append(label)
                self.class_lookup[label] = row["section_id"]

            self.class_combo["values"] = display_values

            if display_values:
                self.class_combo.current(0)
                self.status_label.config(
                    text=f"{len(display_values)} class/section options loaded."
                )
            else:
                self.class_combo.set("")
                self.status_label.config(
                    text="No classes available. Create a class first."
                )

        except Exception as error:
            messagebox.showerror(
                "Class Loading Error",
                str(error),
                parent=self.root,
            )

    # --------------------------------------------------
    # LOAD STUDENTS
    # --------------------------------------------------

    def refresh_students(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        selected_filter = self.status_filter.get()

        query = """
            SELECT
                st.roll_number,
                st.full_name,
                ec.name AS category,
                c.name AS course,
                al.name AS academic_level,
                COALESCE(sem.name, 'No semester') AS semester,
                s.name AS section,
                st.is_active
            FROM students st
            JOIN sections s
                ON st.section_id = s.id
            JOIN academic_levels al
                ON s.academic_level_id = al.id
            JOIN courses c
                ON al.course_id = c.id
            JOIN education_categories ec
                ON c.category_id = ec.id
            LEFT JOIN semesters sem
                ON s.semester_id = sem.id
        """

        if selected_filter == "Active Students":
            query += " WHERE st.is_active = 1"
        elif selected_filter == "Inactive Students":
            query += " WHERE st.is_active = 0"

        query += " ORDER BY st.full_name"

        try:
            with get_connection() as connection:
                rows = connection.execute(query).fetchall()

            for row in rows:
                status = "Active" if row["is_active"] else "Inactive"

                self.tree.insert(
                    "",
                    "end",
                    values=(
                        row["roll_number"],
                        row["full_name"],
                        row["category"],
                        row["course"],
                        row["academic_level"],
                        row["semester"],
                        row["section"],
                        status,
                    ),
                )

            self.status_label.config(
                text=f"Showing {len(rows)} student record(s)."
            )

        except Exception as error:
            messagebox.showerror(
                "Student Loading Error",
                str(error),
                parent=self.root,
            )

    # --------------------------------------------------
    # REGISTER STUDENT
    # --------------------------------------------------

    def register_student(self):
        roll_number = self.roll_entry.get().strip()
        full_name = self.name_entry.get().strip()
        selected_class = self.class_combo.get()

        if not roll_number or not full_name:
            messagebox.showwarning(
                "Missing Information",
                "Enter the roll number and full name.",
                parent=self.root,
            )
            return

        if not selected_class or selected_class not in self.class_lookup:
            messagebox.showwarning(
                "Select Class",
                "Select an existing class/section.",
                parent=self.root,
            )
            return

        section_id = self.class_lookup[selected_class]

        try:
            with get_connection() as connection:
                existing = connection.execute(
                    """
                    SELECT id, is_active
                    FROM students
                    WHERE roll_number = ?
                    """,
                    (roll_number,),
                ).fetchone()

            if existing:
                if existing["is_active"] == 0:
                    messagebox.showwarning(
                        "Student Is Inactive",
                        "This roll number already belongs to an inactive "
                        "student. Use Reactivate Student instead.",
                        parent=self.root,
                    )
                else:
                    messagebox.showerror(
                        "Duplicate Roll Number",
                        "This roll number is already registered.",
                        parent=self.root,
                    )
                return

            face_database = load_face_database()

            if roll_number in face_database:
                messagebox.showerror(
                    "Existing Face Record",
                    "This roll number already exists in face_features.json. "
                    "Check the records before registering again.",
                    parent=self.root,
                )
                return

            self.status_label.config(
                text="Face capture in progress..."
            )
            self.root.update_idletasks()

            embedding = capture_face_embedding()

            if embedding is None:
                messagebox.showwarning(
                    "Registration Cancelled",
                    "At least five face samples are required.",
                    parent=self.root,
                )
                self.status_label.config(text="Registration cancelled.")
                return

            # Detect duplicate faces BEFORE changing JSON or SQLite.
            self.status_label.config(text="Checking existing face records...")
            self.root.update_idletasks()
            duplicate = find_similar_registered_face(
                embedding,
                face_database,
                threshold=0.363,
            )
            if duplicate is not None:
                duplicate_roll, duplicate_name, similarity = duplicate
                messagebox.showerror(
                    "Possible Duplicate Face",
                    "This face resembles an existing student record.\n\n"
                    f"Existing student: {duplicate_name}\n"
                    f"Roll number: {duplicate_roll}\n"
                    f"Cosine similarity: {similarity:.4f}\n\n"
                    "Registration has been cancelled. Verify the record "
                    "before trying again.",
                    parent=self.root,
                )
                self.status_label.config(text=f"Possible duplicate detected: {duplicate_roll}")
                return

            # Back up the existing JSON file before changing it.
            previous_json = (
                FEATURES_FILE.read_text(encoding="utf-8")
                if FEATURES_FILE.exists()
                else None
            )

            face_database[roll_number] = {
                "name": full_name,
                "feature": embedding,
            }

            try:
                save_face_database(face_database)

                with get_connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO students (
                            roll_number,
                            full_name,
                            section_id,
                            face_embedding,
                            is_active
                        )
                        VALUES (?, ?, ?, ?, 1)
                        """,
                        (
                            roll_number,
                            full_name,
                            section_id,
                            json.dumps(embedding),
                        ),
                    )

            except Exception:
                # Restore the previous face JSON if the database insert fails.
                if previous_json is None:
                    if FEATURES_FILE.exists():
                        FEATURES_FILE.unlink()
                else:
                    FEATURES_FILE.write_text(
                        previous_json,
                        encoding="utf-8",
                    )
                raise

            linked_to_class = assign_registered_student_to_web_class(
                roll_number, section_id
            )

            messagebox.showinfo(
                "Success",
                f"Student registered successfully!\n\n"
                f"Name: {full_name}\n"
                f"Roll Number: {roll_number}\n"
                + ("Assigned to the matching dashboard class." if linked_to_class else "Return to the class Students page to assign this student."),
                parent=self.root,
            )

            self.roll_entry.delete(0, tk.END)
            self.name_entry.delete(0, tk.END)

            self.refresh_students()
            self.status_label.config(text="Student registered successfully.")

        except Exception as error:
            messagebox.showerror(
                "Registration Error",
                str(error),
                parent=self.root,
            )
            self.status_label.config(text="Registration failed.")

    # --------------------------------------------------
    # DEACTIVATE STUDENT (SOFT DELETE)
    # --------------------------------------------------

    def deactivate_student(self):
        selected = self.tree.selection()

        if not selected:
            messagebox.showwarning(
                "Select Student",
                "Select a student from the table first.",
                parent=self.root,
            )
            return

        values = self.tree.item(selected[0], "values")
        roll_number = values[0]
        student_name = values[1]
        current_status = values[7]

        if current_status == "Inactive":
            messagebox.showinfo(
                "Already Inactive",
                "This student is already inactive.",
                parent=self.root,
            )
            return

        confirm = messagebox.askyesno(
            "Confirm Deactivation",
            f"Deactivate {student_name}?\n\n"
            f"Roll Number: {roll_number}\n\n"
            "Historical attendance will be preserved.",
            parent=self.root,
        )

        if not confirm:
            return

        try:
            with get_connection() as connection:
                result = connection.execute(
                    """
                    UPDATE students
                    SET is_active = 0
                    WHERE roll_number = ? AND is_active = 1
                    """,
                    (roll_number,),
                )

            if result.rowcount == 0:
                messagebox.showinfo(
                    "No Change",
                    "The student was already inactive or was not found.",
                    parent=self.root,
                )
                return

            self.refresh_students()

            messagebox.showinfo(
                "Student Deactivated",
                f"{student_name} is now inactive.\n\n"
                "Historical attendance has been preserved.",
                parent=self.root,
            )

        except Exception as error:
            messagebox.showerror(
                "Deactivation Error",
                str(error),
                parent=self.root,
            )

    # --------------------------------------------------
    # REACTIVATE STUDENT
    # --------------------------------------------------

    def reactivate_student(self):
        selected = self.tree.selection()

        if not selected:
            messagebox.showwarning(
                "Select Student",
                "Select an inactive student from the table first.",
                parent=self.root,
            )
            return

        values = self.tree.item(selected[0], "values")
        roll_number = values[0]
        student_name = values[1]
        current_status = values[7]

        if current_status == "Active":
            messagebox.showinfo(
                "Already Active",
                "This student is already active.",
                parent=self.root,
            )
            return

        confirm = messagebox.askyesno(
            "Confirm Reactivation",
            f"Reactivate {student_name}?\n\n"
            f"Roll Number: {roll_number}",
            parent=self.root,
        )

        if not confirm:
            return

        try:
            with get_connection() as connection:
                result = connection.execute(
                    """
                    UPDATE students
                    SET is_active = 1
                    WHERE roll_number = ? AND is_active = 0
                    """,
                    (roll_number,),
                )

            if result.rowcount == 0:
                messagebox.showinfo(
                    "No Change",
                    "The student was already active or was not found.",
                    parent=self.root,
                )
                return

            self.refresh_students()

            messagebox.showinfo(
                "Student Reactivated",
                f"{student_name} has been reactivated.",
                parent=self.root,
            )

        except Exception as error:
            messagebox.showerror(
                "Reactivation Error",
                str(error),
                parent=self.root,
            )


# ==================================================
# RUN APPLICATION
# ==================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = StudentManager(root)
    root.mainloop()
