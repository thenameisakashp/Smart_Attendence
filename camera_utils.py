"""Shared webcam discovery for the desktop and class attendance workflows."""


def diagnose_camera(cv2, preferred_index=0, max_index=3):
    """Quickly check if a camera is available without fully opening it.

    Returns (found_index, backend_name) or raises RuntimeError with helpful diagnostics.
    """
    if cv2 is None:
        raise RuntimeError("OpenCV is required to use the webcam.")

    diagnostics = []

    if __import__("os").name == "nt":
        backends = [
            (getattr(cv2, "CAP_DSHOW", None), "DirectShow"),
            (getattr(cv2, "CAP_MSMF", None), "Media Foundation"),
            (getattr(cv2, "CAP_V4L2", None), "V4L2 (Linux)"),
            (getattr(cv2, "CAP_FFMPEG", None), "FFmpeg"),
            (getattr(cv2, "CAP_ANY", 0), "automatic"),
        ]
        # Filter out None backends (not available on this build)
        backends = [(b, n) for b, n in backends if b is not None]
    else:
        backends = [(getattr(cv2, "CAP_V4L2", 0), "V4L2"),
                    (getattr(cv2, "CAP_ANY", 0), "automatic")]

    for index in range(max(0, preferred_index), max_index + 1):
        for backend, backend_name in backends:
            try:
                cap = cv2.VideoCapture(index, backend)
                if cap.isOpened():
                    # Do a non-blocking read test
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    ok, frame = cap.read()
                    cap.release()
                    if ok and frame is not None:
                        try:
                            h, w = frame.shape[:2]
                            if h > 0 and w > 0:
                                return index, backend_name
                        except (ValueError, AttributeError):
                            pass
            except Exception as e:
                diagnostics.append(f"device {index} ({backend_name}) error: {e}")

    return None, None


def open_camera(cv2, preferred_index=0, max_index=3, allow_fallback=True):
    """Open and verify a webcam, trying common Windows backends and indexes.

    Args:
        cv2: The OpenCV module
        preferred_index: Preferred camera index to try first
        max_index: Maximum camera index to check
        allow_fallback: If True, try alternate indexes when preferred fails

    Returns:
        cv2.VideoCapture object

    Raises:
        RuntimeError with detailed diagnostics if no camera is found
    """
    if cv2 is None:
        raise RuntimeError("OpenCV is required to use the webcam.")

    errors = []

    if __import__("os").name == "nt":
        backends = [
            (getattr(cv2, "CAP_DSHOW", None), "DirectShow"),
            (getattr(cv2, "CAP_MSMF", None), "Media Foundation"),
            (getattr(cv2, "CAP_ANY", 0), "automatic"),
        ]
    else:
        backends = [(getattr(cv2, "CAP_ANY", 0), "automatic")]

    # Try preferred index first, then fall back to others if specified
    indexes_to_try = list(range(max(0, preferred_index), max_index + 1))
    if not allow_fallback:
        indexes_to_try = [preferred_index]
    elif preferred_index > 0:
        indexes_to_try = list(range(0, max_index + 1))

    for index in indexes_to_try:
        for backend, backend_name in backends:
            if backend is None:
                continue
            try:
                cap = cv2.VideoCapture(index, backend)
                if cap.isOpened():
                    # Verify it returns actual frames
                    ok, _frame = cap.read()
                    if ok:
                        try:
                            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        except (AttributeError, cv2.error):
                            pass
                        print(f"Webcam opened: device {index} ({backend_name}).")
                        return cap
                    else:
                        errors.append(f"device {index} ({backend_name}) opened but returned no valid frames")
                    cap.release()
                else:
                    errors.append(f"device {index} ({backend_name}) unavailable")
            except Exception as e:
                errors.append(f"device {index} ({backend_name}) exception: {e}")

    # Build detailed diagnostic message
    error_parts = ["; ".join(errors)]

    # Add helpful suggestions
    suggestions = [
        "Connect or enable a camera,",
        "allow camera access for desktop apps in Windows Settings → Privacy & security → Camera,",
        "and close other apps using it.",
        "Ensure the Face Landmarker model is available at models/face_landmarker.task"
    ]

    raise RuntimeError("No working webcam was found " + " ".join(suggestions) + f"\n\nDetails: {error_parts[0]}")


def test_camera_health(cv2, cap, num_frames=5):
    """Test if a camera is healthy and returning consistent frames.

    Returns:
        (healthy, message) tuple
    """
    if cap is None or not cap.isOpened():
        return False, "Camera is not open"

    try:
        frames = []
        for _ in range(num_frames):
            ok, frame = cap.read()
            if not ok or frame is None:
                return False, "Camera returned no frame"
            frames.append(frame)

        # Check frame consistency (no major jumps)
        heights = [f.shape[0] for f in frames]
        widths = [f.shape[1] for f in frames]
        if len(set(heights)) > 1 or len(set(widths)) > 1:
            return False, "Frame dimensions inconsistent"

        return True, f"Camera healthy ({frames[0].shape[1]}x{heights[0]})"
    except Exception as e:
        return False, f"Camera error: {e}"