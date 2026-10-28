from flask import Flask, render_template, request, Response, redirect, url_for, flash, session, jsonify, send_file
from pathlib import Path
from datetime import datetime, date, timedelta
from functools import wraps
from contextlib import contextmanager
import csv, json, io, os, sqlite3, subprocess, sys, calendar, secrets, time
from werkzeug.security import generate_password_hash, check_password_hash
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SMART_ATTENDANCE_DATA_DIR", BASE_DIR / "dataset")).expanduser()
if not DATA_DIR.is_absolute():
    DATA_DIR = (BASE_DIR / DATA_DIR).resolve()
FEATURES_FILE = DATA_DIR / "face_features.json"
ATTENDANCE_DIR = DATA_DIR / "attendance_records"
DASHBOARD_DB = DATA_DIR / "dashboard_admin.db"
LEGACY_ATTENDANCE_DB = DATA_DIR / "smart_attendance.db"
ATTENDANCE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
# Persist a randomly generated secret locally unless an environment secret is supplied.
SECRET_FILE = DASHBOARD_DB.parent / ".flask_secret"
if os.environ.get("SMART_ATTENDANCE_SECRET"):
    app.secret_key = os.environ["SMART_ATTENDANCE_SECRET"]
else:
    DASHBOARD_DB.parent.mkdir(parents=True, exist_ok=True)
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32), encoding="utf-8")
    app.secret_key = SECRET_FILE.read_text(encoding="utf-8").strip()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024


