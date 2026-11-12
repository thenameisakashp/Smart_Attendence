"""Shared constants for the Smart Attendance System."""

# Scheduler catch-up grace period; must match the window used by class_attendance_web.py.
GRACE_MINUTES = 30

# Face recognition threshold (cosine similarity)
RECOGNITION_THRESHOLD = 0.45

# Liveness passing score (0-100)
LIVENESS_PASS_THRESHOLD = 65

# Scheduler check interval (seconds)
SCHEDULER_INTERVAL_SECONDS = 15

# Stale session recovery buffer (minutes beyond grace period)
STALE_SESSION_BUFFER_MINUTES = 5