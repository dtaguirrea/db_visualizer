"""Bitácora de auditoría transversal."""
import json

from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.database import utc_now


def log_action(conn: Connection, table_name: str, record_id, action: str,
               username: str, details: dict | None = None) -> None:
    conn.execute(text('''
        INSERT INTO audit_log (table_name, record_id, action, username, occurred_at, details)
        VALUES (:table_name, :record_id, :action, :username, :occurred_at, :details)
    '''), {
        "table_name": table_name,
        "record_id": str(record_id) if record_id is not None else None,
        "action": action,
        "username": username,
        "occurred_at": utc_now(),
        "details": json.dumps(details, ensure_ascii=False) if details else None,
    })


def fetch_audit_log(conn: Connection, table_filter: str | None = None,
                    user_filter: str | None = None, limit: int = 500) -> list[dict]:
    clauses = []
    params = {"limit": limit}
    if table_filter:
        clauses.append("table_name = :table_filter")
        params["table_filter"] = table_filter
    if user_filter:
        clauses.append("username = :user_filter")
        params["user_filter"] = user_filter
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(text(
        f"SELECT * FROM audit_log{where} ORDER BY occurred_at DESC LIMIT :limit"
    ), params).mappings().all()
    return [dict(row) for row in rows]
