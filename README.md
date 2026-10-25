# Smart Attendance System — Integrated Build

This build keeps the original Python/Tkinter face-registration tool and OpenCV YuNet/SFace models, and connects them to a local Flask dashboard for departments, classes, class membership, subjects, lecturer access, timetable import, class attendance sessions, and reports.

## What is connected

- HOD first-time setup and sign-in; passwords are hashed.
- HOD-managed departments and classes.
- Class-specific student membership. The dashboard opens the existing Tkinter Student Manager for face registration; it does not replace it with a web registration form.
- Before the Tkinter Student Manager opens, web classes are synchronized into its legacy SQLite class selector so the selected class is identifiable there. After capturing a student's face, return to that class's Students page and assign the student to the class.
- Subjects per class and lecturer account/subject assignments.
- Lecturer access is restricted server-side to assigned classes and, when configured, assigned subjects.
- CSV/XLSX timetable import with required-column, time, class matching, and overlap validation.
- Scheduled attendance launch while the local Flask app is running. The scheduler checks every 15 seconds and catches up for timetable entries whose start time was within the last 30 minutes. It creates at most one session per timetable entry/date.
- Class-scoped face recognition in `class_attendance_web.py`. It reads only the class's assigned student roster and the existing `dataset/face_features.json`; it writes records into SQLite and prevents duplicate attendance per student/session.
- Class session reports derive absent/not-marked status from the class roster. The main dashboard can read both legacy daily CSV attendance and the new class/session records.
- Audit entries for key account, assignment, subject, timetable, and attendance actions.

## Important limits

- The uploaded project did not include the YuNet/SFace ONNX model files. You must place both model files in `models/` before camera recognition/registration can run:
  - `models/face_detection_yunet_2023mar.onnx`
  - `models/face_recognition_sface_2021dec.onnx`
- A working webcam is required. Automatic attendance opens a camera window on the computer running `app.py`; the browser itself cannot remotely access that computer's camera through this design.
- The timetable scheduler runs only while the local `app.py` process is running. Keep the computer awake and the app open during class times. If the camera is busy, a model is missing, or the process cannot start, check the terminal and session status.
- The 30-minute window is a scheduler catch-up grace period and the attendance window stays open until the scheduled end plus 30 minutes. The user can finish early with **Q** or **Esc** in the camera window.
- Glasses/cap/accessory detection is **not implemented** by YuNet/SFace alone. Do not treat these models as an accessory classifier.
- The previous standalone daily CSV attendance workflow remains available through `attendance.py`. It is a legacy workflow alongside the new class/session workflow, not the same session database.
- The application is configured for local use at `127.0.0.1:5000`, not public internet deployment. Review security, consent, privacy, access controls, and institutional policies before using real student biometric data.
- Absent status in class reports is currently derived from students assigned to the class who have no Present record for that session; it is not inserted as a separate `Absent` row.

## Setup on Windows

1. Install a supported Python version and ensure the Tkinter component is installed.
2. Open PowerShell in this project folder:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

3. Copy the two ONNX model files into `models/` (see above).
4. Start the dashboard:

   ```powershell
   python app.py
   ```

   Or double-click `start_dashboard.bat` after creating the environment and installing dependencies.
5. Open `http://127.0.0.1:5000/setup` in your browser and create the HOD account. After setup, sign in at `http://127.0.0.1:5000/login`.

## Recommended first-time workflow

1. **Departments & classes:** Create the department, then create a class such as `CSE-4-A`.
2. **Students:** Open the class, select **Students**, and use **Register student / capture face**. The existing Tkinter Student Manager opens on the same computer. Choose the matching mirrored class in its class selector and complete registration.
3. Return to the class Students page, refresh, and assign the registered student to the web class. Repeat for the roster.
4. **Subjects:** Create subjects in the class workspace, or import a timetable that creates/updates subjects.
5. **Lecturers:** Create lecturer accounts and assign them to a class, optionally restricting them to one subject. Revoke an assignment to remove access.
6. **Timetable:** Import CSV/XLSX using these headers exactly (case-insensitive):

   `day,subject_code,subject,start_time,end_time,section,room`

   Example row: `Monday,21CS43,Data Structures,09:00,10:00,CSE-4-A,Room 101`

   The `section` value should match one class code (recommended) or an unambiguous section label. Overlaps for the same class/day are rejected.
7. **Attendance:** Use the class's **Take attendance** page for a manual session, or leave `app.py` running for timetable-triggered sessions. The camera window shows recognition and present counts.
8. **Reports:** Open the class attendance page and select a session report. Review all absences before treating them as official.

## Existing desktop tools retained

- `student_manager.py` — primary Tkinter face registration/student status UI.
- `class_manager.py` — legacy category/course/semester/section management UI.
- `dashboard.py` — original Tkinter dashboard.
- `attendance.py` — standalone daily CSV attendance.
- `class_attendance.py` — original SQLite section-based attendance workflow.
- `class_attendance_web.py` — class-specific attendance launched by the integrated web dashboard.
- `database.py` — original desktop SQLite schema initializer.
- `timetable_manager.py` — original local CSV/XLSX timetable JSON utility.

## Data and backups

Runtime databases and biometric data are created under `dataset/`; daily CSVs are stored in `attendance_records/`. These files are intentionally excluded from source-control defaults. Back up local data securely. Do not upload real student face embeddings, biometric samples, populated databases, or attendance files to a public repository.

## Validation performed for this package

- Python syntax compilation for the updated Flask app and new class attendance runner.
- Jinja parsing for the HTML templates.
- Template endpoint-name consistency check.

A live Flask/camera integration test could not be completed in this environment because Flask is not installed here and the ONNX model files/webcam were not supplied. Install the requirements and test on the target Windows computer before using the system for real attendance.
