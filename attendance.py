import cv2
import numpy as np
import json
import csv
import sqlite3
import os
from datetime import datetime
from pathlib import Path
from camera_utils import open_camera
from liveness import LivenessChallenge

# ---------------- CONFIGURATION ----------------
BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = Path(os.environ.get("SMART_ATTENDANCE_DATA_DIR", BASE_DIR / "dataset")).expanduser()
if not DATASET_DIR.is_absolute():
    DATASET_DIR = (BASE_DIR / DATASET_DIR).resolve()
DETECTOR_MODEL = BASE_DIR / "models" / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = BASE_DIR / "models" / "face_recognition_sface_2021dec.onnx"
FEATURES_FILE = DATASET_DIR / "face_features.json"
DATABASE_FILE = DATASET_DIR / "smart_attendance.db"
ATTENDANCE_DIR = DATASET_DIR / "attendance_records"

# Starting values only; calibrate using genuine and non-matching test faces.
RECOGNITION_THRESHOLD = 0.45
MIN_MATCH_GAP = 0.05
MIN_FACE_SIZE = 80
DETECTOR_SCORE_THRESHOLD = 0.8
DETECTOR_NMS_THRESHOLD = 0.3
DETECTOR_TOP_K = 5000
ACTIVE_REFRESH_SECONDS = 1.0

ATTENDANCE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------- VALIDATE FILES ----------------
if not FEATURES_FILE.exists():
    raise FileNotFoundError(
        f"Face features file not found: {FEATURES_FILE}\nRegister students first."
    )
with open(FEATURES_FILE, "r", encoding="utf-8") as file:
    students = json.load(file)

if not isinstance(students, dict) or not students:
    raise ValueError("No registered face features found or JSON format is invalid.")
if not DATABASE_FILE.exists():
    raise FileNotFoundError(
        f"Student database not found: {DATABASE_FILE}\nInitialize the database first."
    )


# ---------------- DATABASE HELPERS ----------------
def get_active_roll_numbers():
    """Read current active students from SQLite."""
    connection = sqlite3.connect(str(DATABASE_FILE), timeout=5)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT roll_number FROM students WHERE is_active = 1")
        return {
            str(row[0]).strip()
            for row in cursor.fetchall()
            if row[0] is not None
        }
    finally:
        connection.close()


def normalize_embedding(value):
    """Validate and L2-normalize one SFace embedding."""
    try:
        vector = np.asarray(value, dtype=np.float32).reshape(1, -1)
    except (TypeError, ValueError):
        return None
    if vector.size == 0 or not np.isfinite(vector).all():
        return None
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        return None
    return vector / norm


# Only use JSON records whose roll numbers are active in SQLite.
active_roll_numbers = get_active_roll_numbers()
active_students = {}
for raw_roll, student in students.items():
    roll_no = str(raw_roll).strip()
    if roll_no not in active_roll_numbers:
        continue
    if not isinstance(student, dict) or not student.get("name") or student.get("feature") is None:
        print(f"Warning: invalid student record skipped: {roll_no}")
        continue
    normalized = normalize_embedding(student["feature"])
    if normalized is None:
        print(f"Warning: invalid face embedding skipped: {roll_no}")
        continue
    active_students[roll_no] = {
        "name": str(student["name"]),
        "feature": normalized,
    }

if not active_students:
    raise ValueError(
        "No active students with valid registered face features were found. "
        "Register students or reactivate them first."
    )
print(f"Active students loaded: {len(active_students)}")


# ---------------- INITIALIZE MODELS ----------------
if not DETECTOR_MODEL.exists():
    raise FileNotFoundError(f"Face detector model not found: {DETECTOR_MODEL}")
if not RECOGNIZER_MODEL.exists():
    raise FileNotFoundError(f"Face recognition model not found: {RECOGNIZER_MODEL}")

detector = cv2.FaceDetectorYN_create(
    str(DETECTOR_MODEL), "", (320, 320),
    DETECTOR_SCORE_THRESHOLD, DETECTOR_NMS_THRESHOLD, DETECTOR_TOP_K
)
recognizer = cv2.FaceRecognizerSF_create(str(RECOGNIZER_MODEL), "")


# ---------------- ATTENDANCE CSV ----------------
today = datetime.now().strftime("%Y-%m-%d")
attendance_file = ATTENDANCE_DIR / f"attendance_{today}.csv"
CSV_HEADERS = ["Roll Number", "Name", "Date", "Time", "Status"]

if not attendance_file.exists():
    with open(attendance_file, "w", newline="", encoding="utf-8") as file:
        csv.writer(file).writerow(CSV_HEADERS)