@contextmanager
def db():
    """Open a short-lived SQLite connection and always close it after the transaction."""
    conn = sqlite3.connect(DASHBOARD_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS student_overrides (
            roll TEXT PRIMARY KEY, display_name TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS pending_students (
            roll TEXT PRIMARY KEY, full_name TEXT NOT NULL, created_at TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT 'Register face using the existing student manager')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('hod','lecturer')),
            display_name TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS institution_profile (
            id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, code TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL)""")
        # Step 1: class records. Subjects, lecturer assignments, and timetable are later steps.
        conn.execute("""CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_code TEXT NOT NULL UNIQUE COLLATE NOCASE,
            department TEXT NOT NULL,
            semester INTEGER NOT NULL CHECK(semester BETWEEN 1 AND 8),
            section TEXT NOT NULL,
            academic_year TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE COLLATE NOCASE,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS class_students (
            class_id INTEGER NOT NULL,
            roll TEXT NOT NULL,
            assigned_at TEXT NOT NULL,
            PRIMARY KEY(class_id, roll),
            FOREIGN KEY(class_id) REFERENCES classes(id) ON DELETE CASCADE)""")
        # Operational class modules: subjects, timetable, lecturer assignments, and attendance.
        conn.execute("""CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT, class_id INTEGER NOT NULL,
            code TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(class_id, code), FOREIGN KEY(class_id) REFERENCES classes(id) ON DELETE CASCADE)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS lecturer_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, class_id INTEGER NOT NULL,
            subject_id INTEGER, created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(class_id) REFERENCES classes(id) ON DELETE CASCADE,
            FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS timetable_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, class_id INTEGER NOT NULL, subject_id INTEGER NOT NULL,
            day_of_week TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, room TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
            UNIQUE(class_id, day_of_week, start_time, subject_id),
            FOREIGN KEY(class_id) REFERENCES classes(id) ON DELETE CASCADE,
            FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS attendance_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, class_id INTEGER NOT NULL, subject_id INTEGER NOT NULL,
            session_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled', source_timetable_id INTEGER,
            started_at TEXT, completed_at TEXT, process_id INTEGER, launch_attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(source_timetable_id, session_date),
            FOREIGN KEY(class_id) REFERENCES classes(id) ON DELETE CASCADE,
            FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE RESTRICT,
            FOREIGN KEY(source_timetable_id) REFERENCES timetable_entries(id) ON DELETE SET NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS attendance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, roll TEXT NOT NULL,
            student_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Present', marked_at TEXT NOT NULL,
            UNIQUE(session_id, roll), FOREIGN KEY(session_id) REFERENCES attendance_sessions(id) ON DELETE CASCADE)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER, action TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT '', target_id TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL)""")
        session_columns = {row[1] for row in conn.execute("PRAGMA table_info(attendance_sessions)")}
        if "process_id" not in session_columns:
            conn.execute("ALTER TABLE attendance_sessions ADD COLUMN process_id INTEGER")
        if "launch_attempts" not in session_columns:
            conn.execute("ALTER TABLE attendance_sessions ADD COLUMN launch_attempts INTEGER NOT NULL DEFAULT 0")
        # Existing installations are assigned a neutral profile; first-time setup names the college.
        has_profile = conn.execute("SELECT 1 FROM institution_profile WHERE id=1").fetchone()
        has_user = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        if not has_profile and has_user:
            conn.execute("INSERT INTO institution_profile(id,name,code,created_at) VALUES(1,?,?,?)",
                         ("Local College", "", datetime.now().isoformat(timespec="seconds")))
        # Seed common engineering departments once; the HOD can add others.
        department_count = conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        if department_count == 0:
            now = datetime.now().isoformat(timespec="seconds")
            defaults = [
                ("CSE", "Computer Science and Engineering", ""),
                ("CIVIL", "Civil Engineering", ""),
                ("AIML", "Artificial Intelligence and Machine Learning", ""),
                ("MECH", "Mechanical Engineering", ""),
                ("ECE", "Electronics and Communication Engineering", ""),
                ("EEE", "Electrical and Electronics Engineering", ""),
                ("ISE", "Information Science and Engineering", ""),
                ("OTHER", "Other Department", "Add a department not listed here"),
            ]
            conn.executemany("INSERT INTO departments(code,name,description,created_at) VALUES(?,?,?,?)",
                             [(code, name, desc, now) for code, name, desc in defaults])
        # Preserve any departments already present in classes created before this update.
        for row in conn.execute("SELECT DISTINCT department FROM classes"):
            code = (row[0] or "").strip().upper()
            if code and not conn.execute("SELECT 1 FROM departments WHERE code=?", (code,)).fetchone():
                now = datetime.now().isoformat(timespec="seconds")
                conn.execute("INSERT INTO departments(code,name,description,created_at) VALUES(?,?,?,?)",
                             (code, code, "Migrated from an existing class", now))


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        with db() as conn:
            user = conn.execute("SELECT id, username, role, display_name, is_active FROM users WHERE id=?",
                                (session["user_id"],)).fetchone()
        if not user or not user["is_active"]:
            session.clear()
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


@app.context_processor
def institution_context():
    """Make the single-college profile available to every template in this deployment."""
    try:
        with db() as conn:
            profile = conn.execute("SELECT name,code FROM institution_profile WHERE id=1").fetchone()
        return {"institution_name": profile["name"] if profile else "College not configured"}
    except sqlite3.Error:
        return {"institution_name": "College not configured"}


def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                return render_template("forbidden.html"), 403
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def audit(action, target_type="", target_id="", details=""):
    """Write a small audit event without allowing logging failures to break a request or scheduler thread."""
    try:
        try:
            actor_id = session.get("user_id")
        except RuntimeError:  # background scheduler has no Flask request context
            actor_id = None
        with db() as conn:
            conn.execute(
                "INSERT INTO audit_logs(actor_user_id,action,target_type,target_id,details,created_at) VALUES(?,?,?,?,?,?)",
                (actor_id, action, target_type, str(target_id), details[:1000], datetime.now().isoformat(timespec="seconds")),
            )
    except Exception:
        pass


def class_access_allowed(class_id):
    if session.get("role") == "hod":
        return True
    if session.get("role") != "lecturer":
        return False
    with db() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM lecturer_assignments WHERE user_id=? AND class_id=? LIMIT 1",
            (session.get("user_id"), class_id),
        ).fetchone())


def class_access_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        class_id = kwargs.get("class_id")
        if class_id is None or not class_access_allowed(class_id):
            return render_template("forbidden.html"), 403
        return fn(*args, **kwargs)
    return wrapped


def load_students():
    """Face-enrolled students are read-only here to protect the recognition file."""
    students = {}
    if FEATURES_FILE.exists():
        try:
            data = json.loads(FEATURES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for roll, record in data.items():
                    if isinstance(record, dict):
                        students[str(roll)] = {
                            "roll": str(roll),
                            "name": str(record.get("name") or record.get("full_name") or record.get("student_name") or "Unknown"),
                            "enrolled": True,
                        }
        except (OSError, json.JSONDecodeError):
            pass
    try:
        with db() as conn:
            for row in conn.execute("SELECT roll, display_name FROM student_overrides"):
                if row["roll"] in students:
                    students[row["roll"]]["name"] = row["display_name"]
            for row in conn.execute("SELECT roll, full_name FROM pending_students"):
                if row["roll"] not in students:
                    students[row["roll"]] = {"roll": row["roll"], "name": row["full_name"], "enrolled": False}
    except sqlite3.Error:
        pass
    return students


def registered_student_rolls_for_department(department):
    """Return active student rolls registered in the selected engineering branch."""
    if not LEGACY_ATTENDANCE_DB.exists():
        return set()
    legacy = None
    try:
        legacy = sqlite3.connect(LEGACY_ATTENDANCE_DB, timeout=3)
        rows = legacy.execute("""SELECT st.roll_number
            FROM students st
            JOIN sections sec ON sec.id=st.section_id
            JOIN academic_levels al ON al.id=sec.academic_level_id
            JOIN courses co ON co.id=al.course_id
            WHERE st.is_active=1 AND upper(trim(co.name))=upper(trim(?))""", (department,)).fetchall()
        return {str(row[0]) for row in rows}
    except sqlite3.Error:
        return set()
    finally:
        if legacy is not None:
            legacy.close()


def attendance_file(day_text):
    try:
        datetime.strptime(day_text, "%Y-%m-%d")
    except (TypeError, ValueError):
        day_text = date.today().isoformat()
    return ATTENDANCE_DIR / f"attendance_{day_text}.csv"


def load_attendance(day_text):
    """Merge daily CSV rows and both desktop/web class-session databases."""
    records = {}
    path = attendance_file(day_text)
    if path.exists():
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    roll = (row.get("Roll Number") or row.get("roll_number") or row.get("Roll") or row.get("roll") or "").strip()
                    if not roll:
                        continue
                    records[roll] = {
                        "roll": roll,
                        "name": (row.get("Name") or row.get("Student Name") or row.get("full_name") or "").strip(),
                        "time": (row.get("Time") or row.get("Timestamp") or row.get("time") or "").strip(),
                        "status": (row.get("Status") or row.get("status") or "Present").strip() or "Present",
                        "class_name": (row.get("Class") or row.get("Section") or row.get("class_name") or "").strip(),
                    }
        except OSError:
            pass
    if LEGACY_ATTENDANCE_DB.exists():
        legacy = None
        try:
            legacy = sqlite3.connect(LEGACY_ATTENDANCE_DB, timeout=3)
            legacy.row_factory = sqlite3.Row
            legacy_rows = legacy.execute("""SELECT st.roll_number AS roll,st.full_name AS student_name,
                sec.name AS class_name,r.status,r.marked_at FROM attendance_records r
                JOIN attendance_sessions a ON a.id=r.session_id
                JOIN students st ON st.id=r.student_id
                JOIN sections sec ON sec.id=a.section_id
                WHERE a.session_date=? ORDER BY r.marked_at""", (day_text,)).fetchall()
            for row in legacy_rows:
                records[row["roll"]] = {"roll": row["roll"], "name": row["student_name"],
                                         "time": row["marked_at"], "status": row["status"],
                                         "class_name": row["class_name"]}
        except sqlite3.Error:
            pass
        finally:
            if legacy is not None:
                legacy.close()
    try:
        with db() as conn:
            rows = conn.execute("""SELECT r.roll,r.student_name,r.status,r.marked_at,c.class_code AS class_name
                FROM attendance_records r JOIN attendance_sessions a ON a.id=r.session_id
                JOIN classes c ON c.id=a.class_id
                WHERE a.session_date=? ORDER BY r.marked_at""", (day_text,)).fetchall()
        for row in rows:
            records[row["roll"]] = {"roll": row["roll"], "name": row["student_name"],
                                    "time": row["marked_at"], "status": row["status"],
                                    "class_name": row["class_name"]}
    except sqlite3.Error:
        # Keep the dashboard usable if opened against an older database before migrations run.
        pass
    return records


def student_class_labels():
    """Return class labels for students so unmarked dashboard rows remain contextualized."""
    labels = defaultdict(set)
    try:
        with db() as conn:
            rows = conn.execute("""SELECT cs.roll,c.class_code FROM class_students cs
                JOIN classes c ON c.id=cs.class_id""").fetchall()
        for row in rows:
            labels[row["roll"]].add(row["class_code"])
    except sqlite3.Error:
        pass
    if LEGACY_ATTENDANCE_DB.exists():
        legacy = None
        try:
            legacy = sqlite3.connect(LEGACY_ATTENDANCE_DB, timeout=3)
            for roll, class_name in legacy.execute("""SELECT st.roll_number,sec.name
                FROM students st JOIN sections sec ON sec.id=st.section_id"""):
                if class_name:
                    labels[str(roll)].add(str(class_name))
        except sqlite3.Error:
            pass
        finally:
            if legacy is not None:
                legacy.close()
    return {roll: ", ".join(sorted(names, key=str.casefold)) for roll, names in labels.items()}


def class_attendance_summaries(day_text):
    """Build a class/session roster summary with named present, absent, and excused students."""
    students = load_students()
    summaries = []

    try:
        with db() as conn:
            sessions = conn.execute("""SELECT a.id,a.class_id,a.subject_id,a.session_date,a.start_time,a.end_time,
                a.status,c.class_code,c.department,s.code AS subject_code,s.name AS subject_name
                FROM attendance_sessions a JOIN classes c ON c.id=a.class_id
                JOIN subjects s ON s.id=a.subject_id WHERE a.session_date=?
                ORDER BY c.class_code,a.start_time,s.code""", (day_text,)).fetchall()
            for session_row in sessions:
                roster = conn.execute("SELECT roll FROM class_students WHERE class_id=? ORDER BY roll",
                                      (session_row["class_id"],)).fetchall()
                marks = {r["roll"]: r["status"] for r in conn.execute(
                    "SELECT roll,status FROM attendance_records WHERE session_id=?", (session_row["id"],)
                ).fetchall()}
                present, absent, excused, unmarked = [], [], [], []
                for item in roster:
                    roll = item["roll"]
                    name = students.get(roll, {}).get("name", roll)
                    label = f"{name} ({roll})"
                    recorded_status = marks.get(roll)
                    if recorded_status is None and session_row["status"] != "completed":
                        unmarked.append(label)
                        continue
                    status = (recorded_status or "Absent").casefold()
                    if status in {"present", "late"}:
                        present.append(label)
                    elif status == "excused":
                        excused.append(label)
                    else:
                        absent.append(label)
                summaries.append({"session_key": f"workspace:{session_row['id']}",
                    "session_id": session_row["id"], "class_id": session_row["class_id"],
                    "class_name": session_row["class_code"], "department": session_row["department"],
                    "subject": f"{session_row['subject_code']} — {session_row['subject_name']}",
                    "time": f"{session_row['start_time']}–{session_row['end_time']}",
                    "status": session_row["status"], "present": present, "absent": absent,
                    "excused": excused, "unmarked": unmarked, "roster_count": len(roster), "source": "Class workspace"})
    except sqlite3.Error:
        pass

    if LEGACY_ATTENDANCE_DB.exists():
        legacy = None
        try:
            legacy = sqlite3.connect(LEGACY_ATTENDANCE_DB, timeout=3)
            legacy.row_factory = sqlite3.Row
            sessions = legacy.execute("""SELECT a.id,a.section_id,a.subject,a.start_time,a.end_time,a.status,
                sec.name AS class_name,co.name AS department FROM attendance_sessions a
                JOIN sections sec ON sec.id=a.section_id
                JOIN academic_levels al ON al.id=sec.academic_level_id
                JOIN courses co ON co.id=al.course_id WHERE a.session_date=?
                ORDER BY sec.name,a.start_time,a.subject""", (day_text,)).fetchall()
            for session_row in sessions:
                roster = legacy.execute("SELECT roll_number,full_name FROM students WHERE section_id=? AND is_active=1 ORDER BY roll_number",
                                        (session_row["section_id"],)).fetchall()
                # Resolve recorded student IDs back to roll numbers for display.
                student_ids = {r["roll_number"]: r["full_name"] for r in roster}
                marked_rolls = {r["roll_number"] for r in legacy.execute(
                    "SELECT st.roll_number FROM attendance_records ar JOIN students st ON st.id=ar.student_id "
                    "WHERE ar.session_id=? AND lower(ar.status) IN ('present','late')",
                    (session_row["id"],)).fetchall()}
                present, absent, unmarked = [], [], []
                for roll, name in student_ids.items():
                    label = f"{name} ({roll})"
                    if roll in marked_rolls:
                        present.append(label)
                    elif session_row["status"] == "completed":
                        absent.append(label)
                    else:
                        unmarked.append(label)
                summaries.append({"session_key": f"desktop:{session_row['id']}",
                    "class_name": session_row["class_name"], "department": session_row["department"],
                    "subject": session_row["subject"], "time": f"{session_row['start_time']}–{session_row['end_time']}",
                    "status": session_row["status"], "present": present, "absent": absent,
                    "excused": [], "unmarked": unmarked, "roster_count": len(roster), "source": "Desktop session"})
        except sqlite3.Error:
            pass
        finally:
            if legacy is not None:
                legacy.close()
    return summaries


def all_days():
    days = []
    for p in ATTENDANCE_DIR.glob("attendance_*.csv"):
        day_text = p.stem.replace("attendance_", "", 1)
        try:
            datetime.strptime(day_text, "%Y-%m-%d")
            days.append(day_text)
        except ValueError:
            continue
    try:
        with db() as conn:
            days.extend(row[0] for row in conn.execute("SELECT DISTINCT session_date FROM attendance_sessions"))
    except sqlite3.Error:
        pass
    if LEGACY_ATTENDANCE_DB.exists():
        legacy = None
        try:
            legacy = sqlite3.connect(LEGACY_ATTENDANCE_DB, timeout=3)
            days.extend(row[0] for row in legacy.execute("SELECT DISTINCT session_date FROM attendance_sessions"))
        except sqlite3.Error:
            pass
        finally:
            if legacy is not None:
                legacy.close()
    return sorted(set(days))


def dashboard_data(day_text, search="", classes_by_roll=None, branch="all", class_id=""):
    students = load_students()
    if classes_by_roll is None:
        classes_by_roll = student_class_labels()
    attendance = load_attendance(day_text)
    memberships_by_roll = defaultdict(list)
    try:
        with db() as conn:
            memberships = conn.execute("""SELECT cs.roll,c.id AS class_id,c.class_code,c.department
                FROM class_students cs JOIN classes c ON c.id=cs.class_id""").fetchall()
        for membership in memberships:
            memberships_by_roll[membership["roll"]].append({
                "id": str(membership["class_id"]), "department": str(membership["department"]),
                "class_code": str(membership["class_code"]),
            })
    except sqlite3.Error:
        pass
    complete = []
    for roll, student in students.items():
        rec = attendance.get(roll)
        complete.append({
            "roll": roll, "name": (rec.get("name") if rec and rec.get("name") else student["name"]),
            "time": rec["time"] if rec else "—",
            "status": rec["status"] if rec else ("Pending enrollment" if not student["enrolled"] else "Absent"),
            "class_name": (rec.get("class_name") if rec else "") or classes_by_roll.get(roll, "—"),
            "memberships": memberships_by_roll.get(roll, []),
            "enrolled": student["enrolled"],
        })
    for roll, rec in attendance.items():
        if roll not in students:
            complete.append({"roll": roll, "name": rec["name"] or "Unknown student", "time": rec["time"] or "—",
                             "status": rec["status"], "class_name": rec.get("class_name") or classes_by_roll.get(roll, "—"),
                             "memberships": memberships_by_roll.get(roll, []),
                             "enrolled": True})
    complete.sort(key=lambda r: (r["status"].lower() != "present", r["name"].lower(), r["roll"]))
    rows = complete
    if search:
        n = search.casefold()
        rows = [r for r in complete if n in r["roll"].casefold() or n in r["name"].casefold()]
    if branch != "all":
        rows = [r for r in rows if any(m["department"].casefold() == branch.casefold() for m in r["memberships"])]
    if class_id:
        rows = [r for r in rows if any(m["id"] == str(class_id) for m in r["memberships"])]
    enrolled = [s for s in students.values() if s["enrolled"]]
    present = sum(1 for r in complete if r["status"].casefold() in {"present", "late"})
    total = len(enrolled)
    enrolled_present = sum(1 for r in complete if r["enrolled"] and r["status"].casefold() in {"present", "late"})
    return {"rows": rows, "all_rows": complete, "total": total, "present": enrolled_present,
            "absent": max(total - enrolled_present, 0),
            "rate": round(enrolled_present / total * 100) if total else 0,
            "pending": sum(1 for s in students.values() if not s["enrolled"]),
            "attendance_count": len(attendance), "all_days": all_days()}


def student_attendance_stats(roll, days=30):
    day_list = [date.today() - timedelta(days=i) for i in range(days - 1, -1, -1)]
    present_count = 0
    records = []
    for d in day_list:
        rec = load_attendance(d.isoformat()).get(roll)
        is_present = bool(rec and rec["status"].casefold() in {"present", "late"})
        present_count += int(is_present)
        records.append({"date": d.isoformat(), "present": is_present,
                        "time": rec["time"] if rec else "—"})
    rate = round(present_count / days * 100) if days else 0
    return {"present_days": present_count, "tracked_days": days, "rate": rate, "records": records}


@app.route("/setup", methods=["GET", "POST"])
def setup():
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count:
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        institution_name = request.form.get("institution_name", "").strip()
        institution_code = request.form.get("institution_code", "").strip().upper()
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not institution_name or not username or not display_name:
            error = "Enter the college name, your name, and a username."
        elif len(institution_name) > 160 or len(institution_code) > 32 or len(username) > 80 or len(display_name) > 120:
            error = "One or more fields are too long."
        elif len(password) < 12:
            error = "Use a password with at least 12 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            with db() as conn:
                conn.execute("INSERT INTO institution_profile(id,name,code,created_at) VALUES(1,?,?,?)",
                             (institution_name, institution_code, datetime.now().isoformat(timespec="seconds")))
                conn.execute("INSERT INTO users(username,password_hash,role,display_name,created_at) VALUES(?,?,?,?,?)",
                             (username, generate_password_hash(password), "hod", display_name, datetime.now().isoformat(timespec="seconds")))
            flash("HOD account created. Sign in to continue.", "success")
            return redirect(url_for("login"))
    return render_template("setup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("index" if session.get("role") == "hod" else "lecturer_dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and user["is_active"] and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["display_name"] = user["display_name"]
            session["role"] = user["role"]
            destination = "index" if user["role"] == "hod" else "lecturer_dashboard"
            return redirect(url_for(destination))
        error = "Invalid username or password, or account disabled."
    with db() as conn:
        has_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    return render_template("login.html", error=error, has_users=has_users)


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/lecturer")
@role_required("lecturer")
def lecturer_dashboard():
    with db() as conn:
        assignments = conn.execute("""SELECT c.id AS class_id,c.class_code,c.department,c.semester,c.section,c.academic_year,
            s.name AS subject_name,s.code AS subject_code
            FROM lecturer_assignments la JOIN classes c ON c.id=la.class_id
            LEFT JOIN subjects s ON s.id=la.subject_id WHERE la.user_id=?
            ORDER BY c.department,c.semester,c.section,s.name""", (session["user_id"],)).fetchall()
    return render_template("lecturer_dashboard.html", display_name=session.get("display_name", "Lecturer"),
                           username=session.get("username", ""), assignments=assignments)


def sync_web_classes_to_desktop_db():
    """Mirror web classes into the legacy Tkinter class tables so registration can choose the matching class."""
    import contextlib
    import io as _io
    with contextlib.redirect_stdout(_io.StringIO()):
        import database as desktop_database
        desktop_database.initialize_database()
        desktop_database.seed_default_categories()
    with db() as web_conn:
        web_classes = web_conn.execute("SELECT id,class_code,department,semester,section,academic_year FROM classes ORDER BY id").fetchall()
    with desktop_database.get_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS web_class_sync (
            web_class_id INTEGER PRIMARY KEY, section_id INTEGER NOT NULL UNIQUE,
            FOREIGN KEY(section_id) REFERENCES sections(id))""")
        category = conn.execute("SELECT id FROM education_categories WHERE name='Engineering'").fetchone()
        if category is None:
            category_id = conn.execute("INSERT INTO education_categories(name) VALUES('Engineering')").lastrowid
        else:
            category_id = category["id"]
        for cls in web_classes:
            course = conn.execute("SELECT id FROM courses WHERE category_id=? AND name=?", (category_id, cls["department"])).fetchone()
            if course is None:
                course_id = conn.execute("INSERT INTO courses(category_id,name) VALUES(?,?)", (category_id, cls["department"])).lastrowid
            else:
                course_id = course["id"]
            level_name = f"B.E. {cls['department']}"
            level = conn.execute("SELECT id FROM academic_levels WHERE course_id=? AND name=?", (course_id, level_name)).fetchone()
            if level is None:
                level_id = conn.execute("INSERT INTO academic_levels(course_id,name,level_order) VALUES(?,?,1)", (course_id, level_name)).lastrowid
            else:
                level_id = level["id"]
            semester_name = f"Semester {cls['semester']}"
            semester = conn.execute("SELECT id FROM semesters WHERE academic_level_id=? AND semester_order=?", (level_id, cls["semester"])).fetchone()
            if semester is None:
                semester_id = conn.execute("INSERT INTO semesters(academic_level_id,name,semester_order) VALUES(?,?,?)", (level_id, semester_name, cls["semester"])).lastrowid
            else:
                semester_id = semester["id"]
                conn.execute("UPDATE semesters SET name=? WHERE id=?", (semester_name, semester_id))
            mapping = conn.execute("SELECT section_id FROM web_class_sync WHERE web_class_id=?", (cls["id"],)).fetchone()
            if mapping:
                section_id = mapping["section_id"]
                conn.execute("UPDATE sections SET academic_level_id=?,semester_id=?,name=? WHERE id=?", (level_id, semester_id, cls["class_code"], section_id))
            else:
                existing = conn.execute("SELECT id FROM sections WHERE academic_level_id=? AND semester_id=? AND name=?", (level_id, semester_id, cls["class_code"])).fetchone()
                if existing:
                    section_id = existing["id"]
                else:
                    section_id = conn.execute("INSERT INTO sections(academic_level_id,semester_id,name) VALUES(?,?,?)", (level_id, semester_id, cls["class_code"])).lastrowid
                conn.execute("INSERT OR REPLACE INTO web_class_sync(web_class_id,section_id) VALUES(?,?)", (cls["id"], section_id))


