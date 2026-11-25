"""AUDIT HISTORY module. Every other service calls log() whenever something changes,
so who/what/when is always recorded."""
from datetime import datetime


def log(conn, user_id, entity, entity_id, action, details=""):
    conn.execute(
        "INSERT INTO audit_log(user_id,entity,entity_id,action,details,timestamp) VALUES(?,?,?,?,?,?)",
        (user_id, entity, entity_id, action, details, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


def recent(conn, limit=300, entity=None):
    sql = """SELECT a.*, e.name AS user_name FROM audit_log a
             LEFT JOIN employees e ON e.id=a.user_id"""
    params = []
    if entity:
        sql += " WHERE a.entity=?"
        params.append(entity)
    sql += " ORDER BY a.id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()