def load_marked_students():
    marked = set()
    with open(attendance_file, "r", newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            roll_no = row.get("Roll Number")
            if roll_no:
                marked.add(str(roll_no).strip().casefold())
    return marked

marked_students = load_marked_students()
liveness_checks = {}

def mark_attendance(roll_no, student_name):
    """
    Record attendance once per student per day.

    Returns:
        "marked"         - a new CSV row was written
        "already_marked" - attendance was already recorded today
        "error"          - the roll number was invalid or writing failed
    """
    key = str(roll_no).strip().casefold()
    if not key:
        return "error"

    # Fast path: this student has already been marked in this process or
    # was present in today's CSV when the application started.
    if key in marked_students:
        # Repeated console output suppressed; the camera label shows "Already Marked".
        return "already_marked"

    # Re-read the file before writing in case it was updated while running.
    if attendance_file.exists():
        try:
            with open(
                attendance_file, "r", newline="", encoding="utf-8-sig"
            ) as file:
                for row in csv.DictReader(file):
                    existing = row.get("Roll Number")
                    if existing and str(existing).strip().casefold() == key:
                        marked_students.add(key)
                        # Repeated console output suppressed; the camera label shows "Already Marked".
                        return "already_marked"
        except (OSError, csv.Error) as error:
            print(f"Could not verify today's attendance CSV: {error}")
            return "error"

    current = datetime.now()
    try:
        with open(attendance_file, "a", newline="", encoding="utf-8") as file:
            csv.writer(file).writerow([
                str(roll_no).strip(),
                student_name,
                current.strftime("%Y-%m-%d"),
                current.strftime("%H:%M:%S"),
                "Present",
            ])
    except OSError as error:
        print(f"Could not write attendance for {roll_no}: {error}")
        return "error"

    marked_students.add(key)
    print(f"Attendance marked successfully: {student_name} ({roll_no})")
    return "marked"


# ---------------- FACE RECOGNITION ----------------
def identify_face(frame, face, currently_active):
    """
    Return (roll_no, best_score, second_score, reason).
    Reject low scores and ambiguous top-two matches.
    """
    x, y, w, h = map(int, face[:4])
    if w < MIN_FACE_SIZE or h < MIN_FACE_SIZE:
        return None, -1.0, -1.0, "Move closer"

    try:
        aligned_face = recognizer.alignCrop(frame, face)
        raw_feature = recognizer.feature(aligned_face)
    except cv2.error:
        return None, -1.0, -1.0, "Alignment failed"

    probe = normalize_embedding(raw_feature)
    if probe is None:
        return None, -1.0, -1.0, "Invalid face feature"

    scores = []
    for roll_no, student in active_students.items():
        if roll_no not in currently_active:
            continue
        score = float(recognizer.match(
            probe, student["feature"], cv2.FaceRecognizerSF_FR_COSINE
        ))
        if np.isfinite(score):
            scores.append((score, roll_no))

    if not scores:
        return None, -1.0, -1.0, "Unknown / inactive"

    scores.sort(reverse=True)
    best_score, best_roll = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else -1.0

    if best_score < RECOGNITION_THRESHOLD:
        return None, best_score, second_score, "Unknown person"
    if len(scores) > 1 and best_score - second_score < MIN_MATCH_GAP:
        return None, best_score, second_score, "Ambiguous match"

    return best_roll, best_score, second_score, "Recognized"


# ---------------- WEBCAM LOOP ----------------
cap = open_camera(cv2)

print("\nSmart Attendance System started.")
print("Only active students can be marked present.")
print(f"Similarity threshold: {RECOGNITION_THRESHOLD:.2f}")
print(f"Minimum best/second-best gap: {MIN_MATCH_GAP:.2f}")
print("Press Q to quit.\n")

last_active_refresh = 0.0
currently_active = set(active_roll_numbers)

try:
    while True:
        success, frame = cap.read()
        if not success:
            print("Unable to read from webcam.")
            break

        frame = cv2.flip(frame, 1)
        height, width = frame.shape[:2]
        detector.setInputSize((width, height))

        # Refresh active status once per second. If the database fails, fail closed.
        now = datetime.now().timestamp()
        if now - last_active_refresh >= ACTIVE_REFRESH_SECONDS:
            try:
                currently_active = get_active_roll_numbers()
            except sqlite3.Error as error:
                currently_active = set()
                print(f"Database error; attendance disabled until next refresh: {error}")
            last_active_refresh = now

        _, faces = detector.detect(frame)
        if faces is not None:
            for face in faces:
                x, y, w, h = map(int, face[:4])
                roll_no, score, second_score, reason = identify_face(
                    frame, face, currently_active
                )

                if roll_no is not None:
                    student_name = active_students[roll_no]["name"]
                    if str(roll_no).strip().casefold() in marked_students:
                        attendance_status = "already_marked"
                        prompt = "Already marked"
                    else:
                        challenge = liveness_checks.setdefault(roll_no, LivenessChallenge())
                        live, prompt = challenge.update(frame, face)
                        attendance_status = (
                            mark_attendance(roll_no, student_name) if live else "checking"
                        )

                    if attendance_status == "checking":
                        label = f"{student_name}: {prompt}"
                        color = (0, 165, 255)
                    elif attendance_status == "marked":
                        label = f"{student_name} - Attendance Marked"
                    elif attendance_status == "already_marked":
                        label = f"{student_name} - Already Marked"
                    else:
                        label = f"{student_name} - Attendance Error"

                    # Include the similarity score while keeping the status visible.
                    label = f"{label} ({score:.2f})"
                    if attendance_status in {"marked", "already_marked"}:
                        color = (0, 200, 0)
                    elif attendance_status == "checking":
                        color = (0, 165, 255)
                    else:
                        color = (0, 0, 255)
                else:
                    score_text = f"{score:.2f}" if score >= 0 else "--"
                    label = f"{reason} ({score_text})"
                    color = (0, 165, 255) if reason == "Move closer" else (0, 0, 255)

                cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                cv2.putText(
                    frame, label[:75], (x, max(y - 10, 25)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2
                )

        cv2.putText(
            frame, "Q: Quit | Attendance once per student per day",
            (10, height - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
            (255, 255, 255), 2
        )
        cv2.imshow("Smart Attendance System", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    cap.release()
    cv2.destroyAllWindows()

print(f"\nAttendance saved in: {attendance_file}")