def launch_desktop_script(script, *args, startup_wait=1.5):
    """Start a desktop window in its own Windows console and retain startup errors."""
    script = Path(script).resolve()
    if not script.is_file():
        raise FileNotFoundError(f"Desktop program was not found: {script.name}")
    log_dir = DATA_DIR / "launch_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = log_dir / f"{script.stem}_{stamp}.log"
    executable = sys.executable
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    with log_path.open("w", encoding="utf-8", errors="replace") as output:
        process = subprocess.Popen(
            [executable, str(script), *map(str, args)],
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    time.sleep(startup_wait)
    if process.poll() is not None:
        details = log_path.read_text(encoding="utf-8", errors="replace")[-1800:].strip()
        explanation = details or "The program closed during startup without reporting an error."
        raise RuntimeError(f"{script.name} closed during startup (exit {process.returncode}). {explanation} Log: {log_path}")
    return process.pid, log_path


@app.post("/launch-student-manager")
@role_required("hod")
def launch_student_manager():
    """Launch the existing desktop face-registration UI on the machine running Flask."""
    script = BASE_DIR / "student_manager.py"
    if not script.is_file():
        flash("student_manager.py was not found. Put it in the same project folder as app.py.", "error")
    else:
        try:
            sync_web_classes_to_desktop_db()
            process_id, _log_path = launch_desktop_script(script)
            flash(f"Student Manager opened (process {process_id}). Web classes were synced into its class selector. After registration, refresh this page and assign the student to the class.", "success")
        except Exception as exc:
            flash(f"Could not open student_manager.py or sync class data: {exc}", "error")
    next_path = request.form.get("next", "").strip()
    # Only accept local app paths; never redirect to an external URL.
    if next_path.startswith("/") and not next_path.startswith("//"):
        return redirect(next_path)
    if session.get("role") == "lecturer":
        return redirect(url_for("lecturer_dashboard"))
    return redirect(url_for("students_page"))


@app.route("/")
@role_required("hod")
def index():
    selected_date = request.args.get("date", date.today().isoformat())
    try:
        datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        selected_date = date.today().isoformat()
    search = request.args.get("q", "").strip()
    selected_branch = request.args.get("branch", "all").strip()
    selected_subject = request.args.get("subject", "").strip()
    record_branch = request.args.get("record_branch", "all").strip()
    record_class_id = request.args.get("record_class_id", "").strip()
    with db() as conn:
        departments = conn.execute("SELECT code,name FROM departments ORDER BY name COLLATE NOCASE").fetchall()
        record_classes = conn.execute("SELECT id,class_code,department,semester,section FROM classes ORDER BY department,semester,section,class_code").fetchall()
    valid_branch_codes = {row["code"].casefold() for row in departments}
    if selected_branch.casefold() not in valid_branch_codes:
        selected_branch = "all"
    if record_branch.casefold() not in valid_branch_codes:
        record_branch = "all"
    if record_branch != "all":
        record_classes = [item for item in record_classes if item["department"].casefold() == record_branch.casefold()]
    if record_class_id not in {str(item["id"]) for item in record_classes}:
        record_class_id = ""
    data = dashboard_data(selected_date, search, branch=record_branch, class_id=record_class_id)
    class_attendance = class_attendance_summaries(selected_date)
    if selected_branch != "all":
        selected_department = next(row for row in departments if row["code"].casefold() == selected_branch.casefold())
        accepted_department_names = {selected_department["code"].casefold(), selected_department["name"].casefold()}
        class_attendance = [item for item in class_attendance
                            if (item.get("department") or "").strip().casefold() in accepted_department_names]
    available_subjects = sorted({item["subject"] for item in class_attendance}, key=str.casefold)
    if selected_subject not in available_subjects:
        selected_subject = ""
    if selected_subject:
        class_attendance = [item for item in class_attendance if item["subject"] == selected_subject]
    return render_template("dashboard.html", data=data, selected_date=selected_date, search=search,
                           today=date.today().isoformat(), generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
                           class_attendance=class_attendance, departments=departments, selected_branch=selected_branch,
                           available_subjects=available_subjects, selected_subject=selected_subject,
                           record_classes=record_classes, record_branch=record_branch, record_class_id=record_class_id,
                           admin_user=session.get("username", "hod"), display_name=session.get("display_name", "HOD"))


@app.get("/api/analytics")
@role_required("hod")
def analytics_api():
    days = []
    for i in range(13, -1, -1):
        d = date.today() - timedelta(days=i)
        info = dashboard_data(d.isoformat(), classes_by_roll={})
        days.append({"date": d.strftime("%d %b"), "present": info["present"], "absent": info["absent"], "rate": info["rate"]})
    month_labels, month_values = [], []
    today = date.today()
    for offset in range(5, -1, -1):
        year = today.year
        month = today.month - offset
        while month <= 0:
            month += 12
            year -= 1
        label = calendar.month_abbr[month]
        monthly_present = 0
        for d_text in all_days():
            d = datetime.strptime(d_text, "%Y-%m-%d").date()
            if d.year == year and d.month == month:
                monthly_present += sum(1 for x in load_attendance(d_text).values() if x["status"].casefold() in {"present", "late"})
        month_labels.append(label)
        month_values.append(monthly_present)
    return jsonify({"daily": days, "monthly_labels": month_labels, "monthly_present": month_values})


@app.route("/departments", methods=["GET", "POST"])
@role_required("hod")
def department_management():
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not code or not name:
            flash("Enter both a department code and name.", "error")
        elif len(code) > 20 or len(name) > 120 or len(description) > 240:
            flash("A department field is too long.", "error")
        else:
            try:
                with db() as conn:
                    conn.execute("INSERT INTO departments(code,name,description,created_at) VALUES(?,?,?,?)",
                                 (code, name, description, datetime.now().isoformat(timespec="seconds")))
                flash(f"Department {code} created.", "success")
                return redirect(url_for("department_management"))
            except sqlite3.IntegrityError:
                flash("That department code already exists.", "error")
    with db() as conn:
        departments = conn.execute("""SELECT d.*,
            (SELECT COUNT(*) FROM classes c WHERE upper(c.department)=upper(d.code) OR upper(c.department)=upper(d.name)) AS class_count
            FROM departments d ORDER BY d.name COLLATE NOCASE""").fetchall()
    return render_template("departments.html", departments=departments,
                           admin_user=session.get("username", "hod"))


@app.post("/departments/<int:department_id>/edit")
@role_required("hod")
def edit_department(department_id):
    code = request.form.get("code", "").strip().upper()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    if not code or not name or len(code) > 20 or len(name) > 120 or len(description) > 240:
        flash("Enter valid department details (code/name required).", "error")
    else:
        try:
            with db() as conn:
                old = conn.execute("SELECT code FROM departments WHERE id=?", (department_id,)).fetchone()
                if not old:
                    flash("Department not found.", "error")
                else:
                    old_code = old["code"]
                    conn.execute("UPDATE departments SET code=?, name=?, description=? WHERE id=?",
                                 (code, name, description, department_id))
                    if old_code.casefold() != code.casefold():
                        conn.execute("UPDATE classes SET department=?, updated_at=? WHERE upper(department)=upper(?)",
                                     (code, datetime.now().isoformat(timespec="seconds"), old_code))
                    flash("Department updated.", "success")
        except sqlite3.IntegrityError:
            flash("That department code is already in use.", "error")
    return redirect(url_for("department_management"))


@app.post("/departments/<int:department_id>/delete")
@role_required("hod")
def delete_department(department_id):
    with db() as conn:
        department = conn.execute("SELECT * FROM departments WHERE id=?", (department_id,)).fetchone()
        if not department:
            flash("Department not found.", "error")
        else:
            count = conn.execute("SELECT COUNT(*) FROM classes WHERE upper(department)=upper(?) OR upper(department)=upper(?)",
                                 (department["code"], department["name"])).fetchone()[0]
            if count:
                flash("This department still has classes. Move or delete its classes before deleting the department.", "error")
            else:
                conn.execute("DELETE FROM departments WHERE id=?", (department_id,))
                flash("Department deleted.", "success")
    return redirect(url_for("department_management"))


@app.route("/departments/<int:department_id>/classes", methods=["GET", "POST"])
@role_required("hod")
def class_management(department_id):
    with db() as conn:
        department = conn.execute("SELECT * FROM departments WHERE id=?", (department_id,)).fetchone()
    if not department:
        flash("Department not found.", "error")
        return redirect(url_for("department_management"))
    if request.method == "POST":
        class_code = request.form.get("class_code", "").strip().upper()
        section = request.form.get("section", "").strip().upper()
        academic_year = request.form.get("academic_year", "").strip()
        semester_text = request.form.get("semester", "").strip()
        if not all([class_code, section, academic_year, semester_text]):
            flash("Complete all class fields before saving.", "error")
        elif any(len(value) > limit for value, limit in [(class_code, 40), (section, 20), (academic_year, 20)]):
            flash("One or more class fields are too long.", "error")
        else:
            try:
                semester = int(semester_text)
                if semester < 1 or semester > 8:
                    raise ValueError
                now = datetime.now().isoformat(timespec="seconds")
                with db() as conn:
                    conn.execute("INSERT INTO classes(class_code, department, semester, section, academic_year, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                                 (class_code, department["code"], semester, section, academic_year, now, now))
                flash(f"Class {class_code} created in {department['name']}.", "success")
                return redirect(url_for("class_management", department_id=department_id))
            except ValueError:
                flash("Semester must be a number from 1 to 8.", "error")
            except sqlite3.IntegrityError:
                flash("That class code already exists. Use a unique class code.", "error")
    with db() as conn:
        classes = conn.execute("SELECT * FROM classes WHERE upper(department)=upper(?) OR upper(department)=upper(?) ORDER BY semester, section, academic_year, class_code",
                               (department["code"], department["name"])).fetchall()
    return render_template("class_management.html", classes=classes, department=department,
                           admin_user=session.get("username", "hod"))


@app.get("/classes/<int:class_id>")
@class_access_required
def class_dashboard(class_id):
    with db() as conn:
        class_record = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if not class_record:
            flash("Class not found.", "error")
            return redirect(url_for("department_management"))
        department = conn.execute("SELECT * FROM departments WHERE upper(code)=upper(?) OR upper(name)=upper(?) LIMIT 1",
                                  (class_record["department"], class_record["department"])).fetchone()
        student_count = conn.execute("SELECT COUNT(*) FROM class_students WHERE class_id=?", (class_id,)).fetchone()[0]
        assigned_rolls = [r[0] for r in conn.execute("SELECT roll FROM class_students WHERE class_id=?", (class_id,))]
    return render_template("class_dashboard.html", class_record=class_record, department=department,
                           student_count=student_count, admin_user=session.get("username", "hod"),
                           user_role=session.get("role", "hod"))


@app.route("/classes/<int:class_id>/students", methods=["GET", "POST"])
@class_access_required
def class_students(class_id):
    if request.method == "POST" and session.get("role") != "hod":
        return render_template("forbidden.html"), 403
    all_students = load_students()
    with db() as conn:
        class_record = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if not class_record:
            flash("Class not found.", "error")
            return redirect(url_for("department_management"))
        eligible_rolls = registered_student_rolls_for_department(class_record["department"])
        if request.method == "POST":
            roll = request.form.get("roll", "").strip()
            if roll not in all_students or roll not in eligible_rolls:
                flash("Choose a registered student from this class's branch.", "error")
            else:
                other_branches = conn.execute("""SELECT DISTINCT c.department
                    FROM class_students cs JOIN classes c ON c.id=cs.class_id
                    WHERE cs.roll=? AND cs.class_id<>?""", (roll, class_id)).fetchall()
                if any(str(row["department"]).strip().casefold() !=
                       str(class_record["department"]).strip().casefold() for row in other_branches):
                    flash("This student is already assigned to a different branch. Remove that membership before assigning them here.", "error")
                else:
                    try:
                        conn.execute("INSERT INTO class_students(class_id, roll, assigned_at) VALUES(?,?,?)",
                                     (class_id, roll, datetime.now().isoformat(timespec="seconds")))
                        flash(f"{all_students[roll]['name']} assigned to {class_record['class_code']}.", "success")
                    except sqlite3.IntegrityError:
                        flash("That student is already assigned to this class.", "error")
            return redirect(url_for("class_students", class_id=class_id))
        rows = conn.execute("SELECT roll, assigned_at FROM class_students WHERE class_id=? ORDER BY roll", (class_id,)).fetchall()
    assigned = []
    for row in rows:
        student = all_students.get(row["roll"], {"roll": row["roll"], "name": "Student record not found", "enrolled": False})
        assigned.append({**student, "assigned_at": row["assigned_at"]})
    unassigned = [student for roll, student in sorted(all_students.items(), key=lambda item: (item[1]["name"].casefold(), item[0]))
                  if roll in eligible_rolls
                  if not any(s["roll"] == roll for s in assigned)]
    return render_template("class_students.html", class_record=class_record, students=assigned,
                           unassigned=unassigned, admin_user=session.get("username", "hod"),
                           user_role=session.get("role", "hod"))


@app.post("/classes/<int:class_id>/students/<path:roll>/remove")
@role_required("hod")
def remove_class_student(class_id, roll):
    with db() as conn:
        conn.execute("DELETE FROM class_students WHERE class_id=? AND roll=?", (class_id, roll))
    flash("Student removed from this class. Their face record has not been deleted.", "success")
    return redirect(url_for("class_students", class_id=class_id))


@app.post("/classes/<int:class_id>/edit")
@role_required("hod")
def edit_class(class_id):
    class_code = request.form.get("class_code", "").strip().upper()
    department = request.form.get("department", "").strip().upper()
    section = request.form.get("section", "").strip().upper()
    academic_year = request.form.get("academic_year", "").strip()
    semester_text = request.form.get("semester", "").strip()
    if not all([class_code, department, section, academic_year, semester_text]):
        flash("Complete all class fields before saving.", "error")
    elif any(len(value) > limit for value, limit in [(class_code, 40), (department, 80), (section, 20), (academic_year, 20)]):
        flash("One or more class fields are too long.", "error")
    else:
        try:
            semester = int(semester_text)
            if semester < 1 or semester > 8:
                raise ValueError
            with db() as conn:
                existing = conn.execute("SELECT department FROM classes WHERE id=?", (class_id,)).fetchone()
                if not existing:
                    flash("Class not found.", "error")
                elif existing["department"].strip().casefold() != department.casefold():
                    assigned_rolls = [row[0] for row in conn.execute(
                        "SELECT roll FROM class_students WHERE class_id=?", (class_id,)
                    )]
                    eligible_rolls = registered_student_rolls_for_department(department)
                    if any(roll not in eligible_rolls for roll in assigned_rolls):
                        flash("This class cannot move to another branch while it has students registered to a different branch. Remove or correct those class assignments first.", "error")
                    else:
                        conn.execute("UPDATE classes SET class_code=?, department=?, semester=?, section=?, academic_year=?, updated_at=? WHERE id=?",
                                     (class_code, department, semester, section, academic_year, datetime.now().isoformat(timespec="seconds"), class_id))
                        flash(f"Class {class_code} updated.", "success")
                else:
                    conn.execute("UPDATE classes SET class_code=?, department=?, semester=?, section=?, academic_year=?, updated_at=? WHERE id=?",
                                 (class_code, department, semester, section, academic_year, datetime.now().isoformat(timespec="seconds"), class_id))
                    flash(f"Class {class_code} updated.", "success")
        except ValueError:
            flash("Semester must be a number from 1 to 8.", "error")
        except sqlite3.IntegrityError:
            flash("That class code is already in use by another class.", "error")
    return redirect(url_for("department_management"))


@app.post("/classes/<int:class_id>/delete")
@role_required("hod")
def delete_class(class_id):
    with db() as conn:
        result = conn.execute("DELETE FROM classes WHERE id=?", (class_id,))
    flash("Class deleted." if result.rowcount else "Class not found.", "success" if result.rowcount else "error")
    return redirect(url_for("department_management"))


@app.get("/students")
@role_required("hod")
def students_page():
    with db() as conn:
        departments = conn.execute("SELECT code,name FROM departments ORDER BY name COLLATE NOCASE").fetchall()
        classes = conn.execute("SELECT id,class_code,department,semester,section FROM classes ORDER BY department,semester,section,class_code").fetchall()
        memberships = conn.execute("""SELECT cs.roll,c.id AS class_id,c.class_code,c.department,c.semester
            FROM class_students cs JOIN classes c ON c.id=cs.class_id
            ORDER BY c.department,c.class_code""").fetchall()

    selected_branch = request.args.get("branch", "all").strip()
    department = next((d for d in departments if d["code"].casefold() == selected_branch.casefold()), None)
    if not department:
        selected_branch = "all"
    selected_branch_name = department["name"] if department else ""
    visible_classes = classes
    if department:
        branch_values = {department["code"].casefold(), department["name"].casefold()}
        visible_classes = [c for c in classes if c["department"].strip().casefold() in branch_values]

    loaded_students = load_students()
    selected_semester = request.args.get("semester", "").strip()
    if selected_semester not in {str(number) for number in range(1, 9)}:
        selected_semester = ""
    semester_counts = {str(number): set() for number in range(1, 9)}
    for membership in memberships:
        if membership["roll"] not in loaded_students:
            continue
        if department and membership["department"].strip().casefold() not in branch_values:
            continue
        semester_counts[str(membership["semester"])].add(membership["roll"])
    if selected_semester:
        visible_classes = [c for c in visible_classes if str(c["semester"]) == selected_semester]

    selected_class_text = request.args.get("class_id", "").strip()
    selected_class = next((c for c in visible_classes if str(c["id"]) == selected_class_text), None)
    if not selected_class:
        selected_class_text = ""

    classes_by_roll = defaultdict(list)
    for membership in memberships:
        classes_by_roll[membership["roll"]].append({
            "class_id": membership["class_id"], "class_code": membership["class_code"],
            "department": membership["department"], "semester": membership["semester"],
        })

    students = []
    for student in loaded_students.values():
        student["memberships"] = classes_by_roll.get(student["roll"], [])
        if selected_class and not any(m["class_id"] == selected_class["id"] for m in student["memberships"]):
            continue
        if selected_semester and not any(str(m["semester"]) == selected_semester for m in student["memberships"]):
            continue
        if department and not any(m["department"].strip().casefold() in branch_values for m in student["memberships"]):
            continue
        students.append(student)
    students.sort(key=lambda s: (s["name"].casefold(), s["roll"]))
    return render_template("students.html", students=students, departments=departments,
                           visible_classes=visible_classes, selected_branch=selected_branch,
                           selected_branch_name=selected_branch_name,
                           selected_class_text=selected_class_text, selected_semester=selected_semester,
                           semester_counts={key: len(value) for key, value in semester_counts.items()},
                           admin_user=session.get("admin_user", "admin"))


@app.post("/students/add")
@role_required("hod")
def add_student():
    roll = request.form.get("roll", "").strip()
    name = request.form.get("name", "").strip()
    if not roll or not name:
        flash("Enter both roll number and student name.", "error")
    elif len(roll) > 80 or len(name) > 120:
        flash("Roll number or name is too long.", "error")
    elif roll in load_students():
        flash("That roll number already exists.", "error")
    else:
        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO pending_students(roll, full_name, created_at) VALUES(?,?,?)",
                         (roll, name, datetime.now().isoformat(timespec="seconds")))
        flash("Student added as pending enrollment. Register their face in your existing student manager before recognition can mark attendance.", "success")
    return redirect(url_for("students_page"))


@app.post("/students/<path:roll>/edit")
@role_required("hod")
def edit_student(roll):
    name = request.form.get("name", "").strip()
    students = load_students()
    if roll not in students:
        flash("Student not found.", "error")
    elif not name:
        flash("Name cannot be empty.", "error")
    elif len(name) > 120:
        flash("Name is too long.", "error")
    else:
        if students[roll]["enrolled"]:
            with db() as conn:
                conn.execute("INSERT OR REPLACE INTO student_overrides(roll, display_name, updated_at) VALUES(?,?,?)",
                             (roll, name, datetime.now().isoformat(timespec="seconds")))
        else:
            with db() as conn:
                conn.execute("UPDATE pending_students SET full_name=? WHERE roll=?", (name, roll))
        flash("Student display name updated.", "success")
    return redirect(url_for("students_page"))


@app.post("/students/<path:roll>/delete")
@role_required("hod")
def delete_student(roll):
    # Delete only dashboard metadata/pending records; never mutate the face embedding file.
    with db() as conn:
        conn.execute("DELETE FROM student_overrides WHERE roll=?", (roll,))
        conn.execute("DELETE FROM pending_students WHERE roll=?", (roll,))
    flash("Dashboard override/pending entry removed. Face-enrolled students remain in the recognition database.", "success")
    return redirect(url_for("students_page"))


@app.get("/student/<path:roll>")
@role_required("hod")
def student_report(roll):
    students = load_students()
    if roll not in students:
        flash("Student was not found.", "error")
        return redirect(url_for("students_page"))
    stats = student_attendance_stats(roll, 30)
    history = []
    for d_text in reversed(all_days()):
        rec = load_attendance(d_text).get(roll)
        if rec:
            history.append({"date": d_text, "time": rec["time"], "status": rec["status"]})
    history.reverse()
    return render_template("student_report.html", student=students[roll], stats=stats, history=history,
                           admin_user=session.get("admin_user", "admin"))


@app.get("/export")
@role_required("hod")
def export_csv():
    selected_date = request.args.get("date", date.today().isoformat())
    data = dashboard_data(selected_date, request.args.get("q", "").strip(),
                          branch=request.args.get("record_branch", "all").strip(),
                          class_id=request.args.get("record_class_id", "").strip())
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Roll Number", "Student Name", "Class / Section", "Time", "Status"])
    for row in data["rows"]:
        writer.writerow([selected_date, row["roll"], row["name"], row["class_name"], row["time"], row["status"]])
    return Response("\\ufeff" + output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=attendance_report_{selected_date}.csv"})


@app.get("/export.xlsx")
@role_required("hod")
def export_excel():
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        flash("Excel export needs openpyxl. Run: python -m pip install openpyxl", "error")
        return redirect(url_for("index"))
    selected_date = request.args.get("date", date.today().isoformat())
    data = dashboard_data(selected_date, request.args.get("q", "").strip(),
                          branch=request.args.get("record_branch", "all").strip(),
                          class_id=request.args.get("record_class_id", "").strip())
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append(["Date", "Roll Number", "Student Name", "Class / Section", "Time", "Status"])
    for row in data["rows"]:
        ws.append([selected_date, row["roll"], row["name"], row["class_name"], row["time"], row["status"]])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="263D63")
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for col in ws.columns:
        width = max(len(str(c.value or "")) for c in col) + 3
        ws.column_dimensions[col[0].column_letter].width = min(width, 36)
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True, download_name=f"attendance_report_{selected_date}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/export.pdf")
@role_required("hod")
def export_pdf():
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        flash("PDF export needs reportlab. Run: python -m pip install reportlab", "error")
        return redirect(url_for("index"))
    selected_date = request.args.get("date", date.today().isoformat())
    data = dashboard_data(selected_date, request.args.get("q", "").strip(),
                          branch=request.args.get("record_branch", "all").strip(),
                          class_id=request.args.get("record_class_id", "").strip())
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), rightMargin=28, leftMargin=28, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Smart Attendance Report", styles["Title"]),
                Paragraph(f"Date: {selected_date} &nbsp;&nbsp; Present: {data['present']} &nbsp;&nbsp; Absent: {data['absent']} &nbsp;&nbsp; Rate: {data['rate']}%", styles["Normal"]),
                Spacer(1, 14)]
    table_data = [["Roll Number", "Student Name", "Class / Section", "Time", "Status"]]
    table_data += [[r["roll"], r["name"], r["class_name"], r["time"], r["status"]] for r in data["rows"]]
    table = Table(table_data, repeatRows=1, colWidths=[90, 190, 170, 120, 90])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#263D63")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), .4, colors.HexColor("#D9DFE9")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F5F7FB")]),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("PADDING", (0,0), (-1,-1), 7),
    ]))
    elements.append(table)
    doc.build(elements)
    out.seek(0)
    return send_file(out, as_attachment=True, download_name=f"attendance_report_{selected_date}.pdf", mimetype="application/pdf")


