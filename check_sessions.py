from database import get_connection

connection = get_connection()

try:
    rows = connection.execute("""
        SELECT
            a.id,
            a.subject,
            a.session_date,
            a.start_time,
            a.end_time,
            a.status,
            COUNT(
                CASE WHEN r.status = 'Present'
                THEN r.id END
            ) AS present
        FROM attendance_sessions AS a
        LEFT JOIN attendance_records AS r
            ON r.session_id = a.id
        GROUP BY a.id
        ORDER BY a.id DESC
    """).fetchall()

    if not rows:
        print("No attendance sessions found yet.")

    for row in rows:
        print("-" * 45)
        print("Session ID:", row["id"])
        print("Subject:", row["subject"])
        print("Date:", row["session_date"])
        print("Start time:", row["start_time"])
        print("End time:", row["end_time"])
        print("Status:", row["status"])
        print("Students present:", row["present"])

finally:
    connection.close()

