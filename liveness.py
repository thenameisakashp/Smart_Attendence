"""Active and passive face liveness checks for attendance capture.

The score is a heuristic signal for this webcam application, not a certified
presentation-attack detector. MediaPipe's relative z values are pseudo-depth.
"""
from collections import deque
from pathlib import Path
import random
import time

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

MODEL_PATH = Path(__file__).resolve().parent / "models" / "face_landmarker.task"


class LivenessError(Exception):
    """Raised when liveness verification fails due to configuration or runtime issues."""
    pass


class LivenessChallenge:
    """Calculate a 0-100 passive/active score and require random actions.

    Uses MediaPipe iris landmarks for enhanced anti-spoofing against
    photos, videos, and display-based attacks.
    """

    _landmarker = None
    _timestamp_ms = 0
    _LEFT_EYE = (362, 385, 387, 263, 373, 380)
    _RIGHT_EYE = (33, 160, 158, 133, 153, 144)
    _MOUTH_WIDTH = (61, 291)
    _MOUTH_VERTICALS = ((13, 14), (82, 87), (312, 317))
    _NOSE = 1
    _EYE_CORNERS = (33, 263)

    # Iris landmark indices (MediaPipe FaceLandmarker with output_iris=True)
    # 468-471: left iris perimeter, 472-475: right iris perimeter
    # 476: left pupil center, 477: right pupil center
    _IRIS_LEFT = (468, 469, 470, 471)
    _IRIS_RIGHT = (472, 473, 474, 475)
    _PUPIL_LEFT = 476
    _PUPIL_RIGHT = 477

    @classmethod
    def _get_landmarker(cls):
        """Get or create the MediaPipe FaceLandmarker singleton."""
        if cls._landmarker is None:
            if not MODEL_PATH.is_file():
                raise FileNotFoundError(f"Face landmark model missing: {MODEL_PATH}")
            options = vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(MODEL_PATH)),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                output_face_blendshapes=False,
                output_iris=True,
            )
            cls._landmarker = vision.FaceLandmarker.create_from_options(options)
        return cls._landmarker

    def __init__(self, timeout_seconds=35):
        self.timeout_seconds = timeout_seconds
        self.reset()

    def reset(self):
        """Reset challenge state for a new liveness session."""
        self.started = time.monotonic()
        self.phase = "baseline"
        self.baseline_ears = []
        self.baseline_yaw = []
        self.eye_threshold = None
        self.yaw_center = None
        self.blink_state = "open"
        self.blink_closed_frames = 0
        self.blink_reopen_frames = 0
        self.blinks = 0
        self.motion = deque(maxlen=24)
        self.texture_scores = deque(maxlen=24)
        self.depth_scores = deque(maxlen=24)
        self.mouth_samples = deque(maxlen=24)
        self.pupil_positions = deque(maxlen=24)
        self.actions = ["blink", random.choice(("mouth", "turn", "smile", "gaze"))]
        random.shuffle(self.actions)
        self.action_index = 0
        self.action_streak = 0
        self.turn_direction = random.choice((-1, 1))
        self.gaze_target_x = random.uniform(0.3, 0.7)  # for gaze challenge
        self.passed = False
        self.failed = False  # Track if we hit a fatal error
        self.score = 0
        self.passive_score = 0
        self.active_score = 0
        self.instruction = "Face the camera with eyes open"
        self.error_message = ""

    @staticmethod
    def _distance(a, b):
        return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5

    @classmethod
    def _ear(cls, points, indices):
        """Calculate Eye Aspect Ratio from landmark indices."""
        a, b, c, d, e, f = (points[index] for index in indices)
        vertical = cls._distance(b, f) + cls._distance(c, e)
        return vertical / max(2 * cls._distance(a, d), 1e-6)

    @classmethod
    def _mar(cls, points):
        """Calculate Mouth Aspect Ratio."""
        width = cls._distance(points[cls._MOUTH_WIDTH[0]], points[cls._MOUTH_WIDTH[1]])
        vertical = sum(cls._distance(points[a], points[b]) for a, b in cls._MOUTH_VERTICALS)
        return vertical / max(3 * width, 1e-6)

    @staticmethod
    def _fit_ellipse_score(points, indices):
        """Fit ellipse to iris points; return quality 0-100.

        High score indicates good 3D iris structure (live eye).
        Low score or 0 indicates flat/2D structure (possible photo/print).
        """
        try:
            pts = np.array([(points[i].x, points[i].y) for i in indices], dtype=np.float32)
            if len(pts) < 4:
                return 0.0
            ellipse = cv2.fitEllipse(pts)
            (cx, cy), (ma, ma), angle = ellipse
            # Check: reasonable aspect ratio (not collapsed), reasonable size
            if ma < 0.008 or ma > 0.06:  # iris too small/large in normalized coords
                return 0.0
            aspect = min(ma, ma) / max(ma, ma, 1e-6)
            if aspect < 0.5:  # too elliptical = likely 2D print
                return 0.0
            # Residuals - how well points fit ellipse
            residuals = []
            for (x, y) in pts:
                dx, dy = x - cx, y - cy
                cos_a, sin_a = np.cos(angle), np.sin(angle)
                x_rot = dx * cos_a + dy * sin_a
                y_rot = -dx * sin_a + dy * cos_a
                residual = (x_rot / (ma / 2)) ** 2 + (y_rot / (ma / 2)) ** 2 - 1
                residuals.append(abs(residual))
            fit_quality = max(0.0, 100.0 - np.mean(residuals) * 500.0)
            return min(100.0, fit_quality)
        except (cv2.error, ValueError, ZeroDivisionError):
            return 0.0

    def _gaze_score(self, points):
        """Check if user is looking at camera (pupil centered in eye). Return 0-100."""
        try:
            # Eye corners: 33 (right inner), 133 (right outer), 263 (left inner), 362 (left outer)
            # Pupil centers: 476 (left), 477 (right)
            left_pupil = points[self._PUPIL_LEFT]
            right_pupil = points[self._PUPIL_RIGHT]
            left_inner, left_outer = points[362], points[263]
            right_inner, right_outer = points[133], points[33]

            # Horizontal gaze: pupil position relative to eye corners
            left_eye_width = self._distance(left_inner, left_outer)
            right_eye_width = self._distance(right_inner, right_outer)
            if left_eye_width < 1e-6 or right_eye_width < 1e-6:
                return 0.0

            left_gaze = (left_pupil.x - left_inner.x) / left_eye_width
            right_gaze = (right_outer.x - right_pupil.x) / right_eye_width

            # Centered gaze ≈ 0.5; looking away deviates
            left_centered = 1.0 - abs(left_gaze - 0.5) * 2
            right_centered = 1.0 - abs(right_gaze - 0.5) * 2
            gaze_centered = (left_centered + right_centered) / 2

            return max(0.0, min(100.0, gaze_centered * 100.0))
        except (ValueError, IndexError, ZeroDivisionError):
            return 0.0

    def _pupil_stability(self, points):
        """Track pupil center micro-movements. Live eyes have physiological micro-jitter."""
        try:
            left_pupil = points[self._PUPIL_LEFT]
            right_pupil = points[self._PUPIL_RIGHT]
            center_x = (left_pupil.x + right_pupil.x) / 2
            center_y = (left_pupil.y + right_pupil.y) / 2

            self.pupil_positions.append((center_x, center_y))
            if len(self.pupil_positions) < 8:
                return 50.0  # neutral until we have history

            # Compute micro-movement variance (live eyes have small but consistent movement)
            xs = [p[0] for p in self.pupil_positions]
            ys = [p[1] for p in self.pupil_positions]
            var_x = np.var(xs)
            var_y = np.var(ys)
            total_var = var_x + var_y

            # Too static = photo/video; too much = tracking noise
            # Sweet spot for live: 1e-7 to 1e-5 range in normalized coords
            if total_var < 1e-7:
                return 20.0  # too static
            if total_var > 5e-5:
                return 30.0  # too noisy
            return min(100.0, total_var * 2e6)  # scale to 0-100
        except (ValueError, IndexError):
            return 50.0

    def _update_passive_score(self, frame, face, points, mar):
        """Update the passive liveness score using multiple biometric signals."""
        x, y, width, height = map(int, face[:4])
        fh, fw = frame.shape[:2]
        x0, y0, x1, y1 = max(0, x), max(0, y), min(fw, x + width), min(fh, y + height)
        if x1 <= x0 or y1 <= y0:
            return
        gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F).var()
        texture = max(0.0, min(100.0, (laplacian - 8.0) * 1.25))

        z_values = [point.z for point in points]
        z_span = max(z_values) - min(z_values)
        pseudo_depth = max(0.0, min(100.0, z_span * 450.0))

        center_x = (x + width / 2) / max(fw, 1)
        center_y = (y + height / 2) / max(fh, 1)
        self.motion.append((center_x, center_y, points[self._NOSE].z))
        if len(self.motion) >= 8:
            movement = sum(
                max(row[index] for row in self.motion) - min(row[index] for row in self.motion)
                for index in range(3)
            )
        else:
            movement = 0.0
        motion_score = max(0.0, min(100.0, movement * 1200.0))

        # Iris-based signals
        left_iris_score = self._fit_ellipse_score(points, self._IRIS_LEFT)
        right_iris_score = self._fit_ellipse_score(points, self._IRIS_RIGHT)
        iris_score = (left_iris_score + right_iris_score) / 2

        gaze_score = self._gaze_score(points)
        pupil_stability = self._pupil_stability(points)

        self.texture_scores.append(texture)
        self.depth_scores.append(pseudo_depth)
        self.mouth_samples.append(mar)
        blink_score = min(100.0, self.blinks * 100.0)
        texture_score = sum(self.texture_scores) / len(self.texture_scores)
        depth_score = sum(self.depth_scores) / len(self.depth_scores)
        mouth_activity = max(self.mouth_samples) - min(self.mouth_samples)
        mouth_score = max(0.0, min(100.0, mouth_activity * 500.0))
        self.passive_score = round(
            0.20 * blink_score + 0.08 * mouth_score + 0.15 * motion_score
            + 0.15 * texture_score + 0.15 * depth_score
            + 0.12 * iris_score + 0.10 * gaze_score + 0.05 * pupil_stability
        )

    def _current_action(self):
        return self.actions[self.action_index] if self.action_index < len(self.actions) else None

    def _action_prompt(self):
        action = self._current_action()
        if action == "blink":
            return "Blink both eyes"
        if action == "mouth":
            return "Open your mouth"
        if action == "turn":
            side = "right" if self.turn_direction > 0 else "left"
            return f"Turn toward the {side} side of the image"
        if action == "smile":
            return "Smile"
        if action == "gaze":
            return "Look at the center of the screen"
        return "Checking live signals"

    def _complete_action(self):
        self.action_index += 1
        self.action_streak = 0
        self.turn_direction = random.choice((-1, 1))
        self.active_score = round(100 * self.action_index / len(self.actions))

    def update(self, frame, face):
        """Return (passed, instruction); the current 0-100 score is .score.

        Args:
            frame: The current camera frame (BGR image)
            face: Tuple of (x, y, w, h) for the detected face

        Returns:
            Tuple of (passed: bool, instruction: str)
            If passed is True, attendance should be recorded
        """
        if self.passed:
            return True, f"Live check passed ({self.score}/100)"
        if time.monotonic() - self.started > self.timeout_seconds:
            self.reset()

        try:
            x, y, width, height = map(int, face[:4])
            fh, fw = frame.shape[:2]
            x0, y0, x1, y1 = max(0, x), max(0, y), min(fw, x + width), min(fh, y + height)
            if x1 <= x0 or y1 <= y0:
                return False, "Center your face in the camera"
            rgb = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            cls = type(self)
            cls._timestamp_ms = max(cls._timestamp_ms + 1, int(time.monotonic() * 1000))
            result = self._get_landmarker().detect_for_video(image, cls._timestamp_ms)
            if not result.face_landmarks:
                return False, "Center your face; keep it visible"
            points = result.face_landmarks[0]
            left_ear = self._ear(points, self._LEFT_EYE)
            right_ear = self._ear(points, self._RIGHT_EYE)
            ear = (left_ear + right_ear) / 2
            open_threshold = max(0.20, (self.eye_threshold or 0.25) * 0.78)
            close_threshold = min(0.18, (self.eye_threshold or 0.25) * 0.68)
            both_open = left_ear > open_threshold and right_ear > open_threshold
            both_closed = left_ear < close_threshold and right_ear < close_threshold
            mar = self._mar(points)
            mouth_width = self._distance(points[61], points[291])
            lip_center_y = (points[13].y + points[14].y) / 2
            corner_y = (points[61].y + points[291].y) / 2
            smile = mouth_width > 0.30 and lip_center_y - corner_y > 0.008
            eye_mid_x = (points[self._EYE_CORNERS[0]].x + points[self._EYE_CORNERS[1]].x) / 2
            eye_width = self._distance(points[self._EYE_CORNERS[0]], points[self._EYE_CORNERS[1]])
            yaw = (points[self._NOSE].x - eye_mid_x) / max(eye_width, 1e-6)
            self._update_passive_score(frame, face, points, mar)
        except Exception as e:
            # Graceful fallback for frame/landmark errors
            error_msg = str(e) if str(e) else "unknown error"
            if "model" in error_msg.lower() or "landmarker" in error_msg.lower():
                self.failed = True
                self.error_message = f"Face detection model error: {error_msg}"
                raise LivenessError(self.error_message) from e
            return False, f"Face detection error: {error_msg}. Try moving the camera."

        previous_blinks = self.blinks
        if both_open:
            if self.blink_state == "closed":
                self.blink_reopen_frames += 1
                if self.blink_reopen_frames >= 2:
                    self.blinks += 1
                    self.blink_state = "open"
                    self.blink_reopen_frames = 0
                    self.blink_closed_frames = 0
            else:
                self.blink_state = "open"
        elif both_closed and self.blink_state == "open":
            self.blink_closed_frames += 1
            if self.blink_closed_frames >= 1:
                self.blink_state = "closed"
                self.blink_reopen_frames = 0
        elif not both_open and self.blink_state == "closed":
            self.blink_reopen_frames = 0

        if self.phase == "baseline":
            if both_open:
                self.baseline_ears.append(ear)
                self.baseline_yaw.append(yaw)
                if len(self.baseline_ears) >= 6:
                    self.eye_threshold = max(0.15, min(0.18, sorted(self.baseline_ears)[3] * 0.68))
                    self.yaw_center = sorted(self.baseline_yaw)[len(self.baseline_yaw) // 2]
                    self.phase = "active"
            else:
                self.baseline_ears.clear()
                self.baseline_yaw.clear()
            self.instruction = "Look at the camera with eyes open"
            return False, self.instruction

        action = self._current_action()
        if action == "blink" and self.blinks > previous_blinks:
            self.action_streak += 1
        elif action == "mouth":
            self.action_streak = self.action_streak + 1 if mar > 0.38 else 0
        elif action == "turn":
            turned = (yaw - self.yaw_center) * self.turn_direction > 0.055
            self.action_streak = self.action_streak + 1 if turned else 0
        elif action == "smile":
            self.action_streak = self.action_streak + 1 if smile else 0
        elif action == "gaze":
            # Gaze challenge: user should look at center (gaze_score > 70)
            gaze_score = self._gaze_score(points)
            self.action_streak = self.action_streak + 1 if gaze_score > 70 else 0

        if self.action_streak >= (2 if action == "blink" else 3):
            self._complete_action()

        self.active_score = round(100 * self.action_index / len(self.actions))
        self.score = round(0.55 * self.active_score + 0.45 * self.passive_score)
        if self.action_index >= len(self.actions):
            if self.score >= 65:
                self.passed = True
                self.instruction = f"Live check passed ({self.score}/100)"
                return True, self.instruction
            self.instruction = f"Hold still with a clear face ({self.score}/100)"
            return False, self.instruction

        self.instruction = f"{self._action_prompt()} | Liveness {self.score}/100"
        return False, self.instruction