@app.post("/start-attendance")
@role_required("hod")
def start_attendance():
    script = BASE_DIR / "class_attendance.py"
    try:
        sync_web_classes_to_desktop_db()
        process_id, _log_path = launch_desktop_script(script)
        flash(f"Class Attendance opened (process {process_id}). Choose the class and subject, then start the camera session.", "success")
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        flash(f"Could not open Class Attendance: {exc}", "error")
    return redirect(url_for("index", date=request.form.get("date", date.today().isoformat())))


@app.route("/lecturers", methods=["GET", "POST"])
@role_required("hod")
def lecturers_page():
    if request.method == "POST":
        action = request.form.get("action", "create")
        now = datetime.now().isoformat(timespec="seconds")
        if action == "create":
            username = request.form.get("username", "").strip()
            display_name = request.form.get("display_name", "").strip()
            password = request.form.get("password", "")
            if not username or not display_name or len(username) > 80 or len(display_name) > 120 or len(password) < 12:
                flash("Enter a username/name and a password of at least 12 characters.", "error")
            else:
                try:
                    with db() as conn:
                        conn.execute("INSERT INTO users(username,password_hash,role,display_name,created_at) VALUES(?,?,?,?,?)",
                                     (username, generate_password_hash(password), "lecturer", display_name, now))
                    audit("lecturer_created", "user", username, display_name)
                    flash(f"Lecturer account '{username}' created.", "success")
                except sqlite3.IntegrityError:
                    flash("That username already exists.", "error")
        elif action == "assign":
            try:
                user_id = int(request.form.get("user_id", ""))
                class_id = int(request.form.get("class_id", ""))
                subject_text = request.form.get("subject_id", "").strip()
                subject_id = int(subject_text) if subject_text else None
                with db() as conn:
                    user = conn.execute("SELECT id,role FROM users WHERE id=?", (user_id,)).fetchone()
                    class_row = conn.execute("SELECT id FROM classes WHERE id=?", (class_id,)).fetchone()
                    subject = conn.execute("SELECT id,class_id FROM subjects WHERE id=?", (subject_id,)).fetchone() if subject_id else None
                    if not user or user["role"] != "lecturer" or not class_row:
                        raise ValueError("Choose a valid lecturer and class.")
                    if subject_id and (not subject or subject["class_id"] != class_id):
                        raise ValueError("The selected subject does not belong to that class.")
                    exists = conn.execute("SELECT id FROM lecturer_assignments WHERE user_id=? AND class_id=? AND COALESCE(subject_id,0)=COALESCE(?,0)",
                                           (user_id, class_id, subject_id)).fetchone()
                    if not exists:
                        conn.execute("INSERT INTO lecturer_assignments(user_id,class_id,subject_id,created_at) VALUES(?,?,?,?)",
                                     (user_id, class_id, subject_id, now))
                audit("lecturer_assigned", "class", class_id, f"user={user_id}; subject={subject_id}")
                flash("Lecturer assignment saved.", "success")
            except (ValueError, TypeError) as exc:
                flash(str(exc) or "Choose a valid assignment.", "error")
            except sqlite3.IntegrityError:
                flash("Could not save that assignment.", "error")
        return redirect(url_for("lecturers_page"))
    with db() as conn:
        lecturers = conn.execute("SELECT id,username,display_name,is_active,created_at FROM users WHERE role='lecturer' ORDER BY display_name").fetchall()
        classes = conn.execute("SELECT id,class_code,department,semester,section FROM classes ORDER BY department,semester,section").fetchall()
        subjects = conn.execute("SELECT id,class_id,code,name FROM subjects ORDER BY name").fetchall()
        assignments = conn.execute("""SELECT la.id,u.display_name,u.username,c.class_code,s.code AS subject_code,s.name AS subject_name
            FROM lecturer_assignments la JOIN users u ON u.id=la.user_id JOIN classes c ON c.id=la.class_id
            LEFT JOIN subjects s ON s.id=la.subject_id ORDER BY u.display_name,c.class_code,s.name""").fetchall()
    return render_template("lecturers.html", lecturers=lecturers, classes=classes, subjects=subjects,
                           assignments=assignments, admin_user=session.get("username", "hod"))


