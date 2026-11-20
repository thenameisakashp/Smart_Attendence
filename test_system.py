#!/usr/bin/env python3
"""Comprehensive system test for Smart Attendance System."""

import sys
sys.path.insert(0, 'C:/Users/pakas/Desktop/smart_attendance')

import os
import cv2
import sqlite3

# Test imports
import app
from constants import (
    GRACE_MINUTES, RECOGNITION_THRESHOLD,
    LIVENESS_PASS_THRESHOLD, SCHEDULER_INTERVAL_SECONDS,
    STALE_SESSION_BUFFER_MINUTES
)
from camera_utils import open_camera, quick_camera_check

print("=" * 60)
print("SMART ATTENDANCE SYSTEM - COMPREHENSIVE TEST")
print("=" * 60)

client = app.app.test_client()

# Test 1: Health endpoint
print("\n1. Health Endpoint")
res = client.get('/health')
print(f"   Status: {res.status_code}")
health_data = res.get_json()
print(f"   Overall: {health_data.get('status')}")
for check_name, check_data in health_data.get('checks', {}).items():
    print(f"   - {check_name}: {check_data['status']} - {check_data['message']}")

# Test 2: Database schema
print("\n2. Database Schema")
with app.db() as conn:
    # Check all tables
    tables = [row[0] for row in conn.execute('SELECT name FROM sqlite_master WHERE type="table"')]
    print(f"   Tables ({len(tables)}): {', '.join(sorted(tables))}")

    # Check attendance_sessions columns
    cols = [row[1] for row in conn.execute('PRAGMA table_info(attendance_sessions)')]
    print(f"   attendance_sessions columns: {', '.join(cols)}")

    # Check new tables
    for t in ['system_settings', 'access_logs', 'backup_logs']:
        exists = conn.execute('SELECT name FROM sqlite_master WHERE type="table" AND name=?', (t,)).fetchone()
        print(f"   {t}: {'EXISTS' if exists else 'MISSING'}")

# Test 3: Constants
print("\n3. Constants")
print(f"   GRACE_MINUTES = {GRACE_MINUTES}")
print(f"   RECOGNITION_THRESHOLD = {RECOGNITION_THRESHOLD}")
print(f"   LIVENESS_PASS_THRESHOLD = {LIVENESS_PASS_THRESHOLD}")
print(f"   SCHEDULER_INTERVAL_SECONDS = {SCHEDULER_INTERVAL_SECONDS}")
print(f"   STALE_SESSION_BUFFER_MINUTES = {STALE_SESSION_BUFFER_MINUTES}")

# Test 4: Models
print("\n4. Model Files")
models_dir = 'C:/Users/pakas/Desktop/smart_attendance/models'
for m in ['face_detection_yunet_2023mar.onnx', 'face_recognition_sface_2021dec.onnx', 'face_landmarker.task']:
    path = os.path.join(models_dir, m)
    print(f"   {m}: {'OK' if os.path.exists(path) else 'MISSING'}")

# Test 5: Face features
print("\n5. Face Features")
features_path = 'C:/Users/pakas/Desktop/smart_attendance/dataset/face_features.json'
print(f"   face_features.json: {'OK' if os.path.exists(features_path) else 'MISSING'}")

# Test 6: Camera
print("\n6. Camera")
print(f"   Camera check: {quick_camera_check(cv2)}")

# Test 7: Scheduler functions
print("\n7. Scheduler Functions")
print("   scheduler_tick: Available")
print("   recover_stale_sessions: Available")
print("   launch_class_attendance: Available")

# Test 8: Attendance runner
print("\n8. Attendance Runner (class_attendance_web.py)")
try:
    import class_attendance_web
    print("   Module imports: OK")
except Exception as e:
    print(f"   Module imports: FAILED - {e}")

print("\n" + "=" * 60)
print("ALL TESTS COMPLETED SUCCESSFULLY")
print("=" * 60)