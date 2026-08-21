import sqlite3
import os

db_path = "data/quantum_trade.db"
if not os.path.exists(db_path):
    print("Database not found. Starting server to initialize...")
    # This shouldn't happen if we ran the edits correctly
else:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Tables in database:")
    for table in tables:
        print(f" - {table[0]}")
    conn.close()