@app.post("/lecturers/assignments/<int:assignment_id>/delete")
@role_required("hod")
def delete_lecturer_assignment(assignment_id):
    with db() as conn:
        row = conn.execute("SELECT user_id,class_id FROM lecturer_assignments WHERE id=?", (assignment_id,)).fetchone()
        conn.execute("DELETE FROM lecturer_assignments WHERE id=?", (assignment_id,))
    if row:
        audit("lecturer_assignment_revoked", "class", row["class_id"], f"user={row['user_id']}")
        flash("Lecturer assignment revoked.", "success")
    else:
        flash("Assignment not found.", "error")
    return redirect(url_for("lecturers_page"))


@app.route("/classes/<int:class_id>/subjects", methods=["GET", "POST"])
@role_required("hod")
def class_subjects(class_id):
    with db() as conn:
        class_record = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        if not class_record:
            flash("Class not found.", "error")
            return redirect(url_for("department_management"))
        if request.method == "POST":
            code = request.form.get("code", "").strip().upper()
            name = request.form.get("name", "").strip()
            if not code or not name or len(code) > 30 or len(name) > 120:
                flash("Enter a subject code and name.", "error")
            else:
                try:
                    conn.execute("INSERT INTO subjects(class_id,code,name,created_at) VALUES(?,?,?,?)",
                                 (class_id, code, name, datetime.now().isoformat(timespec="seconds")))
                    flash("Subject added.", "success")
                except sqlite3.IntegrityError:
                    flash("That subject code already exists in this class.", "error")
            return redirect(url_for("class_subjects", class_id=class_id))
        subjects = conn.execute("SELECT * FROM subjects WHERE class_id=? ORDER BY code", (class_id,)).fetchall()
    return render_template("class_subjects.html", class_record=class_record, subjects=subjects,
                           admin_user=session.get("username", "hod"))


