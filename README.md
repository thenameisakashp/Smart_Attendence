# Smart Attendance System

A Python-based student attendance management project that uses face
detection and face recognition to help record attendance. It is being
developed for educational use, with class management, student
registration, session-based attendance, and database-backed records.

> **Project status:** Under active development. This README
> distinguishes features represented by the current repository from
> planned enhancements. Test features locally before treating them as
> production-ready.

## Contents

-   [Overview](#overview)
-   [Current Features](#current-features)
-   [Technology Stack](#technology-stack)
-   [Project Structure](#project-structure)
-   [Requirements](#requirements)
-   [Setup](#setup)
-   [Run the Application](#run-the-application)
-   [Data and Privacy](#data-and-privacy)
-   [Development Roadmap](#development-roadmap)

## Overview

The system is designed to support different education categories and
class structures. It uses a webcam to detect faces, creates face
embeddings for registered students, and records attendance for a
selected class session.

The current implementation uses OpenCV's YuNet face detector and SFace
face recognizer. SQLite stores structured student, class, session, and
attendance data. Some workflows also use JSON and CSV files.

## Current Features

The repository contains modules for: - Face detection and face
recognition using YuNet and SFace. - Student registration with face
samples/embeddings. - Student active/inactive management. - Dynamic
education-category, course, academic-level, semester, and section
management. - SQLite database initialization and storage. - Class-wise
attendance sessions. - Session attendance records with a database
uniqueness rule for one student per session. - Daily CSV attendance
output from the standalone attendance workflow. - Timetable
import/management utilities. - Tkinter-based management interfaces.

## Technology Stack

-   **Language:** Python
-   **Computer vision:** OpenCV
-   **Face detection:** YuNet ONNX model
-   **Face recognition:** SFace ONNX model
-   **Database:** SQLite
-   **Data files:** JSON and CSV
-   **User interface:** Tkinter
-   **Development environment:** Windows / VS Code (other platforms may
    require adjustments)

## Project Structure

``` text
Smart_Attendence/
├── attendance.py                 # Standalone/daily attendance workflow
├── camera_test.py                # Camera test utility
├── check_database.py             # Database inspection utility
├── check_sessions.py             # Attendance-session inspection utility
├── class_attendance.py           # Class-wise, session-based attendance
├── class_manager.py              # Class/category/course/section management
├── dashboard.py                  # Dashboard interface
├── database.py                   # SQLite connection and schema setup
├── debug_students.py             # Student/face-data debugging utility
├── face_detection.py             # Face detection utility
├── register_student.py           # Standalone student face registration
├── student_manager.py            # Student registration and status management
├── timetable_manager.py          # Timetable import/management
├── sample_timetable.csv          # Example timetable input
├── dataset/                      # Local database and face/timetable data
├── models/
│   ├── face_detection_yunet_2023mar.onnx
│   └── face_recognition_sface_2021dec.onnx
└── attendance_records/           # Generated daily attendance CSV files
```

Some data files and databases are generated at runtime and may not exist
in a fresh clone.

## Requirements

-   Python compatible with the installed OpenCV build.
-   A working webcam.
-   The two ONNX model files listed above.
-   Windows users can run the commands below in PowerShell.
-   Required packages include OpenCV and NumPy. Install any additional
    packages imported by the script you plan to run.

## Setup

### 1. Clone the repository

``` powershell
git clone https://github.com/thenameisakashp/Smart_Attendence.git
cd Smart_Attendence
```

### 2. Create and activate a virtual environment

``` powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install core dependencies

``` powershell
python -m pip install --upgrade pip
python -m pip install opencv-contrib-python numpy
```

If a script reports another missing module, install it into the same
virtual environment. Avoid installing conflicting OpenCV packages in one
environment.

### 4. Check model files

Ensure these files exist:

``` text
models/face_detection_yunet_2023mar.onnx
models/face_recognition_sface_2021dec.onnx
```

## Run the Application

Run commands from the project root with the virtual environment
activated.

### Initialize/check the database

``` powershell
python database.py
```

### Open student management

``` powershell
python student_manager.py
```

### Open class management

``` powershell
python class_manager.py
```

### Run class-wise attendance

``` powershell
python class_attendance.py
```

Select the class/section, enter the subject, and start the attendance
session. Finish the session using the application's stop/finish control.

### Run standalone attendance

``` powershell
python attendance.py
```

### Inspect saved sessions

``` powershell
python check_sessions.py
```

### Test the camera

``` powershell
python camera_test.py
```

The standalone daily CSV workflow and class-wise SQLite session workflow
are separate paths in the current project.

## Data and Privacy

This project processes biometric face embeddings and student attendance
information. Treat these as sensitive data.

-   Do not commit real student face embeddings, biometric samples,
    attendance records, or a populated local database to a public
    repository.
-   Keep passwords, API keys, and other secrets out of source control.
-   Use synthetic/test records when demonstrating the project publicly.
-   Restrict access to folders containing student data.
-   Obtain appropriate consent and institutional approval before
    collecting or processing students' biometric data.
-   Face recognition and accessory detection can make mistakes; provide
    a manual review or correction process.

Inspect repository contents before pushing. Add suitable local-data
entries to `.gitignore` and remove sensitive files from tracking where
necessary. Deleting a file in a later commit does not erase it from
earlier Git history.

## Development Roadmap

The items below are **planned**, not claims that the functionality is
already implemented.

### Priority 1 --- Registration integrity and duplicate prevention

-   [ ] Compare a new face embedding with existing registered embeddings
    before saving.
-   [ ] Check both active and inactive student records.
-   [ ] Flag likely duplicate faces across different roll numbers for
    administrator review.
-   [ ] Validate similarity thresholds with test cases; a similarity
    score is not proof of identity.

### Priority 2 --- Strict appearance and face-quality checks

-   [ ] Evaluate a dedicated model for glasses, caps/hats, and face
    coverings.
-   [ ] Apply checks during both registration and attendance.
-   [ ] Reject or request another capture when a prohibited accessory is
    detected or detection is uncertain.
-   [ ] Require exactly one visible face and assess blur, face size, and
    alignment.
-   [ ] Test false positives and false negatives before relying on the
    checks.

**Important:** YuNet and SFace do not, by themselves, guarantee reliable
glasses/cap classification. A separate detector must be selected,
integrated, and validated before this feature is considered complete.

### Priority 3 --- Attendance completion rules

-   [ ] At session completion, mark eligible students without a present
    record as absent.
-   [ ] Define how late arrivals and students who join after the session
    starts are handled.
-   [ ] Keep records tied to the correct class, subject, date, and
    session.

### Priority 4 --- Reports and usability

-   [ ] Daily, weekly, monthly, class-wise, and student-wise reports.
-   [ ] Export session reports to CSV/Excel and PDF.
-   [ ] Improve error messages, logging, and camera failure handling.
-   [ ] Add tests for database operations and duplicate prevention.

### Priority 5 --- Security and reliability

-   [ ] Add administrator authentication and role-based access where
    required.
-   [ ] Add a manual review/correction workflow.
-   [ ] Add database backup and restore procedures.
-   [ ] Keep sensitive local data out of Git.

## Troubleshooting

### Camera does not open

-   Close other applications using the webcam.
-   Check camera permissions in Windows.
-   Run `python camera_test.py`.
-   Confirm the camera index in the script matches your webcam.

### Model file not found

Confirm that the ONNX files exist under `models/` and filenames match
the paths in the code.

### No students recognized

-   Confirm the student is active in SQLite.
-   Confirm the roll number exists in the face-feature JSON file.
-   Check lighting, camera distance, and face alignment.
-   Confirm the registration embedding was saved successfully.

### Session does not appear in the database

Run `python check_sessions.py` from the project root and verify that the
class-wise workflow started and finished without an error.

------------------------------------------------------------------------

## Project Status

This is an educational project under active development. Update this
README whenever features are implemented, tested, or changed. Keep the
code, database schema, and documentation aligned as the project evolves.
