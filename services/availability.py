"""AVAILABILITY MANAGEMENT module.
Low-level questions: 'is this room / equipment free between start and end?'
Considers (1) existing reservations, (2) maintenance periods, (3) blocked slots.

THE KEY IDEA - overlap test:  two periods [s1,e1) and [s2,e2) overlap  <=>  s1 < e2 AND s2 < e1
Because intervals are half-open, 10:00-11:00 and 11:00-12:00 do NOT conflict (back-to-back is fine).
Only PENDING and APPROVED bookings block a slot; CANCELLED/REJECTED ones are ignored, which is
exactly why a cancelled booking 'immediately' frees the room."""
from datetime import datetime, timedelta
import config

FMT = config.DT_FMT


def _exclude(exclude_ids):
    if not exclude_ids:
        return "", []
    return " AND b.id NOT IN (%s)" % ",".join("?" * len(exclude_ids)), list(exclude_ids)


def room_booking_conflicts(conn, room_id, start, end, exclude_ids=()):
    extra, p = _exclude(exclude_ids)
    return conn.execute(
        """SELECT b.id, b.start_time, b.end_time, b.purpose, b.status, e.name AS booked_by
           FROM bookings b JOIN employees e ON e.id=b.employee_id
           WHERE b.room_id=? AND b.status IN ('PENDING','APPROVED')
             AND b.start_time < ? AND b.end_time > ?""" + extra,
        [room_id, end, start] + p).fetchall()


def equipment_booking_conflicts(conn, equipment_id, start, end, exclude_ids=()):
    extra, p = _exclude(exclude_ids)
    return conn.execute(
        """SELECT b.id, b.start_time, b.end_time, b.purpose, e.name AS booked_by
           FROM booking_equipment be JOIN bookings b ON b.id=be.booking_id
           JOIN employees e ON e.id=b.employee_id
           WHERE be.equipment_id=? AND b.status IN ('PENDING','APPROVED')
             AND b.start_time < ? AND b.end_time > ?""" + extra,
        [equipment_id, end, start] + p).fetchall()


def room_blocks(conn, room_id, start, end):
    return conn.execute(
        "SELECT * FROM maintenance_blocks WHERE room_id=? AND start_time < ? AND end_time > ?",
        (room_id, end, start)).fetchall()


def equipment_blocks(conn, equipment_id, start, end):
    return conn.execute(
        "SELECT * FROM maintenance_blocks WHERE equipment_id=? AND start_time < ? AND end_time > ?",
        (equipment_id, end, start)).fetchall()


def room_is_free(conn, room_id, start, end, exclude_ids=()):
    return not room_booking_conflicts(conn, room_id, start, end, exclude_ids) \
        and not room_blocks(conn, room_id, start, end)


def equipment_is_free(conn, equipment_id, start, end, exclude_ids=()):
    return not equipment_booking_conflicts(conn, equipment_id, start, end, exclude_ids) \
        and not equipment_blocks(conn, equipment_id, start, end)


def available_rooms(conn, start, end, min_capacity=1, required_types=()):
    """Rooms that are free for the period, big enough, and can supply the required equipment types
    (either fixed in the room, or a free portable item at the same facility)."""
    rooms = conn.execute(
        """SELECT r.*, f.name AS facility_name, f.location FROM rooms r
           JOIN facilities f ON f.id=r.facility_id
           WHERE r.status='ACTIVE' AND f.status='ACTIVE' AND r.capacity >= ?
           ORDER BY f.name, r.capacity""", (min_capacity,)).fetchall()
    result = []
    for r in rooms:
        if not room_is_free(conn, r["id"], start, end):
            continue
        notes, ok = [], True
        for t in required_types:
            if conn.execute("SELECT 1 FROM equipment WHERE room_id=? AND type=? AND status='ACTIVE'",
                            (r["id"], t)).fetchone():
                notes.append(f"{t}: in room")
                continue
            portable = conn.execute(
                """SELECT id FROM equipment WHERE room_id IS NULL AND facility_id=? AND type=?
                   AND status='ACTIVE'""", (r["facility_id"], t)).fetchall()
            if any(equipment_is_free(conn, p["id"], start, end) for p in portable):
                notes.append(f"{t}: portable available")
            else:
                ok = False
                break
        if ok:
            result.append({"room": r, "equipment_note": ", ".join(notes)})
    return result


def portable_equipment_status(conn, start, end):
    """For the availability page: every portable item + whether it is free in the period."""
    items = conn.execute(
        """SELECT q.*, f.name AS facility_name FROM equipment q JOIN facilities f ON f.id=q.facility_id
           WHERE q.room_id IS NULL AND q.status='ACTIVE' ORDER BY q.type""").fetchall()
    return [{"item": i, "free": equipment_is_free(conn, i["id"], start, end)} for i in items]


def next_free_slots(conn, room_id, day, duration_minutes, limit=3):
    """Alternative scheduling helper: scan the same day hour by hour for free windows in this room."""
    slots = []
    d = datetime.strptime(day, "%Y-%m-%d")
    t = d.replace(hour=config.BUSINESS_START_HOUR)
    day_end = d.replace(hour=config.BUSINESS_END_HOUR)
    dur = timedelta(minutes=duration_minutes)
    while t + dur <= day_end and len(slots) < limit:
        if t > datetime.now() and room_is_free(conn, room_id, t.strftime(FMT), (t + dur).strftime(FMT)):
            slots.append((t.strftime(FMT), (t + dur).strftime(FMT)))
        t += timedelta(hours=1)
    return slots
