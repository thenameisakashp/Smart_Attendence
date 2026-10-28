"""Class-scoped YuNet/SFace attendance runner used by the Flask dashboard.

It reads the dashboard's class membership and attendance tables, while face
embeddings continue to come from the existing dataset/face_features.json.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager
from camera_utils import open_camera
from liveness import LivenessChallenge

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset"
DB_PATH = DATASET_DIR / "dashboard_admin.db"
FEATURES_PATH = DATASET_DIR / "face_features.json"
DETECTOR_PATH = BASE_DIR / "models" / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_PATH = BASE_DIR / "models" / "face_recognition_sface_2021dec.onnx"
RECOGNITION_THRESHOLD = 0.45
GRACE_MINUTES = 30


@contextmanager
def connect():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Dashboard database not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_session(session_id: int, status: str = "completed") -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("UPDATE attendance_sessions SET status=?, completed_at=?, process_id=NULL WHERE id=? AND status!='completed'",
                     (status, now, session_id))


def main(session_id: int, class_id: int) -> int:
    if not FEATURES_PATH.is_file():
        raise FileNotFoundError(f"Face feature file not found: {FEATURES_PATH}")
    if not DETECTOR_PATH.is_file() or not RECOGNIZER_PATH.is_file():
        raise FileNotFoundError(
            "YuNet/SFace ONNX models are missing. Expected:\n"
            f"{DETECTOR_PATH}\n{RECOGNIZER_PATH}"
        )

    with connect() as conn:
        session_row = conn.execute("""SELECT a.*,c.class_code,s.name AS subject_name
            FROM attendance_sessions a JOIN classes c ON c.id=a.class_id
            JOIN subjects s ON s.id=a.subject_id WHERE a.id=? AND a.class_id=?""",
            (session_id, class_id)).fetchone()
        if not session_row:
            raise ValueError("Attendance session/class combination was not found.")
        assigned = conn.execute("SELECT roll FROM class_students WHERE class_id=?", (class_id,)).fetchall()
        allowed_rolls = {str(row["roll"]).strip() for row in assigned}

    features_data = json.loads(FEATURES_PATH.read_text(encoding="utf-8"))
    students = {}
    for roll in allowed_rolls:
        item = features_data.get(roll)
        if not isinstance(item, dict) or item.get("feature") is None:
            continue
        try:
            vector = np.asarray(item["feature"], dtype=np.float32).reshape(1, -1)
        except (TypeError, ValueError):
            continue
        if vector.size:
            students[roll] = {"name": str(item.get("name") or item.get("full_name") or roll), "feature": vector}
    if not students:
        raise ValueError("No assigned students have face embeddings. Register their faces with the existing Tkinter Student Manager, then assign them to this class.")

    detector = cv2.FaceDetectorYN_create(str(DETECTOR_PATH), "", (320, 320), 0.8, 0.3, 5000)
    recognizer = cv2.FaceRecognizerSF_create(str(RECOGNIZER_PATH), "")
    cap = open_camera(cv2)

    scheduled_end = datetime.strptime(f"{session_row['session_date']} {session_row['end_time']}", "%Y-%m-%d %H:%M")
    hard_deadline = scheduled_end + timedelta(minutes=GRACE_MINUTES)
    if hard_deadline <= datetime.now():
        # For a session launched late by the scheduler, give the user a useful
        # live window rather than immediately closing an already-passed slot.
        hard_deadline = datetime.now() + timedelta(minutes=5)

    with connect() as conn:
        conn.execute("UPDATE attendance_sessions SET status='running', started_at=COALESCE(started_at,?) WHERE id=?",
                     (datetime.now().isoformat(timespec="seconds"), session_id))

    window_name = f"Smart Attendance — {session_row['class_code']} — {session_row['subject_name']}"
    print(f"Session #{session_id}: {session_row['class_code']} / {session_row['subject_name']}")
    print(f"Assigned students with face features: {len(students)}. Press Q or ESC to finish.")
    seen_this_run = set()
    liveness_checks = {}
    status_message = "Attendance running"
    try:
        while datetime.now() <= hard_deadline:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            height, width = frame.shape[:2]
            detector.setInputSize((width, height))
            _, faces = detector.detect(frame)
            if faces is not None:
                for face in faces:
                    x, y, w, h = map(int, face[:4])
                    label, color = "Unknown", (0, 0, 255)
                    try:
                        aligned = recognizer.alignCrop(frame, face)
                        live_feature = recognizer.feature(aligned)
                        best_roll, best_score = None, -1.0
                        for roll, record in students.items():
                            score = recognizer.match(live_feature, record["feature"], cv2.FaceRecognizerSF_FR_COSINE)
                            if score > best_score:
                                best_roll, best_score = roll, float(score)
                        if best_roll is not None and best_score >= RECOGNITION_THRESHOLD:
                            record = students[best_roll]
                            challenge = liveness_checks.setdefault(
                                best_roll, LivenessChallenge()
                            )
                            live, prompt = challenge.update(frame, face)
                            if not live:
                                label, color = f"{record['name']}: {prompt}", (0, 165, 255)
                            else:
                              with connect() as conn:
                                # Revalidate membership at marking time and enforce one record per session/roll.
                                member = conn.execute("SELECT 1 FROM class_students WHERE class_id=? AND roll=?",
                                                      (class_id, best_roll)).fetchone()
                                if member:
                                    try:
                                        conn.execute("INSERT INTO attendance_records(session_id,roll,student_name,status,marked_at) VALUES(?,?,?,?,?)",
                                                     (session_id, best_roll, record["name"], "Present", datetime.now().isoformat(timespec="seconds")))
                                        seen_this_run.add(best_roll)
                                        status_message = f"Marked present: {record['name']}"
                                        label, color = f"{record['name']} {best_score:.2f}", (0, 200, 0)
                                    except sqlite3.IntegrityError:
                                        label, color = f"{record['name']} (already marked)", (0, 220, 220)
                                else:
                                    label = "Not assigned to this class"
                    except Exception as exc:
                        label = f"Recognition error: {type(exc).__name__}"
                    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                    cv2.putText(frame, label[:70], (x, max(22, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            with connect() as conn:
                present_count = conn.execute("SELECT COUNT(*) FROM attendance_records WHERE session_id=? AND status IN ('Present','Late')", (session_id,)).fetchone()[0]
            remaining = max(0, int((hard_deadline - datetime.now()).total_seconds()))
            cv2.putText(frame, f"{session_row['class_code']} | {session_row['subject_name']} | Present: {present_count}/{len(allowed_rolls)}",
                        (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
            cv2.putText(frame, f"{status_message} | Closes in {remaining // 60}m {remaining % 60}s | Q to finish",
                        (12, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.imshow(window_name, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord('q'), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
        finish_session(session_id)
    print(f"Session #{session_id} completed. Newly marked this run: {len(seen_this_run)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--class-id", type=int, required=True)
    args = parser.parse_args()
    try:
        raise SystemExit(main(args.session_id, args.class_id))
    except Exception as exc:
        print(f"ATTENDANCE ERROR: {exc}")
        try:
            finish_session(args.session_id, "failed")
        except Exception:
            pass
        raise SystemExit(1)