@app.post("/classes/<int:class_id>/subjects/<int:subject_id>/delete")
@role_required("hod")
def delete_subject(class_id, subject_id):
    try:
        with db() as conn:
            conn.execute("DELETE FROM subjects WHERE id=? AND class_id=?", (subject_id, class_id))
        flash("Subject deleted if it was not used by attendance history.", "success")
    except sqlite3.IntegrityError:
        flash("This subject has attendance history or timetable entries; remove those first.", "error")
    return redirect(url_for("class_subjects", class_id=class_id))


def parse_timetable_upload(file_storage):
    """Parse and validate a CSV/XLSX timetable without writing user files to disk."""
    filename = (file_storage.filename or "").lower()
    required = {"day", "subject_code", "subject", "start_time", "end_time", "section", "room"}
    if filename.endswith(".csv"):
        text = file_storage.stream.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = [{str(k).strip().lower(): v for k, v in row.items() if k is not None} for row in reader]
    elif filename.endswith(".xlsx"):
        try:
            import openpyxl
        except ImportError as exc:
            raise ValueError("Excel upload requires openpyxl: python -m pip install openpyxl") from exc
        workbook = openpyxl.load_workbook(file_storage, data_only=True, read_only=True)
        try:
            sheet = workbook.active
            values = list(sheet.iter_rows(values_only=True))
        finally:
            workbook.close()
        if not values:
            raise ValueError("The Excel file is empty.")
        headers = [str(v).strip().lower() if v is not None else "" for v in values[0]]
        rows = [{headers[i]: value for i, value in enumerate(vals) if i < len(headers) and headers[i]}
                for vals in values[1:] if any(v is not None for v in vals)]
    else:
        raise ValueError("Choose a .csv or .xlsx timetable file.")
    if not rows:
        raise ValueError("The timetable has no data rows.")
    errors, normalized = [], []
    valid_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    def parse_time(value):
        if hasattr(value, "strftime"):
            return value.strftime("%H:%M")
        value = str(value).strip()
        for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
            try: return datetime.strptime(value, fmt).strftime("%H:%M")
            except ValueError: pass
        raise ValueError(f"Invalid time '{value}' (use HH:MM).")
    with db() as conn:
        classes = conn.execute("SELECT id,class_code,section,department FROM classes").fetchall()
        for number, row in enumerate(rows, 2):
            missing = required - set(row)
            if missing:
                errors.append(f"Row {number}: missing columns {', '.join(sorted(missing))}")
                continue
            try:
                day = str(row.get("day", "")).strip().lower()
                code = str(row.get("subject_code", "")).strip().upper()
                name = str(row.get("subject", "")).strip()
                section = str(row.get("section", "")).strip().casefold()
                room = str(row.get("room", "") or "").strip()
                if day not in valid_days: raise ValueError(f"Invalid day '{day}'.")
                if not code or not name: raise ValueError("Subject code and subject are required.")
                start, end = parse_time(row.get("start_time")), parse_time(row.get("end_time"))
                if end <= start: raise ValueError("End time must be after start time.")
                matches = [c for c in classes if section in {str(c['class_code']).casefold(), str(c['section']).casefold()}]
                if len(matches) != 1:
                    raise ValueError(f"Section '{row.get('section')}' must match exactly one class code or section value.")
                normalized.append({"class_id": matches[0]["id"], "class_code": matches[0]["class_code"],
                                   "day": day.title(), "code": code, "name": name,
                                   "start": start, "end": end, "room": room, "row": number})
            except (ValueError, TypeError) as exc:
                errors.append(f"Row {number}: {exc}")
    # Reject overlapping slots for the same class/day, even when subjects differ.
    for i, a in enumerate(normalized):
        for b in normalized[i+1:]:
            if a["class_id"] == b["class_id"] and a["day"] == b["day"]:
                if a["start"] < b["end"] and b["start"] < a["end"]:
                    errors.append(f"Rows {a['row']} and {b['row']}: overlapping timetable entries for {a['class_code']} on {a['day']}.")
    if errors:
        raise ValueError("\n".join(errors))
    return normalized


