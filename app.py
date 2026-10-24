from flask import Flask, render_template, request, Response, redirect, url_for, flash, session, jsonify, send_file
from pathlib import Path
from datetime import datetime, date, timedelta
from functools import wraps
import csv, json, io, os, sqlite3, subprocess, sys, calendar
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent
FEATURES_FILE = BASE_DIR / "dataset" / "face_features.json"
ATTENDANCE_DIR = BASE_DIR / "attendance_records"
DASHBOARD_DB = BASE_DIR / "dataset" / "dashboard_admin.db"
ATTENDANCE_DIR.mkdir(parents=True, exist_ok=True)
DASHBOARD_DB.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SMART_ATTENDANCE_SECRET", "change-this-secret-before-sharing")
ADMIN_USER = os.environ.get("SMART_ATTENDANCE_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("SMART_ATTENDANCE_ADMIN_PASSWORD", "ChangeMe123!")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024


def db():
    conn = sqlite3.connect(DASHBOARD_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS student_overrides (
            roll TEXT PRIMARY KEY, display_name TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS pending_students (
            roll TEXT PRIMARY KEY, full_name TEXT NOT NULL, created_at TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT 'Register face using the existing student manager')""")


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("login", next=request.path))
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


def attendance_file(day_text):
    try:
        datetime.strptime(day_text, "%Y-%m-%d")
    except (TypeError, ValueError):
        day_text = date.today().isoformat()
    return ATTENDANCE_DIR / f"attendance_{day_text}.csv"


def load_attendance(day_text):
    records = {}
    path = attendance_file(day_text)
    if not path.exists():
        return records
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
                }
    except OSError:
        pass
    return records


def all_days():
    days = []
    for p in ATTENDANCE_DIR.glob("attendance_*.csv"):
        day_text = p.stem.replace("attendance_", "", 1)
        try:
            datetime.strptime(day_text, "%Y-%m-%d")
            days.append(day_text)
        except ValueError:
            continue
    return sorted(set(days))


def dashboard_data(day_text, search=""):
    students = load_students()
    attendance = load_attendance(day_text)
    complete = []
    for roll, student in students.items():
        rec = attendance.get(roll)
        complete.append({
            "roll": roll, "name": (rec.get("name") if rec and rec.get("name") else student["name"]),
            "time": rec["time"] if rec else "—",
            "status": rec["status"] if rec else ("Pending enrollment" if not student["enrolled"] else "Absent"),
            "enrolled": student["enrolled"],
        })
    for roll, rec in attendance.items():
        if roll not in students:
            complete.append({"roll": roll, "name": rec["name"] or "Unknown student", "time": rec["time"] or "—",
                             "status": rec["status"], "enrolled": True})
    complete.sort(key=lambda r: (r["status"].lower() != "present", r["name"].lower(), r["roll"]))
    rows = complete
    if search:
        n = search.casefold()
        rows = [r for r in complete if n in r["roll"].casefold() or n in r["name"].casefold()]
    enrolled = [s for s in students.values() if s["enrolled"]]
    present = sum(1 for r in complete if r["status"].casefold() == "present")
    total = len(enrolled)
    enrolled_present = sum(1 for r in complete if r["enrolled"] and r["status"].casefold() == "present")
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
        is_present = bool(rec and rec["status"].casefold() == "present")
        present_count += int(is_present)
        records.append({"date": d.isoformat(), "present": is_present,
                        "time": rec["time"] if rec else "—"})
    rate = round(present_count / days * 100) if days else 0
    return {"present_days": present_count, "tracked_days": days, "rate": rate, "records": records}


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_logged_in"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USER and password == ADMIN_PASSWORD:
            session.clear()
            session["admin_logged_in"] = True
            session["admin_user"] = username
            return redirect(request.args.get("next") or url_for("index"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    selected_date = request.args.get("date", date.today().isoformat())
    try:
        datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        selected_date = date.today().isoformat()
    search = request.args.get("q", "").strip()
    data = dashboard_data(selected_date, search)
    return render_template("dashboard.html", data=data, selected_date=selected_date, search=search,
                           today=date.today().isoformat(), generated_at=datetime.now().strftime("%d %b %Y, %I:%M %p"),
                           admin_user=session.get("admin_user", "admin"))


@app.get("/api/analytics")
@login_required
def analytics_api():
    days = []
    for i in range(13, -1, -1):
        d = date.today() - timedelta(days=i)
        info = dashboard_data(d.isoformat())
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
                monthly_present += sum(1 for x in load_attendance(d_text).values() if x["status"].casefold() == "present")
        month_labels.append(label)
        month_values.append(monthly_present)
    return jsonify({"daily": days, "monthly_labels": month_labels, "monthly_present": month_values})


@app.get("/students")
@login_required
def students_page():
    students = sorted(load_students().values(), key=lambda s: (s["name"].lower(), s["roll"]))
    return render_template("students.html", students=students, admin_user=session.get("admin_user", "admin"))


@app.post("/students/add")
@login_required
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
@login_required
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
@login_required
def delete_student(roll):
    # Delete only dashboard metadata/pending records; never mutate the face embedding file.
    with db() as conn:
        conn.execute("DELETE FROM student_overrides WHERE roll=?", (roll,))
        conn.execute("DELETE FROM pending_students WHERE roll=?", (roll,))
    flash("Dashboard override/pending entry removed. Face-enrolled students remain in the recognition database.", "success")
    return redirect(url_for("students_page"))


@app.get("/student/<path:roll>")
@login_required
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
@login_required
def export_csv():
    selected_date = request.args.get("date", date.today().isoformat())
    data = dashboard_data(selected_date, request.args.get("q", "").strip())
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Roll Number", "Student Name", "Time", "Status"])
    for row in data["rows"]:
        writer.writerow([selected_date, row["roll"], row["name"], row["time"], row["status"]])
    return Response("\\ufeff" + output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=attendance_report_{selected_date}.csv"})


@app.get("/export.xlsx")
@login_required
def export_excel():
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        flash("Excel export needs openpyxl. Run: python -m pip install openpyxl", "error")
        return redirect(url_for("index"))
    selected_date = request.args.get("date", date.today().isoformat())
    data = dashboard_data(selected_date, request.args.get("q", "").strip())
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append(["Date", "Roll Number", "Student Name", "Time", "Status"])
    for row in data["rows"]:
        ws.append([selected_date, row["roll"], row["name"], row["time"], row["status"]])
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
@login_required
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
    data = dashboard_data(selected_date, request.args.get("q", "").strip())
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), rightMargin=28, leftMargin=28, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Smart Attendance Report", styles["Title"]),
                Paragraph(f"Date: {selected_date} &nbsp;&nbsp; Present: {data['present']} &nbsp;&nbsp; Absent: {data['absent']} &nbsp;&nbsp; Rate: {data['rate']}%", styles["Normal"]),
                Spacer(1, 14)]
    table_data = [["Roll Number", "Student Name", "Time", "Status"]]
    table_data += [[r["roll"], r["name"], r["time"], r["status"]] for r in data["rows"]]
    table = Table(table_data, repeatRows=1, colWidths=[115, 270, 130, 120])
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
@login_required
def start_attendance():
    script = BASE_DIR / "attendance.py"
    if not script.exists():
        flash("attendance.py was not found. Copy this dashboard into your existing project root.", "error")
    else:
        try:
            subprocess.Popen([sys.executable, str(script)], cwd=str(BASE_DIR))
            flash("Attendance program launched. Refresh the dashboard after marking attendance.", "success")
        except OSError as exc:
            flash(f"Could not start attendance.py: {exc}", "error")
    return redirect(url_for("index", date=request.form.get("date", date.today().isoformat())))


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


init_db()
if __name__ == "__main__":
    print("Smart Attendance Dashboard: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
