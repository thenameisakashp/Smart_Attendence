import csv
import json
from pathlib import Path
from datetime import datetime

import openpyxl


BASE_DIR = Path(__file__).resolve().parent
TIMETABLE_FILE = BASE_DIR / "dataset" / "timetable.json"

REQUIRED_COLUMNS = {
    "day",
    "subject_code",
    "subject",
    "start_time",
    "end_time",
    "section",
    "room",
}

VALID_DAYS = {
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
}


def normalize_time(value):
    """Convert a timetable time into HH:MM format."""
    if isinstance(value, datetime):
        return value.strftime("%H:%M")

    if hasattr(value, "hour") and hasattr(value, "minute"):
        return value.strftime("%H:%M")

    value = str(value).strip()

    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M")
        except ValueError:
            continue

    raise ValueError(f"Invalid time '{value}'. Use HH:MM, such as 09:00.")


def read_csv(path):
    """Read a CSV timetable using its header row."""
    with open(path, "r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        if not reader.fieldnames:
            raise ValueError("The CSV file is empty or has no header row.")

        rows = []

        for row in reader:
            normalized = {
                str(key).strip().lower(): value
                for key, value in row.items()
                if key is not None
            }
            rows.append(normalized)

        return rows


def read_excel(path):
    """Read the first worksheet from an Excel timetable."""
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)

    try:
        worksheet = workbook.active
        values = worksheet.iter_rows(values_only=True)

        headers = next(values, None)

        if not headers:
            raise ValueError("The Excel worksheet is empty.")

        headers = [
            str(value).strip().lower() if value is not None else ""
            for value in headers
        ]

        rows = []

        for values_row in values:
            if all(value is None for value in values_row):
                continue

            rows.append({
                headers[index]: value
                for index, value in enumerate(values_row)
                if index < len(headers) and headers[index]
            })

        return rows

    finally:
        workbook.close()


def validate_timetable(rows):
    """Validate rows and return (valid_rows, errors)."""
    valid_rows = []
    errors = []

    for row_number, row in enumerate(rows, start=2):
        missing = REQUIRED_COLUMNS - set(row.keys())

        if missing:
            errors.append(
                f"Row {row_number}: missing columns: "
                + ", ".join(sorted(missing))
            )
            break

        try:
            day = str(row.get("day", "")).strip().lower()
            subject_code = str(row.get("subject_code", "")).strip()
            subject = str(row.get("subject", "")).strip()
            section = str(row.get("section", "")).strip()
            room = str(row.get("room", "") or "").strip()

            if day not in VALID_DAYS:
                raise ValueError(f"Invalid day '{day}'.")

            if not subject_code or not subject:
                raise ValueError("Subject code and subject are required.")

            if not section:
                raise ValueError("Section is required.")

            start_time = normalize_time(row.get("start_time"))
            end_time = normalize_time(row.get("end_time"))

            start = datetime.strptime(start_time, "%H:%M")
            end = datetime.strptime(end_time, "%H:%M")

            if end <= start:
                raise ValueError("End time must be after start time.")

            valid_rows.append({
                "day": day.title(),
                "subject_code": subject_code,
                "subject": subject,
                "start_time": start_time,
                "end_time": end_time,
                "section": section,
                "room": room,
            })

        except (ValueError, TypeError) as error:
            errors.append(f"Row {row_number}: {error}")

    # Detect overlapping sessions for the same day and section.
    for index, first in enumerate(valid_rows):
        for second in valid_rows[index + 1:]:
            same_day = first["day"] == second["day"]
            same_section = first["section"] == second["section"]

            if not (same_day and same_section):
                continue

            first_start = datetime.strptime(first["start_time"], "%H:%M")
            first_end = datetime.strptime(first["end_time"], "%H:%M")
            second_start = datetime.strptime(second["start_time"], "%H:%M")
            second_end = datetime.strptime(second["end_time"], "%H:%M")

            if first_start < second_end and second_start < first_end:
                errors.append(
                    f"Overlapping classes for {first['section']} on "
                    f"{first['day']}: {first['subject']} and "
                    f"{second['subject']}."
                )

    return valid_rows, errors


def import_timetable(file_path):
    """Validate and save a CSV or Excel timetable."""
    path = Path(file_path)

    if not path.is_file():
        raise ValueError("The selected file does not exist.")

    suffix = path.suffix.lower()

    if suffix == ".csv":
        rows = read_csv(path)
    elif suffix == ".xlsx":
        rows = read_excel(path)
    else:
        raise ValueError("Please select a .csv or .xlsx file.")

    valid_rows, errors = validate_timetable(rows)

    if errors:
        return False, errors

    if not valid_rows:
        return False, ["The timetable contains no valid class entries."]

    TIMETABLE_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(TIMETABLE_FILE, "w", encoding="utf-8") as file:
        json.dump(valid_rows, file, indent=4)

    return True, valid_rows


def load_timetable():
    """Load the saved timetable."""
    if not TIMETABLE_FILE.exists():
        return []

    with open(TIMETABLE_FILE, "r", encoding="utf-8") as file:
        return json.load(file)