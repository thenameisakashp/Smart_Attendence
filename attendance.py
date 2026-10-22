import cv2
import numpy as np
import os
import json
import csv
from datetime import datetime

# ---------------- CONFIGURATION ----------------

DETECTOR_MODEL = "models/face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = "models/face_recognition_sface_2021dec.onnx"

FEATURES_FILE = "dataset/face_features.json"
ATTENDANCE_DIR = "attendance_records"

# SFace cosine similarity threshold.
# Higher means stricter matching. Tune using real test results.
RECOGNITION_THRESHOLD = 0.45

os.makedirs(ATTENDANCE_DIR, exist_ok=True)

# ---------------- LOAD STUDENT DATABASE ----------------

if not os.path.exists(FEATURES_FILE):
    raise FileNotFoundError(
        f"{FEATURES_FILE} not found. Register students first."
    )

with open(FEATURES_FILE, "r", encoding="utf-8") as file:
    students = json.load(file)

if not students:
    raise ValueError("No registered students found.")

# ---------------- INITIALIZE MODELS ----------------

detector = cv2.FaceDetectorYN_create(
    DETECTOR_MODEL, "", (320, 320),
    0.8, 0.3, 5000
)

recognizer = cv2.FaceRecognizerSF_create(
    RECOGNIZER_MODEL, ""
)

# ---------------- ATTENDANCE FILE ----------------

today = datetime.now().strftime("%Y-%m-%d")
attendance_file = os.path.join(
    ATTENDANCE_DIR, f"attendance_{today}.csv"
)

if not os.path.exists(attendance_file):
    with open(attendance_file, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([
            "Roll Number", "Name", "Date", "Time", "Status"
        ])

# Read existing attendance so a student isn't marked twice today.
marked_students = set()

with open(attendance_file, "r", newline="", encoding="utf-8") as file:
    reader = csv.DictReader(file)
    for row in reader:
        if row.get("Roll Number"):
            marked_students.add(row["Roll Number"])

# ---------------- RECOGNITION LOOP ----------------

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    raise RuntimeError("Could not open webcam. Check camera permissions.")

print("Attendance system started.")
print("Press Q to quit.")

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

                # Compare the live feature against every registered student.
                for roll_no, student in students.items():
                    stored_feature = np.asarray(
                        student["feature"], dtype=np.float32
                    ).reshape(1, -1)

                    score = recognizer.match(
                        feature, stored_feature,
                        cv2.FaceRecognizerSF_FR_COSINE
                    )

                    if score > best_score:
                        best_score = score
                        best_roll = roll_no

                x, y, w, h = map(int, face[:4])

                if best_roll is not None and best_score >= RECOGNITION_THRESHOLD:
                    student_name = students[best_roll]["name"]

                    if best_roll not in marked_students:
                        current = datetime.now()
                        with open(
                            attendance_file, "a",
                            newline="", encoding="utf-8"
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

                    label = f"{student_name} ({best_roll}) {best_score:.2f}"
                    box_color = (0, 255, 0)

                else:
                    label = f"Unknown Student ({best_score:.2f})"
                    box_color = (0, 0, 255)

                cv2.rectangle(
                    frame, (x, y), (x + w, y + h), box_color, 2
                )

                cv2.putText(
                    frame, label,
                    (x, max(y - 10, 25)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, box_color, 2
                )

        cv2.imshow("Smart Attendance System", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    cap.release()
    cv2.destroyAllWindows()

print(f"Attendance saved in: {attendance_file}")