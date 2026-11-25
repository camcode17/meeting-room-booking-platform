"""BOOKING REQUEST + RECURRING BOOKING + CANCELLATION modules.

create_booking():
   build occurrences -> run conflict engine on ALL -> if any conflict: nothing is saved, conflicts +
   alternatives returned -> else decide approval routing, insert bookings (PENDING or APPROVED), audit.
cancel_booking(): apply cancellation rules, flip status to CANCELLED (which instantly frees the slot).
"""
import uuid
from datetime import datetime, timedelta
import config
from services import audit, conflict_engine, approval_service

FMT = config.DT_FMT


def expand_occurrences(date_str, start_t, end_t, freq, count):
    """NONE = one booking; DAILY = next N weekdays; WEEKLY = same weekday for N weeks."""
    base = datetime.strptime(date_str, "%Y-%m-%d")
    n = 1 if freq == "NONE" else count
    days, d = [], base
    while len(days) < n:
        if freq == "DAILY" and d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        days.append(d)
        d += timedelta(days=7 if freq == "WEEKLY" else 1)
    return [(f"{x:%Y-%m-%d} {start_t}", f"{x:%Y-%m-%d} {end_t}") for x in days]


def create_booking(conn, user, room_id, date, start_t, end_t, purpose, attendees,
                   equipment_ids=(), recurrence="NONE", count=1):
    result = {"ok": False, "errors": [], "conflicts": [], "alternatives": None, "booking_ids": [], "status": None}
    room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room:
        result["errors"].append("Please choose a room.")
        return result
    if not purpose or not purpose.strip():
        result["errors"].append("Purpose is required.")
    if recurrence not in ("NONE", "DAILY", "WEEKLY") or not (1 <= count <= config.MAX_RECURRENCE_COUNT):
        result["errors"].append(f"Recurrence count must be 1-{config.MAX_RECURRENCE_COUNT}.")
    try:
        occurrences = expand_occurrences(date, start_t, end_t, recurrence, count)
    except ValueError:
        result["errors"].append("Invalid date or time.")
    if result["errors"]:
        return result

    conn.commit()
    conn.execute("BEGIN IMMEDIATE")          # lock the DB so two people can't pass the check at the same moment
    try:
        conflicts = conflict_engine.check_occurrences(conn, room, occurrences, attendees, list(equipment_ids))
        if conflicts:                        # all-or-nothing: one bad occurrence blocks the entire request
            conn.rollback()
            result["conflicts"] = conflicts
            c = conflicts[0]
            result["alternatives"] = conflict_engine.suggest_alternatives(conn, room, c["start"], c["end"], attendees)
            return result

        group = str(uuid.uuid4()) if len(occurrences) > 1 else None
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for s, e in occurrences:
            role, reason = approval_service.required_approval(user, room, s, e)
            status = "PENDING" if role else "APPROVED"
            cur = conn.execute(
                """INSERT INTO bookings(employee_id,room_id,start_time,end_time,purpose,attendees,status,
                   approval_required,recurrence_group,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (user["id"], room_id, s, e, purpose.strip(), attendees, status, 1 if role else 0, group, now))
            bid = cur.lastrowid
            for eid in equipment_ids:
                conn.execute("INSERT INTO booking_equipment VALUES(?,?)", (bid, eid))
            if role:
                conn.execute("INSERT INTO approvals(booking_id,approver_role,reason) VALUES(?,?,?)", (bid, role, reason))
            audit.log(conn, user["id"], "booking", bid, "CREATED",
                      f"{room['name']} {s}-{e[11:]} status={status}" + (f" awaiting {role}: {reason}" if role else ""))
            result["booking_ids"].append(bid)
            result["status"] = status
        conn.commit()
        result["ok"] = True
        return result
    except Exception:
        conn.rollback()
        raise


def cancel_booking(conn, user, booking_id, series=False):
    """Cancellation rules: owner or facility admin only; not after it has started; employees need
    MIN_CANCEL_NOTICE_MINUTES notice (admins may override). Returns (ok, message)."""
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if not b:
        return False, "Booking not found."
    if b["status"] not in ("PENDING", "APPROVED"):
        return False, "Only pending or approved bookings can be cancelled."
    is_admin = user["role"] == "FACILITY_ADMIN"
    if b["employee_id"] != user["id"] and not is_admin:
        return False, "You can only cancel your own bookings."
    start = datetime.strptime(b["start_time"], FMT)
    now = datetime.now()
    if start <= now:
        return False, "This booking has already started or finished."
    if not is_admin and start - now < timedelta(minutes=config.MIN_CANCEL_NOTICE_MINUTES):
        return False, f"Cancellations need at least {config.MIN_CANCEL_NOTICE_MINUTES} minutes' notice."

    if series and b["recurrence_group"]:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM bookings WHERE recurrence_group=? AND status IN ('PENDING','APPROVED') AND start_time > ?",
            (b["recurrence_group"], now.strftime(FMT)))]
    else:
        ids = [booking_id]
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    for i in ids:
        conn.execute("UPDATE bookings SET status='CANCELLED', cancelled_at=?, cancelled_by=? WHERE id=?",
                     (stamp, user["id"], i))
        conn.execute("UPDATE approvals SET decision='WITHDRAWN' WHERE booking_id=? AND decision='PENDING'", (i,))
        audit.log(conn, user["id"], "booking", i, "CANCELLED", "slot released")
    conn.commit()
    return True, f"{len(ids)} booking(s) cancelled. Room and equipment are free again."


def list_bookings(conn, user, all_users=False):
    sql = """SELECT b.*, r.name AS room_name, f.name AS facility_name, e.name AS employee_name,
                    (SELECT GROUP_CONCAT(q.name, ', ') FROM booking_equipment be
                     JOIN equipment q ON q.id=be.equipment_id WHERE be.booking_id=b.id) AS equipment_names
             FROM bookings b JOIN rooms r ON r.id=b.room_id JOIN facilities f ON f.id=r.facility_id
             JOIN employees e ON e.id=b.employee_id"""
    params = []
    if not all_users:
        sql += " WHERE b.employee_id=?"
        params.append(user["id"])
    sql += " ORDER BY b.start_time DESC LIMIT 200"
    return conn.execute(sql, params).fetchall()


def modify_booking(conn, user, booking_id, room_id, date, start_t, end_t, purpose, attendees, equipment_ids=()):
    """CHANGE / RESCHEDULE a single booking (brief: 'changes are recorded' in Audit History).
    Same pipeline as create: lock -> conflict engine (ignoring the booking's OWN old slot via exclude_ids)
    -> re-run approval routing -> update -> audit with before/after. Returns the same result dict shape."""
    result = {"ok": False, "errors": [], "conflicts": [], "alternatives": None, "status": None}
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if not b or b["status"] not in ("PENDING", "APPROVED"):
        result["errors"].append("Only pending or approved bookings can be changed.")
        return result
    is_admin = user["role"] == "FACILITY_ADMIN"
    if b["employee_id"] != user["id"] and not is_admin:
        result["errors"].append("You can only change your own bookings.")
        return result
    if datetime.strptime(b["start_time"], FMT) <= datetime.now():
        result["errors"].append("This booking has already started or finished.")
        return result
    room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room or not purpose.strip():
        result["errors"].append("Room and purpose are required.")
        return result
    s, e = f"{date} {start_t}", f"{date} {end_t}"

    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conflicts = conflict_engine.check_occurrences(conn, room, [(s, e)], attendees, list(equipment_ids),
                                                      exclude_ids=[booking_id])
        if conflicts:
            conn.rollback()
            result["conflicts"] = conflicts
            result["alternatives"] = conflict_engine.suggest_alternatives(conn, room, s, e, attendees)
            return result
        owner = conn.execute("SELECT * FROM employees WHERE id=?", (b["employee_id"],)).fetchone()
        role, reason = approval_service.required_approval(owner, room, s, e)
        status = "PENDING" if role else "APPROVED"
        before = f"{b['room_id']} {b['start_time']}-{b['end_time'][11:]} att={b['attendees']}"
        conn.execute("""UPDATE bookings SET room_id=?, start_time=?, end_time=?, purpose=?, attendees=?,
                        status=?, approval_required=? WHERE id=?""",
                     (room_id, s, e, purpose.strip(), attendees, status, 1 if role else 0, booking_id))
        conn.execute("DELETE FROM booking_equipment WHERE booking_id=?", (booking_id,))
        for eid in equipment_ids:
            conn.execute("INSERT INTO booking_equipment VALUES(?,?)", (booking_id, eid))
        conn.execute("UPDATE approvals SET decision='WITHDRAWN' WHERE booking_id=? AND decision='PENDING'", (booking_id,))
        if role:
            conn.execute("INSERT INTO approvals(booking_id,approver_role,reason) VALUES(?,?,?)", (booking_id, role, reason))
        audit.log(conn, user["id"], "booking", booking_id, "MODIFIED",
                  f"from [room {before}] to [room {room_id} {s}-{e[11:]} att={attendees}] status={status}")
        conn.commit()
        result.update(ok=True, status=status)
        return result
    except Exception:
        conn.rollback()
        raise
