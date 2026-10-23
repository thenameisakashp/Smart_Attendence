import sqlite3

database_path = "dataset/smart_attendance.db"

connection = sqlite3.connect(database_path)
cursor = connection.cursor()

cursor.execute("""
    SELECT name, sql
    FROM sqlite_master
    WHERE type = 'table'
      AND name NOT LIKE 'sqlite_%'
    ORDER BY name
""")

tables = cursor.fetchall()

print("\n========== DATABASE TABLES ==========")

for table_name, create_sql in tables:
    print(f"\nTABLE: {table_name}")

    cursor.execute(f'PRAGMA table_info("{table_name}")')

    for column in cursor.fetchall():
        print(
            f"  Column: {column[1]} | "
            f"Type: {column[2]} | "
            f"Primary Key: {column[5]}"
        )

    print("  CREATE SQL:")
    print(create_sql)

print("\n========== END ==========")

connection.close()

