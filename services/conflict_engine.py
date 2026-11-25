"""CONFLICT DETECTION ENGINE  (the 'Core Business Engine': Facility Availability & Booking Conflict Engine)

validate_slot()        -> every reason one time-slot can't be booked (rules + room + equipment conflicts)
check_occurrences()    -> runs validate_slot for EVERY occurrence of a (possibly recurring) request
suggest_alternatives() -> when conflicted, proposes other rooms / other times ('routed for alternative scheduling')

It only READS the database; booking_service decides what to do with the result."""
from datetime import datetime
import config
from services import availability as av

FMT = config.DT_FMT


def validate_slot(conn, room, start_s, end_s, attendees, equipment_ids, exclude_ids=(), check_past=True):
    problems = []

    def add(kind, msg):
        problems.append({"kind": kind, "message": msg})

    try:
        start, end = datetime.strptime(start_s, FMT), datetime.strptime(end_s, FMT)
    except ValueError:
        add("RULE", "Invalid date/time format.")
        return problems
    if end <= start:
        add("RULE", "End time must be after start time.")
        return problems
    if start.date() != end.date():
        add("RULE", "A booking must start and end on the same day.")
        return problems

    # ---- booking rules ----
    if check_past and start < datetime.now():
        add("RULE", "Cannot book a time in the past.")
    if start.hour < config.BUSINESS_START_HOUR or end.hour * 60 + end.minute > config.BUSINESS_END_HOUR * 60:
        add("RULE", f"Bookings must be between {config.BUSINESS_START_HOUR:02d}:00 and {config.BUSINESS_END_HOUR:02d}:00.")
    if (end - start).total_seconds() / 3600 > room["max_hours"]:
        add("RULE", f"Maximum booking length for this room is {room['max_hours']} hours.")
    if attendees < 1:
        add("RULE", "Attendees must be at least 1.")
    if attendees > room["capacity"]:
        add("RULE", f"Room capacity is {room['capacity']} but {attendees} attendees requested.")
    facility = conn.execute("SELECT * FROM facilities WHERE id=?", (room["facility_id"],)).fetchone()
    if room["status"] != "ACTIVE" or facility["status"] != "ACTIVE":
        add("STATUS", "Room (or its facility) is not operational.")

    # ---- room conflicts: existing bookings + maintenance/blocked slots ----
    for b in av.room_booking_conflicts(conn, room["id"], start_s, end_s, exclude_ids):
        add("ROOM_BOOKED", f"Room already booked {b['start_time'][11:]}-{b['end_time'][11:]} by {b['booked_by']} ({b['status']}).")
    for m in av.room_blocks(conn, room["id"], start_s, end_s):
        add("ROOM_MAINTENANCE", f"Room is {m['kind'].lower()} {m['start_time']} to {m['end_time']} ({m['reason'] or 'no reason given'}).")

    # ---- equipment conflicts ----
    for eid in equipment_ids:
        eq = conn.execute("SELECT * FROM equipment WHERE id=?", (eid,)).fetchone()
        if not eq or eq["status"] != "ACTIVE":
            add("STATUS", f"Equipment #{eid} is not available.")
            continue
        if eq["facility_id"] != room["facility_id"]:
            add("RULE", f"{eq['name']} belongs to a different facility than the room.")
            continue
        if eq["room_id"] is not None and eq["room_id"] != room["id"]:
            add("RULE", f"{eq['name']} is fixed in another room.")
            continue
        if eq["room_id"] == room["id"]:
            continue                                  # fixed in THIS room: comes with the room
        for b in av.equipment_booking_conflicts(conn, eid, start_s, end_s, exclude_ids):
            add("EQUIPMENT_BOOKED", f"{eq['name']} already reserved {b['start_time'][11:]}-{b['end_time'][11:]} by {b['booked_by']}.")
        for m in av.equipment_blocks(conn, eid, start_s, end_s):
            add("EQUIPMENT_MAINTENANCE", f"{eq['name']} unavailable ({m['reason'] or m['kind'].lower()}).")
    return problems


def check_occurrences(conn, room, occurrences, attendees, equipment_ids, exclude_ids=(), check_past=True):
    """occurrences = [(start_s, end_s), ...]. Returns only the occurrences that have problems."""
    bad = []
    for s, e in occurrences:
        p = validate_slot(conn, room, s, e, attendees, equipment_ids, exclude_ids, check_past)
        if p:
            bad.append({"start": s, "end": e, "problems": p})
    return bad


def suggest_alternatives(conn, room, start_s, end_s, attendees):
    start, end = datetime.strptime(start_s, FMT), datetime.strptime(end_s, FMT)
    other_rooms = [x for x in av.available_rooms(conn, start_s, end_s, attendees) if x["room"]["id"] != room["id"]]
    same_room_slots = av.next_free_slots(conn, room["id"], start.strftime("%Y-%m-%d"),
                                         int((end - start).total_seconds() // 60))
    return {"rooms": other_rooms[:4], "slots": same_room_slots}
