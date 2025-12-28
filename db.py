import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "seochecker.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def is_user_pro(user_id: int) -> bool:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT is_pro, pro_until FROM user_entitlements WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if not row:
            return False
        return int(row["is_pro"]) == 1
    finally:
        conn.close()

def get_report_for_user(report_id: str, user_id: int):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, user_id, report_json FROM reports WHERE id = ? AND user_id = ?",
            (report_id, user_id)
        ).fetchone()
        return row
    finally:
        conn.close()