@app.route("/timetable", methods=["GET", "POST"])
@role_required("hod")
def timetable_page():
    if request.method == "POST":
        upload = request.files.get("timetable_file")
        if not upload or not upload.filename:
            flash("Choose a CSV or XLSX timetable file.", "error")
        else:
            try:
                rows = parse_timetable_upload(upload)
                now = datetime.now().isoformat(timespec="seconds")
                with db() as conn:
                    # A valid upload replaces the active timetable atomically.
                    conn.execute("DELETE FROM timetable_entries")
                    for row in rows:
                        subject = conn.execute("SELECT id FROM subjects WHERE class_id=? AND upper(code)=upper(?)",
                                               (row["class_id"], row["code"])).fetchone()
                        if subject:
                            subject_id = subject["id"]
                            conn.execute("UPDATE subjects SET name=? WHERE id=?", (row["name"], subject_id))
                        else:
                            subject_id = conn.execute("INSERT INTO subjects(class_id,code,name,created_at) VALUES(?,?,?,?)",
                                                      (row["class_id"], row["code"], row["name"], now)).lastrowid
                        conn.execute("INSERT INTO timetable_entries(class_id,subject_id,day_of_week,start_time,end_time,room,created_at) VALUES(?,?,?,?,?,?,?)",
                                     (row["class_id"], subject_id, row["day"], row["start"], row["end"], row["room"], now))
                audit("timetable_imported", "timetable", "all", f"{len(rows)} entries")
                flash(f"Imported {len(rows)} timetable entries. Automatic launch runs while app.py is running on the camera computer.", "success")
            except (ValueError, sqlite3.Error) as exc:
                flash(f"Timetable import failed: {exc}", "error")
        return redirect(url_for("timetable_page"))
    with db() as conn:
        entries = conn.execute("""SELECT t.*,c.class_code,s.code AS subject_code,s.name AS subject_name
            FROM timetable_entries t JOIN classes c ON c.id=t.class_id JOIN subjects s ON s.id=t.subject_id
            ORDER BY CASE t.day_of_week WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END,t.start_time,c.class_code""").fetchall()
    return render_template("timetable.html", entries=entries, admin_user=session.get("username", "hod"))


@app.get("/classes/<int:class_id>/timetable")
@class_access_required
def class_timetable(class_id):
    with db() as conn:
        class_record = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        entries = conn.execute("""SELECT t.*,s.code AS subject_code,s.name AS subject_name FROM timetable_entries t
            JOIN subjects s ON s.id=t.subject_id WHERE t.class_id=? ORDER BY CASE t.day_of_week WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END,t.start_time""", (class_id,)).fetchall()
        if session.get("role") == "lecturer":
            all_subjects = conn.execute("SELECT 1 FROM lecturer_assignments WHERE user_id=? AND class_id=? AND subject_id IS NULL", (session["user_id"], class_id)).fetchone()
            if not all_subjects:
                allowed = {r[0] for r in conn.execute("SELECT subject_id FROM lecturer_assignments WHERE user_id=? AND class_id=? AND subject_id IS NOT NULL", (session["user_id"], class_id))}
                entries = [e for e in entries if e["subject_id"] in allowed]
    return render_template("class_timetable.html", class_record=class_record, entries=entries,
                           admin_user=session.get("username", "lecturer"))


def create_attendance_session(class_id, subject_id, session_date, start_time, end_time, source_timetable_id=None):
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        if source_timetable_id is not None:
            existing = conn.execute("SELECT id FROM attendance_sessions WHERE source_timetable_id=? AND session_date=?",
                                    (source_timetable_id, session_date)).fetchone()
            if existing:
                return existing["id"], False
        # Also prevent a re-imported timetable (new entry ID) from duplicating the same class/subject slot.
        existing_slot = conn.execute("SELECT id FROM attendance_sessions WHERE class_id=? AND subject_id=? AND session_date=? AND start_time=?",
                                     (class_id, subject_id, session_date, start_time)).fetchone()
        if existing_slot:
            return existing_slot["id"], False
        cur = conn.execute("""INSERT INTO attendance_sessions(class_id,subject_id,session_date,start_time,end_time,status,source_timetable_id,created_at)
            VALUES(?,?,?,?,?,'scheduled',?,?)""", (class_id, subject_id, session_date, start_time, end_time, source_timetable_id, now))
        return cur.lastrowid, True


def launch_class_attendance(session_id, class_id):
    script = BASE_DIR / "class_attendance_web.py"
    if not script.is_file():
        raise FileNotFoundError("class_attendance_web.py is missing from the project folder.")
    process_id, _log_path = launch_desktop_script(
        script, "--session-id", session_id, "--class-id", class_id, startup_wait=3.0
    )
    return process_id


@app.route("/classes/<int:class_id>/attendance", methods=["GET", "POST"])
@class_access_required
def class_attendance_page(class_id):
    with db() as conn:
        class_record = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
        subjects = conn.execute("SELECT * FROM subjects WHERE class_id=? ORDER BY code", (class_id,)).fetchall()
    if not class_record:
        flash("Class not found.", "error")
        return redirect(url_for("lecturer_dashboard" if session.get("role") == "lecturer" else "department_management"))
    if session.get("role") == "lecturer":
        with db() as conn:
            class_wide = conn.execute("SELECT 1 FROM lecturer_assignments WHERE user_id=? AND class_id=? AND subject_id IS NULL", (session["user_id"], class_id)).fetchone()
            allowed_subjects = {r[0] for r in conn.execute("SELECT subject_id FROM lecturer_assignments WHERE user_id=? AND class_id=? AND subject_id IS NOT NULL", (session["user_id"], class_id))}
        if not class_wide:
            subjects = [s for s in subjects if s["id"] in allowed_subjects]
    if request.method == "POST":
        session_id = None
        try:
            subject_id = int(request.form.get("subject_id", ""))
            subject = next((s for s in subjects if s["id"] == subject_id), None)
            if not subject:
                raise ValueError("Choose a subject assigned to you and belonging to this class.")
            now = datetime.now()
            start = now.strftime("%H:%M")
            end = (now + timedelta(minutes=60)).strftime("%H:%M")
            session_id, _created = create_attendance_session(class_id, subject_id, now.date().isoformat(), start, end)
            with db() as conn:
                conn.execute("UPDATE attendance_sessions SET status='running',started_at=?,launch_attempts=launch_attempts+1 WHERE id=? AND status IN ('scheduled','failed')",
                             (now.isoformat(timespec="seconds"), session_id))
            pid = launch_class_attendance(session_id, class_id)
            with db() as conn:
                conn.execute("UPDATE attendance_sessions SET process_id=? WHERE id=?", (pid, session_id))
            audit("attendance_started_manually", "session", session_id, f"class={class_id}; subject={subject['name']}")
            flash(f"Attendance camera launched for {subject['name']}.", "success")
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            if session_id is not None:
                with db() as conn:
                    conn.execute("UPDATE attendance_sessions SET status='failed',process_id=NULL WHERE id=? AND status='running'", (session_id,))
            flash(f"Could not start attendance: {exc}", "error")
        return redirect(url_for("class_attendance_page", class_id=class_id))
    with db() as conn:
        sessions = conn.execute("""SELECT a.*,s.code AS subject_code,s.name AS subject_name,
            (SELECT COUNT(*) FROM attendance_records r WHERE r.session_id=a.id AND r.status IN ('Present','Late')) AS present_count
            FROM attendance_sessions a JOIN subjects s ON s.id=a.subject_id WHERE a.class_id=?
            ORDER BY a.session_date DESC,a.start_time DESC LIMIT 100""", (class_id,)).fetchall()
        if session.get("role") == "lecturer":
            class_wide = conn.execute("SELECT 1 FROM lecturer_assignments WHERE user_id=? AND class_id=? AND subject_id IS NULL", (session["user_id"], class_id)).fetchone()
            if not class_wide:
                sessions = [r for r in sessions if r["subject_id"] in allowed_subjects]
    return render_template("class_attendance.html", class_record=class_record, subjects=subjects, sessions=sessions,
                           admin_user=session.get("username", "user"))


