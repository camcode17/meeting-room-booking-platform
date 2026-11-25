"""APPROVAL MANAGEMENT module.
1. required_approval(): decides IF a booking needs approval and from WHOM (routing rules).
2. decide(): an approver approves/rejects; the decision is recorded in `approvals` and audited.
A recurring series is approved/rejected as ONE unit (all occurrences share recurrence_group)."""
from datetime import datetime
import config
from services import audit, conflict_engine

FMT = config.DT_FMT


def required_approval(employee, room, start_s, end_s):
    """Returns (approver_role, reason) or (None, None). These are the 'configured rules'."""
    if room["capacity"] >= config.APPROVAL_CAPACITY_THRESHOLD:
        return "FACILITY_TEAM", f"High-capacity room (capacity {room['capacity']})"
    if room["requires_approval"]:
        return "FACILITY_TEAM", "Special facility requires facility-team approval"
    hours = (datetime.strptime(end_s, FMT) - datetime.strptime(start_s, FMT)).total_seconds() / 3600
    if hours > config.LONG_BOOKING_HOURS and employee["role"] == "EMPLOYEE" and employee["manager_id"]:
        return "MANAGER", f"Booking longer than {config.LONG_BOOKING_HOURS}h needs manager approval"
    return None, None


def can_decide(conn, user, booking, approval):
    if user["id"] == booking["employee_id"]:
        return False                                   # nobody approves their own request
    if approval["approver_role"] == "FACILITY_TEAM":
        return user["role"] == "FACILITY_ADMIN"
    owner = conn.execute("SELECT manager_id FROM employees WHERE id=?", (booking["employee_id"],)).fetchone()
    return user["id"] == owner["manager_id"] or user["role"] == "FACILITY_ADMIN"


def pending_for(conn, user):
    """Approval queue for this user; a series shows once (first occurrence) with its size."""
    rows = conn.execute(
        """SELECT a.*, b.start_time, b.end_time, b.purpose, b.attendees, b.recurrence_group, b.employee_id,
                  b.room_id, r.name AS room_name, e.name AS requester
           FROM approvals a JOIN bookings b ON b.id=a.booking_id
           JOIN rooms r ON r.id=b.room_id JOIN employees e ON e.id=b.employee_id
           WHERE a.decision='PENDING' AND b.status='PENDING' ORDER BY b.start_time""").fetchall()
    out, seen = [], set()
    for a in rows:
        if not can_decide(conn, user, {"employee_id": a["employee_id"]}, a):
            continue
        g = a["recurrence_group"]
        if g:
            if g in seen:
                continue
            seen.add(g)
            n = conn.execute("SELECT COUNT(*) FROM bookings WHERE recurrence_group=? AND status='PENDING'", (g,)).fetchone()[0]
        else:
            n = 1
        out.append({"a": a, "count": n})
    return out


def decide(conn, user, booking_id, decision, comment=""):
    """decision = 'APPROVED' or 'REJECTED'. Returns (ok, message)."""
    b = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if not b or b["status"] != "PENDING":
        return False, "Booking is not awaiting approval."
    ap = conn.execute("SELECT * FROM approvals WHERE booking_id=? AND decision='PENDING'", (booking_id,)).fetchone()
    if not ap or not can_decide(conn, user, b, ap):
        return False, "You are not allowed to decide this request."
    group = ([r["id"] for r in conn.execute(
        "SELECT id FROM bookings WHERE recurrence_group=? AND status='PENDING'", (b["recurrence_group"],))]
        if b["recurrence_group"] else [booking_id])

    if decision == "APPROVED":
        # Re-validate: maintenance may have been scheduled since the request was made.
        room = conn.execute("SELECT * FROM rooms WHERE id=?", (b["room_id"],)).fetchone()
        for bid in group:
            x = conn.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
            eq = [r["equipment_id"] for r in conn.execute("SELECT equipment_id FROM booking_equipment WHERE booking_id=?", (bid,))]
            bad = conflict_engine.check_occurrences(conn, room, [(x["start_time"], x["end_time"])],
                                                    x["attendees"], eq, exclude_ids=group, check_past=False)
            if bad:
                return False, "Cannot approve - conflict detected: " + bad[0]["problems"][0]["message"]

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for bid in group:
        conn.execute("UPDATE bookings SET status=? WHERE id=?", (decision, bid))
        conn.execute("""UPDATE approvals SET decision=?, decided_by=?, decided_at=?, comment=?
                        WHERE booking_id=? AND decision='PENDING'""", (decision, user["id"], now, comment, bid))
        audit.log(conn, user["id"], "booking", bid, decision, comment or "")
    conn.commit()
    return True, f"{len(group)} booking(s) {decision.lower()}."
