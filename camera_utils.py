"""Shared webcam discovery for the desktop and class attendance workflows."""


def open_camera(cv2, preferred_index=0, max_index=3):
    """Open and verify a webcam, trying common Windows backends and indexes."""
    if cv2 is None:
        raise RuntimeError("OpenCV is required to use the webcam.")

    if __import__("os").name == "nt":
        backends = [
            (getattr(cv2, "CAP_DSHOW", None), "DirectShow"),
            (getattr(cv2, "CAP_MSMF", None), "Media Foundation"),
            (getattr(cv2, "CAP_ANY", 0), "automatic"),
        ]
    else:
        backends = [(getattr(cv2, "CAP_ANY", 0), "automatic")]

    indexes = list(range(max(0, preferred_index), max_index + 1))
    errors = []
    for index in indexes:
        for backend, backend_name in backends:
            if backend is None:
                continue
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except (AttributeError, cv2.error):
                    pass
                ok, _frame = cap.read()
                if ok:
                    print(f"Webcam opened: device {index} ({backend_name}).")
                    return cap
                errors.append(f"device {index} ({backend_name}) opened but returned no frames")
            else:
                errors.append(f"device {index} ({backend_name}) unavailable")
            cap.release()

    details = "; ".join(errors)
    raise RuntimeError(
        "No working webcam was found (checked device indexes "
        f"{preferred_index}–{max_index}). Connect or enable a camera, allow "
        "camera access for desktop apps in Windows Settings, and close other "
        f"apps using it. Details: {details}"
    )
