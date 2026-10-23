import cv2
import numpy as np
import os
import json
import csv
import sqlite3
from datetime import datetime
from pathlib import Path

# ---------------- CONFIGURATION ----------------

BASE_DIR = Path(__file__).resolve().parent

DETECTOR_MODEL = BASE_DIR / "models" / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = BASE_DIR / "models" / "face_recognition_sface_2021dec.onnx"

FEATURES_FILE = BASE_DIR / "dataset" / "face_features.json"
DATABASE_FILE = BASE_DIR / "dataset" / "smart_attendance.db"
ATTENDANCE_DIR = BASE_DIR / "attendance_records"

# SFace cosine similarity threshold.
RECOGNITION_THRESHOLD = 0.45

ATTENDANCE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------- LOAD FACE FEATURES ----------------

if not FEATURES_FILE.exists():
    raise FileNotFoundError(
        f"Face features file not found: {FEATURES_FILE}\n"
        "Register students first."
    )

with open(FEATURES_FILE, "r", encoding="utf-8") as file:
    students = json.load(file)

if not students:
    raise ValueError("No registered face features found.")


# ---------------- CONNECT TO SQLITE ----------------

if not DATABASE_FILE.exists():
    raise FileNotFoundError(
        f"Student database not found: {DATABASE_FILE}\n"
        "Initialize your database before starting attendance."
    )


def get_active_students():
    """
    Return roll numbers of active students from SQLite.

    Only students whose is_active value is 1 and whose
    roll numbers have registered face features are eligible.
    """
    connection = sqlite3.connect(str(DATABASE_FILE))

    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT roll_number
            FROM students
            WHERE is_active = 1
        """)

        active_roll_numbers = {
            str(row[0]).strip()
            for row in cursor.fetchall()
            if row[0] is not None
        }

        return {
            roll_no: student
            for roll_no, student in students.items()
            if str(roll_no).strip() in active_roll_numbers
        }

    finally:
        connection.close()


active_students = get_active_students()

if not active_students:
    raise ValueError(
        "No active students with registered face features were found.\n"
        "Register students or reactivate existing students first."
    )

print(f"Active students loaded: {len(active_students)}")


# ---------------- INITIALIZE MODELS ----------------

if not DETECTOR_MODEL.exists():
    raise FileNotFoundError(
        f"Face detector model not found: {DETECTOR_MODEL}"
    )

if not RECOGNIZER_MODEL.exists():
    raise FileNotFoundError(
        f"Face recognition model not found: {RECOGNIZER_MODEL}"
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


# ---------------- ATTENDANCE FILE ----------------

today = datetime.now().strftime("%Y-%m-%d")

attendance_file = ATTENDANCE_DIR / f"attendance_{today}.csv"

if not attendance_file.exists():
    with open(
        attendance_file,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:
        writer = csv.writer(file)

        writer.writerow([
            "Roll Number",
            "Name",
            "Date",
            "Time",
            "Status"
        ])


# Prevent duplicate attendance for the same day.
marked_students = set()

with open(
    attendance_file,
    "r",
    newline="",
    encoding="utf-8"
) as file:
    reader = csv.DictReader(file)

    for row in reader:
        roll_no = row.get("Roll Number")

        if roll_no:
            marked_students.add(roll_no.strip())


# ---------------- RECOGNITION LOOP ----------------

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    raise RuntimeError(
        "Could not open webcam. Check camera permissions."
    )

print("\nSmart Attendance System started.")
print("Only active students can be marked present.")
print("Press Q to quit.\n")


try:
    while True:
        success, frame = cap.read()

        if not success:
            print("Unable to read from webcam.")
            break

        height, width = frame.shape[:2]

        detector.setInputSize((width, height))

        _, faces = detector.detect(frame)

        if faces is not None:
            for face in faces:

                aligned_face = recognizer.alignCrop(frame, face)
                feature = recognizer.feature(aligned_face)

                best_roll = None
                best_score = -1.0

                # Compare against ACTIVE students only.
                for roll_no, student in active_students.items():

                    stored_feature = np.asarray(
                        student["feature"],
                        dtype=np.float32
                    ).reshape(1, -1)

                    score = recognizer.match(
                        feature,
                        stored_feature,
                        cv2.FaceRecognizerSF_FR_COSINE
                    )

                    if score > best_score:
                        best_score = score
                        best_roll = roll_no

                x, y, w, h = map(int, face[:4])

                if (
                    best_roll is not None
                    and best_score >= RECOGNITION_THRESHOLD
                ):

                    student_name = active_students[best_roll]["name"]

                    if best_roll not in marked_students:

                        current = datetime.now()

                        with open(
                            attendance_file,
                            "a",
                            newline="",
                            encoding="utf-8"
                        ) as file:

                            writer = csv.writer(file)

                            writer.writerow([
                                best_roll,
                                student_name,
                                current.strftime("%Y-%m-%d"),
                                current.strftime("%H:%M:%S"),
                                "Present"
                            ])

                        marked_students.add(best_roll)

                        print(
                            f"Attendance marked: {student_name} "
                            f"({best_roll})"
                        )

                    label = (
                        f"{student_name} ({best_roll}) "
                        f"{best_score:.2f}"
                    )

                    box_color = (0, 255, 0)

                else:

                    label = f"Unknown/Inactive ({best_score:.2f})"
                    box_color = (0, 0, 255)

                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + w, y + h),
                    box_color,
                    2
                )

                cv2.putText(
                    frame,
                    label,
                    (x, max(y - 10, 25)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    box_color,
                    2
                )

        cv2.imshow("Smart Attendance System", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    cap.release()
    cv2.destroyAllWindows()

print(f"\nAttendance saved in: {attendance_file}")
