import tkinter as tk
from tkinter import ttk, messagebox

from database import (
    initialize_database,
    seed_default_categories,
    get_connection,
    get_categories,
)


class ClassManager:
    def __init__(self, root):
        self.root = root
        self.root.title("Smart Attendance - Class Management")
        self.root.geometry("1050x650")
        self.root.configure(bg="#101827")

        initialize_database()
        seed_default_categories()

        self.setup_ui()
        self.load_categories()
        self.load_classes()

    def setup_ui(self):
        title = tk.Label(
            self.root,
            text="CLASS MANAGEMENT",
            font=("Arial", 22, "bold"),
            bg="#101827",
            fg="white",
        )
        title.pack(pady=20)

        form = tk.Frame(self.root, bg="#182235", padx=20, pady=20)
        form.pack(fill="x", padx=25, pady=10)

        self.category_var = tk.StringVar()
        self.course_var = tk.StringVar()
        self.level_var = tk.StringVar()
        self.semester_var = tk.StringVar()
        self.section_var = tk.StringVar()

        self.category_box = self.create_field(
            form, "Education Category", self.category_var, 0
        )
        self.create_field(form, "Course / Department", self.course_var, 1)
        self.create_field(form, "Academic Year / Class", self.level_var, 2)
        self.create_field(form, "Semester (optional)", self.semester_var, 3)
        self.create_field(form, "Section / Batch", self.section_var, 4)

        tk.Button(
            form,
            text="Add Class",
            command=self.add_class,
            bg="#2563eb",
            fg="white",
            font=("Arial", 11, "bold"),
            relief="flat",
            padx=20,
            pady=8,
        ).grid(row=5, column=0, columnspan=2, pady=18)

        table_frame = tk.Frame(self.root, bg="#101827")
        table_frame.pack(fill="both", expand=True, padx=25, pady=15)

        columns = (
            "id",
            "category",
            "course",
            "level",
            "semester",
            "section",
        )

        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=12,
        )

        headings = {
            "id": "ID",
            "category": "Category",
            "course": "Course / Department",
            "level": "Academic Year / Class",
            "semester": "Semester",
            "section": "Section / Batch",
        }

        widths = {
            "id": 50,
            "category": 180,
            "course": 200,
            "level": 190,
            "semester": 130,
            "section": 120,
        }

        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="center")

        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def create_field(self, parent, label_text, variable, row):
        tk.Label(
            parent,
            text=label_text,
            bg="#182235",
            fg="white",
            font=("Arial", 10),
        ).grid(row=row, column=0, sticky="w", padx=10, pady=7)

        if label_text == "Education Category":
            widget = ttk.Combobox(
                parent,
                textvariable=variable,
                state="readonly",
                width=30,
            )
        else:
            widget = ttk.Entry(
                parent,
                textvariable=variable,
                width=33,
            )

        widget.grid(row=row, column=1, sticky="w", padx=10, pady=7)
        return widget

    def load_categories(self):
        try:
            categories = get_categories()
            names = [row["name"] for row in categories]

            self.category_box["values"] = names

            if names:
                self.category_var.set(names[0])

        except Exception as error:
            messagebox.showerror(
                "Category Error",
                f"Could not load education categories:\n{error}",
            )

    def load_classes(self):
        try:
            for item in self.tree.get_children():
                self.tree.delete(item)

            connection = get_connection()
            cursor = connection.cursor()

            cursor.execute("""
                SELECT
                    s.id,
                    ec.name AS category,
                    c.name AS course,
                    al.name AS academic_level,
                    COALESCE(sem.name, '') AS semester,
                    s.name AS section
                FROM sections AS s
                JOIN academic_levels AS al
                    ON s.academic_level_id = al.id
                JOIN courses AS c
                    ON al.course_id = c.id
                JOIN education_categories AS ec
                    ON c.category_id = ec.id
                LEFT JOIN semesters AS sem
                    ON s.semester_id = sem.id
                ORDER BY
                    ec.name,
                    c.name,
                    al.level_order,
                    sem.semester_order,
                    s.name
            """)

            rows = cursor.fetchall()

            for row in rows:
                self.tree.insert("", "end", values=tuple(row))

            connection.close()

        except Exception as error:
            messagebox.showerror(
                "Loading Error",
                f"Could not load classes:\n{error}",
            )

    def add_class(self):
        category = self.category_var.get().strip()
        course = self.course_var.get().strip()
        level = self.level_var.get().strip()
        semester = self.semester_var.get().strip()
        section = self.section_var.get().strip()

        if not all([category, course, level, section]):
            messagebox.showwarning(
                "Missing Information",
                "Enter the category, course, academic level, and section.",
            )
            return

        connection = None

        try:
            connection = get_connection()
            cursor = connection.cursor()

            # Find the selected education category.
            cursor.execute(
                "SELECT id FROM education_categories WHERE name = ?",
                (category,),
            )
            category_row = cursor.fetchone()

            if category_row is None:
                raise ValueError("Selected education category was not found.")

            category_id = category_row["id"]

            # Create the course if it does not already exist.
            cursor.execute(
                """
                SELECT id FROM courses
                WHERE category_id = ? AND name = ?
                """,
                (category_id, course),
            )
            course_row = cursor.fetchone()

            if course_row:
                course_id = course_row["id"]
            else:
                cursor.execute(
                    """
                    INSERT INTO courses (category_id, name)
                    VALUES (?, ?)
                    """,
                    (category_id, course),
                )
                course_id = cursor.lastrowid

            # Create the academic level if necessary.
            cursor.execute(
                """
                SELECT id FROM academic_levels
                WHERE course_id = ? AND name = ?
                """,
                (course_id, level),
            )
            level_row = cursor.fetchone()

            if level_row:
                level_id = level_row["id"]
            else:
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(level_order), 0) + 1
                    FROM academic_levels
                    WHERE course_id = ?
                    """,
                    (course_id,),
                )
                level_order = cursor.fetchone()[0]

                cursor.execute(
                    """
                    INSERT INTO academic_levels
                        (course_id, name, level_order)
                    VALUES (?, ?, ?)
                    """,
                    (course_id, level, level_order),
                )
                level_id = cursor.lastrowid

            # Find or create the semester, if one was entered.
            semester_id = None

            if semester:
                cursor.execute(
                    """
                    SELECT id FROM semesters
                    WHERE academic_level_id = ? AND name = ?
                    """,
                    (level_id, semester),
                )
                semester_row = cursor.fetchone()

                if semester_row:
                    semester_id = semester_row["id"]
                else:
                    cursor.execute(
                        """
                        SELECT COALESCE(MAX(semester_order), 0) + 1
                        FROM semesters
                        WHERE academic_level_id = ?
                        """,
                        (level_id,),
                    )
                    semester_order = cursor.fetchone()[0]

                    cursor.execute(
                        """
                        INSERT INTO semesters
                            (academic_level_id, name, semester_order)
                        VALUES (?, ?, ?)
                        """,
                        (level_id, semester, semester_order),
                    )
                    semester_id = cursor.lastrowid

            # Prevent duplicate sections.
            if semester_id is None:
                cursor.execute(
                    """
                    SELECT id FROM sections
                    WHERE academic_level_id = ?
                      AND semester_id IS NULL
                      AND name = ?
                    """,
                    (level_id, section),
                )
            else:
                cursor.execute(
                    """
                    SELECT id FROM sections
                    WHERE academic_level_id = ?
                      AND semester_id = ?
                      AND name = ?
                    """,
                    (level_id, semester_id, section),
                )

            if cursor.fetchone():
                connection.rollback()
                messagebox.showwarning(
                    "Duplicate Class",
                    "This section already exists for the selected level and semester.",
                )
                return

            cursor.execute(
                """
                INSERT INTO sections
                    (academic_level_id, semester_id, name)
                VALUES (?, ?, ?)
                """,
                (level_id, semester_id, section),
            )

            connection.commit()

            messagebox.showinfo(
                "Success",
                "Class added successfully!",
            )

            self.course_var.set("")
            self.level_var.set("")
            self.semester_var.set("")
            self.section_var.set("")

            self.load_classes()

        except Exception as error:
            if connection is not None:
                connection.rollback()

            messagebox.showerror(
                "Database Error",
                f"Could not add class:\n{error}",
            )

        finally:
            if connection is not None:
                connection.close()


if __name__ == "__main__":
    root = tk.Tk()
    app = ClassManager(root)
    root.mainloop()

