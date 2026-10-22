import cv2

camera = cv2.VideoCapture(0)

if not camera.isOpened():
    print("Error: Could not access the webcam.")
    raise SystemExit

print("Webcam started. Press Q to quit.")

while True:
    success, frame = camera.read()

    if not success:
        print("Error: Could not read the camera frame.")
        break

    cv2.imshow("Smart Attendance - Camera Test", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

camera.release()
cv2.destroyAllWindows()