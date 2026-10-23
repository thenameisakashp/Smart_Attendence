import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import json
import csv
import subprocess
import sys
from datetime import datetime

from timetable_manager import (
    import_timetable,
    load_timetable,
    read_csv,
    read_excel,
    validate_timetable,
)

# ---------------- CONFIGURATION ----------------

APP_TITLE = "Smart Attendance System"
FEATURES_FILE = "dataset/face_features.json"
ATTENDANCE_DIR = "attendance_records"

BG_COLOR = "#0F172A"
SIDEBAR_COLOR = "#111827"
CARD_COLOR = "#1E293B"
TEXT_COLOR = "#F8FAFC"
MUTED_COLOR = "#94A3B8"
GREEN_COLOR = "#22C55E"
RED_COLOR = "#EF4444"
BLUE_COLOR = "#3B82F6"

os.makedirs(ATTENDANCE_DIR, exist_ok=True)


class SmartAttendanceDashboard:

    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1200x760")
        self.root.minsize(1000, 650)
        self.root.configure(bg=BG_COLOR)

        self.setup_styles()
        self.build_layout()
        self.refresh_dashboard()

    # ---------------- STYLES ----------------

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            "Treeview",
            background=CARD_COLOR,
            foreground=TEXT_COLOR,
            fieldbackground=CARD_COLOR,
            rowheight=30,
            font=("Segoe UI", 10)
        )

        style.configure(
            "Treeview.Heading",
            background=SIDEBAR_COLOR,
            foreground=TEXT_COLOR,
            font=("Segoe UI", 10, "bold")
        )

        style.map(
            "Treeview",
            background=[("selected", BLUE_COLOR)],
            foreground=[("selected", "white")]
        )

    # ---------------- MAIN DASHBOARD ----------------

    def build_layout(self):

        # Sidebar
        sidebar = tk.Frame(
            self.root,
            bg=SIDEBAR_COLOR,
            width=220
        )
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(
            sidebar,
            text="SMART\nATTENDANCE",
            bg=SIDEBAR_COLOR,
            fg=TEXT_COLOR,
            font=("Segoe UI", 18, "bold"),
            justify="left"
        ).pack(anchor="w", padx=20, pady=(30, 35))

        self.add_sidebar_button(
            sidebar,
            "🏠  Dashboard",
            self.refresh_dashboard
        )

        self.add_sidebar_button(
            sidebar,
            "👨‍🎓  Students",
            lambda: messagebox.showinfo(
                "Students",
                "Student management will be added in the next step."
            )
        )

        self.add_sidebar_button(
            sidebar,
            "📷  Attendance",
            self.start_attendance
        )

        self.add_sidebar_button(
            sidebar,
            "📊  Reports",
            lambda: messagebox.showinfo(
                "Reports",
                "Reports will be added in a later step."
            )
        )

        self.add_sidebar_button(
            sidebar,
            "🕒  Class Timetable",
            self.open_timetable
        )

        # Main content
        main = tk.Frame(self.root, bg=BG_COLOR)
        main.pack(
            side="left",
            fill="both",
            expand=True,
            padx=28,
            pady=25
        )

        # Header
        header = tk.Frame(main, bg=BG_COLOR)
        header.pack(fill="x", pady=(0, 22))

        tk.Label(
            header,
            text="Dashboard Overview",
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            font=("Segoe UI", 23, "bold")
        ).pack(side="left")

        self.date_label = tk.Label(
            header,
            text=datetime.now().strftime("%d %B %Y"),
            bg=BG_COLOR,
            fg=MUTED_COLOR,
            font=("Segoe UI", 11)
        )
        self.date_label.pack(side="right", pady=8)

        # Statistics cards
        stats = tk.Frame(main, bg=BG_COLOR)
        stats.pack(fill="x", pady=(0, 22))

        self.registered_value = self.create_stat_card(
            stats,
            "REGISTERED STUDENTS",
            "0",
            BLUE_COLOR
        )

        self.present_value = self.create_stat_card(
            stats,
            "PRESENT TODAY",
            "0",
            GREEN_COLOR
        )

        self.absent_value = self.create_stat_card(
            stats,
            "ABSENT TODAY",
            "0",
            RED_COLOR
        )

        # Attendance tables
        tables = tk.Frame(main, bg=BG_COLOR)
        tables.pack(fill="both", expand=True)

        # Present table
        present_frame = tk.Frame(tables, bg=CARD_COLOR)
        present_frame.pack(
            fill="both",
            expand=True,
            pady=(0, 14)
        )

        tk.Label(
            present_frame,
            text="  Today's Attendance",
            bg=CARD_COLOR,
            fg=TEXT_COLOR,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=10, pady=10)

        self.present_table = self.create_table(
            present_frame,
            ("roll", "name", "time", "status"),
            ("Roll Number", "Student Name", "Time", "Status"),
            (120, 220, 150, 120)
        )

        # Absent table
        absent_frame = tk.Frame(tables, bg=CARD_COLOR)
        absent_frame.pack(fill="both", expand=True)

        tk.Label(
            absent_frame,
            text="  Absent Students",
            bg=CARD_COLOR,
            fg=RED_COLOR,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=10, pady=10)

        self.absent_table = self.create_table(
            absent_frame,
            ("roll", "name", "status"),
            ("Roll Number", "Student Name", "Status"),
            (150, 300, 150)
        )

        # Bottom buttons
        buttons = tk.Frame(main, bg=BG_COLOR)
        buttons.pack(fill="x", pady=(18, 0))

        self.create_action_button(
            buttons,
            "↻ Refresh Dashboard",
            self.refresh_dashboard,
            BLUE_COLOR
        )

        self.create_action_button(
            buttons,
            "📷 Start Attendance",
            self.start_attendance,
            GREEN_COLOR
        )

        self.create_action_button(
            buttons,
            "👨‍🎓 Register Student",
            self.register_student,
            "#7C3AED"
        )

    # ---------------- UI HELPERS ----------------

    def add_sidebar_button(self, parent, text, command):

        button = tk.Button(
            parent,
            text=text,
            command=command,
            anchor="w",
            bg=SIDEBAR_COLOR,
            fg=TEXT_COLOR,
            activebackground=CARD_COLOR,
            activeforeground=TEXT_COLOR,
            relief="flat",
            font=("Segoe UI", 11),
            padx=20,
            pady=13,
            cursor="hand2"
        )

        button.pack(fill="x", padx=8, pady=2)

    def create_stat_card(self, parent, title, value, accent):

        card = tk.Frame(
            parent,
            bg=CARD_COLOR,
            height=115
        )
        card.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(0, 12)
        )
        card.pack_propagate(False)

        tk.Frame(
            card,
            bg=accent,
            height=4
        ).pack(fill="x")

        tk.Label(
            card,
            text=title,
            bg=CARD_COLOR,
            fg=MUTED_COLOR,
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", padx=16, pady=(13, 3))

        value_label = tk.Label(
            card,
            text=value,
            bg=CARD_COLOR,
            fg=TEXT_COLOR,
            font=("Segoe UI", 25, "bold")
        )
        value_label.pack(anchor="w", padx=16)

        return value_label

    def create_table(self, parent, columns, headings, widths):

        wrapper = tk.Frame(parent, bg=CARD_COLOR)
        wrapper.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=(0, 10)
        )

        table = ttk.Treeview(
            wrapper,
            columns=columns,
            show="headings",
            height=4
        )

        for col, heading, width in zip(
            columns,
            headings,
            widths
        ):
            table.heading(col, text=heading)
            table.column(
                col,
                width=width,
                anchor="center"
            )

        scrollbar = ttk.Scrollbar(
            wrapper,
            orient="vertical",
            command=table.yview
        )

        table.configure(
            yscrollcommand=scrollbar.set
        )

        table.pack(
            side="left",
            fill="both",
            expand=True
        )
        scrollbar.pack(side="right", fill="y")

        return table

    def create_action_button(
        self,
        parent,
        text,
        command,
        color
    ):

        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=color,
            fg="white",
            activebackground=color,
            activeforeground="white",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=10,
            cursor="hand2"
        )

        button.pack(side="left", padx=(0, 10))

    # ---------------- STUDENT DATA ----------------

    def load_students(self):

        if not os.path.exists(FEATURES_FILE):
            return {}

        try:
            with open(
                FEATURES_FILE,
                "r",
                encoding="utf-8"
            ) as file:
                return json.load(file)

        except (json.JSONDecodeError, OSError):
            messagebox.showerror(
                "Data Error",
                "Could not read the student feature database."
            )
            return {}

    # ---------------- ATTENDANCE DATA ----------------

    def load_attendance(self):

        today = datetime.now().strftime("%Y-%m-%d")

        attendance_file = os.path.join(
            ATTENDANCE_DIR,
            f"attendance_{today}.csv"
        )

        records = {}

        if not os.path.exists(attendance_file):
            return records

        try:
            with open(
                attendance_file,
                "r",
                newline="",
                encoding="utf-8"
            ) as file:

                reader = csv.DictReader(file)

                for row in reader:
                    roll = row.get(
                        "Roll Number",
                        ""
                    ).strip()

                    if roll:
                        records[roll] = row

        except OSError:
            messagebox.showerror(
                "File Error",
                "Could not read today's attendance file."
            )

        return records

    # ---------------- REFRESH DASHBOARD ----------------

    def refresh_dashboard(self):

        students = self.load_students()
        attendance = self.load_attendance()

        # Clear existing rows
        for table in (
            self.present_table,
            self.absent_table
        ):
            for item in table.get_children():
                table.delete(item)

        total = len(students)

        present = sum(
            1 for roll in attendance
            if roll in students
        )

        absent = max(total - present, 0)

        self.registered_value.config(text=str(total))
        self.present_value.config(text=str(present))
        self.absent_value.config(text=str(absent))

        # Present students
        for roll, record in attendance.items():

            if roll in students:
                self.present_table.insert(
                    "",
                    "end",
                    values=(
                        roll,
                        record.get(
                            "Name",
                            students[roll].get("name", "")
                        ),
                        record.get("Time", ""),
                        record.get("Status", "Present")
                    )
                )

        # Students not yet marked
        for roll, student in students.items():

            if roll not in attendance:
                self.absent_table.insert(
                    "",
                    "end",
                    values=(
                        roll,
                        student.get("name", ""),
                        "Not Yet Marked"
                    )
                )

        self.date_label.config(
            text=datetime.now().strftime("%d %B %Y")
        )

    # ---------------- START ATTENDANCE ----------------

    def start_attendance(self):

        if not os.path.exists("attendance.py"):
            messagebox.showerror(
                "Missing File",
                "Could not find attendance.py."
            )
            return

        try:
            subprocess.Popen([
                sys.executable,
                "attendance.py"
            ])

            self.root.after(
                1500,
                self.refresh_dashboard
            )

            messagebox.showinfo(
                "Attendance",
                "Attendance program started.\n"
                "After marking attendance, click Refresh Dashboard."
            )

        except OSError as error:
            messagebox.showerror(
                "Launch Error",
                f"Could not start attendance:\n{error}"
            )

    # ---------------- REGISTER STUDENT ----------------

    def register_student(self):

        if not os.path.exists("register_student.py"):
            messagebox.showerror(
                "Missing File",
                "Could not find register_student.py."
            )
            return

        try:
            subprocess.Popen([
                sys.executable,
                "register_student.py"
            ])

            messagebox.showinfo(
                "Registration",
                "Student registration started.\n"
                "Complete registration in the terminal and webcam window."
            )

        except OSError as error:
            messagebox.showerror(
                "Launch Error",
                f"Could not start registration:\n{error}"
            )

    # ---------------- TIMETABLE MANAGER ----------------

    def open_timetable(self):

        window = tk.Toplevel(self.root)
        window.title("Class Timetable Manager")
        window.geometry("1050x620")
        window.minsize(850, 480)
        window.configure(bg=BG_COLOR)
        window.transient(self.root)

        tk.Label(
            window,
            text="Class Timetable",
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            font=("Segoe UI", 20, "bold")
        ).pack(
            anchor="w",
            padx=22,
            pady=(18, 4)
        )

        tk.Label(
            window,
            text=(
                "Upload a CSV or Excel (.xlsx) timetable. Required columns: "
                "day, subject_code, subject, start_time, end_time, section, room."
            ),
            bg=BG_COLOR,
            fg=MUTED_COLOR,
            font=("Segoe UI", 10),
            wraplength=980,
            justify="left"
        ).pack(
            anchor="w",
            padx=22,
            pady=(0, 14)
        )

        # Toolbar
        toolbar = tk.Frame(window, bg=BG_COLOR)
        toolbar.pack(
            fill="x",
            padx=22,
            pady=(0, 12)
        )

        # Timetable table
        table_frame = tk.Frame(window, bg=CARD_COLOR)
        table_frame.pack(
            fill="both",
            expand=True,
            padx=22,
            pady=(0, 12)
        )

        columns = (
            "day",
            "subject_code",
            "subject",
            "start_time",
            "end_time",
            "section",
            "room"
        )

        table = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=14
        )

        headings = {
            "day": ("Day", 95),
            "subject_code": ("Subject Code", 115),
            "subject": ("Subject", 230),
            "start_time": ("Start", 85),
            "end_time": ("End", 85),
            "section": ("Section", 100),
            "room": ("Room", 120),
        }

        for column, (heading, width) in headings.items():
            table.heading(column, text=heading)
            table.column(
                column,
                width=width,
                minwidth=65,
                anchor="center"
            )

        scrollbar_y = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=table.yview
        )

        scrollbar_x = ttk.Scrollbar(
            table_frame,
            orient="horizontal",
            command=table.xview
        )

        table.configure(
            yscrollcommand=scrollbar_y.set,
            xscrollcommand=scrollbar_x.set
        )

        table.grid(
            row=0,
            column=0,
            sticky="nsew"
        )

        scrollbar_y.grid(
            row=0,
            column=1,
            sticky="ns"
        )

        scrollbar_x.grid(
            row=1,
            column=0,
            sticky="ew"
        )

        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # Status message
        status_label = tk.Label(
            window,
            text="Loading saved timetable...",
            bg=BG_COLOR,
            fg=MUTED_COLOR,
            font=("Segoe UI", 10)
        )

        status_label.pack(
            anchor="w",
            padx=22,
            pady=(0, 14)
        )

        # Display timetable entries
        def display_rows(rows):

            for item in table.get_children():
                table.delete(item)

            for row in rows:
                table.insert(
                    "",
                    "end",
                    values=(
                        row.get("day", ""),
                        row.get("subject_code", ""),
                        row.get("subject", ""),
                        row.get("start_time", ""),
                        row.get("end_time", ""),
                        row.get("section", ""),
                        row.get("room", "")
                    )
                )

        # Load existing timetable
        def refresh_saved_timetable():

            try:
                rows = load_timetable()
                display_rows(rows)

                if rows:
                    status_label.config(
                        text=f"Saved timetable: {len(rows)} class entries."
                    )
                else:
                    status_label.config(
                        text="No timetable saved yet. Click Upload Timetable."
                    )

            except (OSError, ValueError) as error:
                messagebox.showerror(
                    "Timetable Error",
                    f"Could not load the saved timetable: {error}",
                    parent=window
                )

        # Select, validate, preview, and import a timetable
        def choose_and_import():

            file_path = filedialog.askopenfilename(
                parent=window,
                title="Select College Timetable",
                filetypes=[
                    ("Timetable files", "*.csv *.xlsx"),
                    ("CSV files", "*.csv"),
                    ("Excel workbooks", "*.xlsx"),
                ]
            )

            if not file_path:
                return

            try:
                suffix = os.path.splitext(file_path)[1].lower()

                if suffix == ".csv":
                    raw_rows = read_csv(file_path)

                elif suffix == ".xlsx":
                    raw_rows = read_excel(file_path)

                else:
                    messagebox.showerror(
                        "Unsupported File",
                        "Please select a CSV or .xlsx file.",
                        parent=window
                    )
                    return

                # Validate timetable
                valid_rows, errors = validate_timetable(raw_rows)

                if errors:

                    error_text = (
                        "The timetable was not imported. "
                        "Fix these issues: "
                        + "; ".join(errors[:15])
                    )

                    if len(errors) > 15:
                        error_text += (
                            f"; and {len(errors) - 15} more issue(s)."
                        )

                    messagebox.showerror(
                        "Timetable Validation Failed",
                        error_text,
                        parent=window
                    )

                    refresh_saved_timetable()
                    return

                if not valid_rows:
                    messagebox.showwarning(
                        "Empty Timetable",
                        "No class entries were found in the selected file.",
                        parent=window
                    )
                    return

                # Preview before import
                display_rows(valid_rows)

                status_label.config(
                    text=(
                        f"Preview: {len(valid_rows)} entries from "
                        f"{os.path.basename(file_path)}"
                    )
                )

                confirmed = messagebox.askyesno(
                    "Confirm Timetable Import",
                    f"Previewed {len(valid_rows)} valid class entries. "
                    "Import this timetable? It will replace the currently "
                    "saved timetable.",
                    parent=window
                )

                if not confirmed:
                    refresh_saved_timetable()
                    return

                # Save timetable
                success, result = import_timetable(file_path)

                if success:

                    display_rows(result)

                    status_label.config(
                        text=(
                            f"Imported successfully: "
                            f"{len(result)} class entries."
                        )
                    )

                    messagebox.showinfo(
                        "Import Successful",
                        f"Imported {len(result)} timetable entries. "
                        "Saved to dataset/timetable.json.",
                        parent=window
                    )

                else:
                    refresh_saved_timetable()

                    messagebox.showerror(
                        "Import Failed",
                        "; ".join(result),
                        parent=window
                    )

            except (OSError, ValueError, csv.Error) as error:
                messagebox.showerror(
                    "Timetable Error",
                    f"Could not read the selected file: {error}",
                    parent=window
                )

        # Timetable buttons
        self.create_action_button(
            toolbar,
            "Upload Timetable",
            choose_and_import,
            BLUE_COLOR
        )

        self.create_action_button(
            toolbar,
            "Refresh Saved Timetable",
            refresh_saved_timetable,
            GREEN_COLOR
        )

        refresh_saved_timetable()


# ---------------- RUN APPLICATION ----------------

if __name__ == "__main__":
    root = tk.Tk()
    app = SmartAttendanceDashboard(root)
    root.mainloop()
