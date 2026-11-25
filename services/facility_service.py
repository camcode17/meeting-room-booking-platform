"""FACILITY, MEETING ROOM, EQUIPMENT, EMPLOYEE management + maintenance/blocked slots.
Only FACILITY_ADMIN may call these (enforced in app.py). Each change is audited."""
from services import audit


def add_facility(conn, user, name, location, ftype, capacity):
    cur = conn.execute("INSERT INTO facilities(name,location,type,capacity) VALUES(?,?,?,?)",
                       (name, location, ftype, capacity or None))
    audit.log(conn, user["id"], "facility", cur.lastrowid, "CREATED", name)
    conn.commit()


def add_room(conn, user, facility_id, name, floor, capacity, requires_approval, max_hours):
    cur = conn.execute("""INSERT INTO rooms(facility_id,name,floor,capacity,requires_approval,max_hours)
                          VALUES(?,?,?,?,?,?)""", (facility_id, name, floor, capacity, requires_approval, max_hours))
    audit.log(conn, user["id"], "room", cur.lastrowid, "CREATED", f"{name} cap={capacity}")
    conn.commit()


def add_equipment(conn, user, name, etype, facility_id, room_id):
    cur = conn.execute("INSERT INTO equipment(name,type,facility_id,room_id) VALUES(?,?,?,?)",
                       (name, etype, facility_id, room_id or None))
    audit.log(conn, user["id"], "equipment", cur.lastrowid, "CREATED", name)
    conn.commit()


def add_employee(conn, user, name, email, role, manager_id):
    cur = conn.execute("INSERT INTO employees(name,email,role,manager_id) VALUES(?,?,?,?)",
                       (name, email, role, manager_id or None))
    audit.log(conn, user["id"], "employee", cur.lastrowid, "CREATED", f"{name} ({role})")
    conn.commit()


def toggle_status(conn, user, table, row_id):
    assert table in ("facilities", "rooms", "equipment")          # whitelist: never trust table names from the web
    cur = conn.execute(f"SELECT status FROM {table} WHERE id=?", (row_id,)).fetchone()
    new = "INACTIVE" if cur["status"] == "ACTIVE" else "ACTIVE"
    conn.execute(f"UPDATE {table} SET status=? WHERE id=?", (new, row_id))
    audit.log(conn, user["id"], table, row_id, "STATUS_CHANGED", new)
    conn.commit()


def add_block(conn, user, room_id, equipment_id, start, end, kind, reason):
    """Adds a maintenance/blocked period and returns how many EXISTING bookings it overlaps (a warning)."""
    cur = conn.execute("""INSERT INTO maintenance_blocks(room_id,equipment_id,start_time,end_time,kind,reason,created_by)
                          VALUES(?,?,?,?,?,?,?)""", (room_id or None, equipment_id or None, start, end, kind, reason, user["id"]))
    if room_id:
        n = conn.execute("""SELECT COUNT(*) FROM bookings WHERE room_id=? AND status IN ('PENDING','APPROVED')
                            AND start_time < ? AND end_time > ?""", (room_id, end, start)).fetchone()[0]
    else:
        n = conn.execute("""SELECT COUNT(*) FROM booking_equipment be JOIN bookings b ON b.id=be.booking_id
                            WHERE be.equipment_id=? AND b.status IN ('PENDING','APPROVED')
                            AND b.start_time < ? AND b.end_time > ?""", (equipment_id, end, start)).fetchone()[0]
    audit.log(conn, user["id"], "maintenance", cur.lastrowid, "CREATED", f"{kind} {start} to {end}: {reason}")
    conn.commit()
    return n


def lists(conn):
    return {
        "facilities": conn.execute("SELECT * FROM facilities ORDER BY name").fetchall(),
        "rooms": conn.execute("""SELECT r.*, f.name AS facility_name FROM rooms r
                                 JOIN facilities f ON f.id=r.facility_id ORDER BY f.name, r.name""").fetchall(),
        "equipment": conn.execute("""SELECT q.*, f.name AS facility_name, r.name AS room_name FROM equipment q
                                     JOIN facilities f ON f.id=q.facility_id LEFT JOIN rooms r ON r.id=q.room_id
                                     ORDER BY q.type""").fetchall(),
        "employees": conn.execute("""SELECT e.*, m.name AS manager_name FROM employees e
                                     LEFT JOIN employees m ON m.id=e.manager_id ORDER BY e.role, e.name""").fetchall(),
        "blocks": conn.execute("""SELECT m.*, r.name AS room_name, q.name AS equipment_name FROM maintenance_blocks m
                                  LEFT JOIN rooms r ON r.id=m.room_id LEFT JOIN equipment q ON q.id=m.equipment_id
                                  ORDER BY m.start_time DESC LIMIT 30""").fetchall(),
    }