@app.route("/classes/<int:class_id>/sessions/<int:session_id>", methods=["GET", "POST"])
@class_access_required
def class_session_report(class_id, session_id):
    students = load_students()
    roster_size = 0
    changed = 0
    change_log = []
    with db() as conn:
        session_row = conn.execute("""SELECT a.*,c.class_code,s.code AS subject_code,s.name AS subject_name
            FROM attendance_sessions a JOIN classes c ON c.id=a.class_id JOIN subjects s ON s.id=a.subject_id
            WHERE a.id=? AND a.class_id=?""", (session_id, class_id)).fetchone()
        if not session_row:
            flash("Session not found.", "error")
            return redirect(url_for("class_attendance_page", class_id=class_id))
        if session.get("role") == "lecturer":
            allowed = conn.execute("SELECT 1 FROM lecturer_assignments WHERE user_id=? AND class_id=? AND (subject_id IS NULL OR subject_id=?) LIMIT 1",
                                   (session["user_id"], class_id, session_row["subject_id"])).fetchone()
            if not allowed:
                return render_template("forbidden.html"), 403
        assigned = {r[0] for r in conn.execute("SELECT roll FROM class_students WHERE class_id=?", (class_id,))}
        roster_size = len(assigned)
        if request.method == "POST":
            allowed_statuses = {"Present", "Late", "Absent", "Excused"}
            submitted = {roll: request.form.get(f"status__{roll}", "") for roll in assigned}
            invalid = [status for status in submitted.values() if status not in allowed_statuses]
            if invalid:
                flash("The attendance update included an invalid status. No changes were saved.", "error")
                return redirect(url_for("class_session_report", class_id=class_id, session_id=session_id))
            now = datetime.now().isoformat(timespec="seconds")
            changed = 0
            for roll, status in submitted.items():
                existing = conn.execute("SELECT status FROM attendance_records WHERE session_id=? AND roll=?",
                                        (session_id, roll)).fetchone()
                if existing is None or existing["status"] != status:
                    conn.execute("""INSERT INTO attendance_records(session_id,roll,student_name,status,marked_at)
                        VALUES(?,?,?,?,?) ON CONFLICT(session_id,roll) DO UPDATE SET
                        student_name=excluded.student_name,status=excluded.status,marked_at=excluded.marked_at""",
                                 (session_id, roll, students.get(roll, {}).get("name", roll), status, now))
                    changed += 1
                    change_log.append(f"{roll}:{existing['status'] if existing else 'Unrecorded'}->{status}")
        else:
            present = {r["roll"]: r for r in conn.execute("SELECT * FROM attendance_records WHERE session_id=?", (session_id,))}
            rows = []
            for roll in sorted(assigned):
                student = students.get(roll, {"name": "Unknown", "enrolled": False})
                record = present.get(roll)
                rows.append({"roll": roll, "name": student["name"], "status": record["status"] if record else "Absent",
                             "time": record["marked_at"] if record else "—"})
    if request.method == "POST":
        audit("attendance_roster_corrected", "session", session_id,
              f"class={class_id}; changed={changed}; roster_size={roster_size}; changes={','.join(change_log)}")
        flash(f"Roster saved. {changed} attendance status{'es' if changed != 1 else ''} updated.", "success")
        return redirect(url_for("class_session_report", class_id=class_id, session_id=session_id))
    return render_template("class_session_report.html", session_row=session_row, rows=rows,
                           admin_user=session.get("username", "user"))


@app.get("/classes/<int:class_id>/sessions/<int:session_id>/export.csv")
@class_access_required
def export_class_session_csv(class_id, session_id):
    with db() as conn:
        session_row = conn.execute("""SELECT a.*,c.class_code,s.code AS subject_code,s.name AS subject_name
            FROM attendance_sessions a JOIN classes c ON c.id=a.class_id JOIN subjects s ON s.id=a.subject_id
            WHERE a.id=? AND a.class_id=?""", (session_id, class_id)).fetchone()
        if not session_row:
            flash("Session not found.", "error")
            return redirect(url_for("class_attendance_page", class_id=class_id))
        if session.get("role") == "lecturer":
            allowed = conn.execute("SELECT 1 FROM lecturer_assignments WHERE user_id=? AND class_id=? AND (subject_id IS NULL OR subject_id=?) LIMIT 1",
                                   (session["user_id"], class_id, session_row["subject_id"])).fetchone()
            if not allowed:
                return render_template("forbidden.html"), 403
        assigned = [r[0] for r in conn.execute("SELECT roll FROM class_students WHERE class_id=? ORDER BY roll", (class_id,))]
        records = {r["roll"]: r for r in conn.execute("SELECT roll,status,marked_at FROM attendance_records WHERE session_id=?", (session_id,))}
    students = load_students()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Class", "Subject code", "Subject", "Session date", "Start time", "End time", "Roll number", "Student name", "Status", "Marked at"])
    for roll in assigned:
        student = students.get(roll, {"name": "Unknown"})
        record = records.get(roll)
        writer.writerow([session_row["class_code"], session_row["subject_code"], session_row["subject_name"],
                         session_row["session_date"], session_row["start_time"], session_row["end_time"], roll,
                         student["name"], record["status"] if record else "Absent / not marked",
                         record["marked_at"] if record else ""])
    filename = f"attendance_{session_row['class_code']}_{session_row['session_date']}_session-{session_id}.csv"
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def scheduler_tick(now=None):
    """Launch scheduled sessions once per timetable entry/date; start only within a 30-minute grace window."""
    now = now or datetime.now()
    day_name = now.strftime("%A")
    current = now.strftime("%H:%M")
    lower = (now - timedelta(minutes=30)).strftime("%H:%M")
    with db() as conn:
        entries = conn.execute("""SELECT t.*,s.name AS subject_name FROM timetable_entries t
            JOIN subjects s ON s.id=t.subject_id WHERE t.is_active=1 AND t.day_of_week=? AND t.start_time<=? AND t.start_time>=?""",
            (day_name, current, lower)).fetchall()
    for entry in entries:
        session_id = None
        try:
            session_id, created = create_attendance_session(entry["class_id"], entry["subject_id"], now.date().isoformat(),
                                                            entry["start_time"], entry["end_time"], entry["id"])
            with db() as conn:
                existing = conn.execute("SELECT status,process_id,launch_attempts FROM attendance_sessions WHERE id=?", (session_id,)).fetchone()
            if not created and existing:
                if existing["status"] in ("completed",):
                    continue
                if existing["status"] == "running" and existing["process_id"]:
                    try:
                        os.kill(int(existing["process_id"]), 0)
                        continue  # the previous camera process is still alive
                    except (OSError, ValueError, TypeError):
                        pass  # stale process record; safe to recover the existing session
                if existing["launch_attempts"] >= 2:
                    continue
            with db() as conn:
                conn.execute("""UPDATE attendance_sessions SET status='running',started_at=COALESCE(started_at,?),
                    launch_attempts=launch_attempts+1 WHERE id=?""", (now.isoformat(timespec="seconds"), session_id))
            pid = launch_class_attendance(session_id, entry["class_id"])
            with db() as conn:
                conn.execute("UPDATE attendance_sessions SET process_id=? WHERE id=?", (pid, session_id))
            audit("attendance_auto_started", "session", session_id, f"timetable={entry['id']}")
        except Exception as exc:
            if session_id:
                with db() as conn:
                    conn.execute("UPDATE attendance_sessions SET status='failed',process_id=NULL WHERE id=?", (session_id,))
            print(f"[scheduler] Could not launch timetable entry {entry['id']}: {exc}")


def scheduler_loop():
    import time
    print("[scheduler] Timetable scheduler active; automatic attendance starts when a scheduled slot is reached (30-minute catch-up window).")
    while True:
        try:
            scheduler_tick()
        except Exception as exc:
            print(f"[scheduler] {exc}")
        time.sleep(15)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


init_db()
if __name__ == "__main__":
    with db() as conn:
        has_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    print("Smart Attendance Dashboard: http://127.0.0.1:5000")
    if not has_users:
        print("First run: open http://127.0.0.1:5000/setup to create the HOD account.")
    import threading
    threading.Thread(target=scheduler_loop, name="attendance-scheduler", daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
