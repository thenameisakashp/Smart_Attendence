import cv2
import numpy as np
import os
import json

# Model paths
DETECTOR_MODEL = "models/face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = "models/face_recognition_sface_2021dec.onnx"

# Dataset path
DATASET_DIR = "dataset/students"
FEATURES_FILE = "dataset/face_features.json"

# Create directories
os.makedirs(DATASET_DIR, exist_ok=True)
os.makedirs("dataset", exist_ok=True)

# Initialize face detector and recognizer
detector = cv2.FaceDetectorYN_create(
    DETECTOR_MODEL, "", (320, 320),
    0.8, 0.3, 5000
)

recognizer = cv2.FaceRecognizerSF_create(
    RECOGNIZER_MODEL, ""
)


def register_student():
    roll_no = input("Enter student roll number: ").strip()
    name = input("Enter student name: ").strip()

    if not roll_no or not name:
        print("Roll number and name cannot be empty.")
        return

    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("Error: Could not open webcam.")
        return

    features = []
    print("\nWebcam opened.")
    print("Look at the camera and move your head slightly.")
    print("Press Q to stop registration.\n")

    while len(features) < 30:
        ret, frame = cap.read()

        if not ret:
            print("Could not read webcam frame.")
            break

        height, width = frame.shape[:2]

        detector.setInputSize((width, height))
        _, faces = detector.detect(frame)

        if faces is not None and len(faces) > 0:
            # Use the largest detected face
            face = max(
                faces,
                key=lambda f: f[2] * f[3]
            )

            aligned_face = recognizer.alignCrop(frame, face)
            feature = recognizer.feature(aligned_face)

            features.append(feature.flatten().tolist())

            x, y, w, h = map(int, face[:4])
            cv2.rectangle(
                frame, (x, y), (x + w, y + h),
                (0, 255, 0), 2
            )

            cv2.putText(
                frame,
                f"Samples: {len(features)}/30",
                (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (0, 255, 0), 2
            )
        else:
            cv2.putText(
                frame, "No face detected",
                (20, 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (0, 0, 255), 2
            )

        cv2.imshow("Student Registration", frame)

        if cv2.waitKey(100) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()

    if len(features) < 5:
        print("Registration cancelled: not enough face samples.")
        return

    # Store the average feature vector
    average_feature = np.mean(
        np.array(features, dtype=np.float32),
        axis=0
    )

    if os.path.exists(FEATURES_FILE):
        with open(FEATURES_FILE, "r", encoding="utf-8") as file:
            database = json.load(file)
    else:
        database = {}

    database[roll_no] = {
        "name": name,
        "feature": average_feature.tolist()
    }

    with open(FEATURES_FILE, "w", encoding="utf-8") as file:
        json.dump(database, file, indent=4)

    print(f"\nStudent registered successfully!")
    print(f"Name: {name}")
    print(f"Roll number: {roll_no}")
    print(f"Samples captured: {len(features)}")


if __name__ == "__main__":
    register_student()