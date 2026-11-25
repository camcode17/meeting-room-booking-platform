"""Demo data so the app isn't empty. Run automatically on first start (or: python seed.py)."""
import random
from datetime import datetime, timedelta
import database
from services import audit


def seed(conn):
    random.seed(42)
    q = conn.execute
    q("INSERT INTO facilities(name,location,type,capacity) VALUES('Bengaluru HQ','Bengaluru','Corporate Office',400)")
    q("INSERT INTO facilities(name,location,type,capacity) VALUES('Pune Tech Park','Pune','Branch Office',150)")
    rooms = [  # facility, name, floor, capacity, requires_approval, max_hours
        (1, "Cubicle Huddle A", "1", 4, 0, 4), (1, "Aspen", "2", 8, 0, 8), (1, "Birch", "2", 12, 0, 8),
        (1, "Boardroom", "5", 20, 0, 8), (1, "Auditorium", "G", 100, 0, 8), (1, "Executive Suite", "6", 10, 1, 8),
        (2, "Pune Alpha", "1", 6, 0, 8), (2, "Pune Beta", "2", 15, 0, 8)]
    q_rooms = "INSERT INTO rooms(facility_id,name,floor,capacity,requires_approval,max_hours) VALUES(?,?,?,?,?,?)"
    for r in rooms:
        q(q_rooms, r)
    eq = [  # name, type, facility, room (None = portable)
        ("Wall Projector - Aspen", "Projector", 1, 2), ("Wall Projector - Birch", "Projector", 1, 3),
        ("Polycom System - Boardroom", "Conference System", 1, 4), ("PA System - Auditorium", "Microphone", 1, 5),
        ("Portable Projector 1", "Projector", 1, None), ("Portable Projector 2", "Projector", 1, None),
        ("Wireless Mic Set 1", "Microphone", 1, None), ("Video Conf Kit 1", "Conference System", 1, None),
        ("Portable Projector P1", "Projector", 2, None), ("Wireless Mic Set P1", "Microphone", 2, None)]
    for e in eq:
        q("INSERT INTO equipment(name,type,facility_id,room_id) VALUES(?,?,?,?)", e)
    emps = [("Anita Rao", "anita@corp.com", "FACILITY_ADMIN", None), ("Dev Menon", "dev@corp.com", "FACILITY_ADMIN", None),
            ("Mohan Iyer", "mohan@corp.com", "MANAGER", None), ("Ravi Kumar", "ravi@corp.com", "EMPLOYEE", 3),
            ("Sara Khan", "sara@corp.com", "EMPLOYEE", 3), ("Kiran Shah", "kiran@corp.com", "EMPLOYEE", 3)]
    for e in emps:
        q("INSERT INTO employees(name,email,role,manager_id) VALUES(?,?,?,?)", e)

    # Historical + upcoming bookings (popular rooms get more) so analytics has something to show.
    popularity = {1: .3, 2: .8, 3: .6, 4: .5, 5: .1, 6: .1, 7: .4, 8: .2}
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    purposes = ["Sprint planning", "Client call", "1:1", "Design review", "Interview", "Team sync", "Training"]
    for off in range(-21, 8):
        day = today + timedelta(days=off)
        if day.weekday() >= 5:
            continue
        for rid in range(1, 9):
            if random.random() > popularity[rid] + 0.2:
                continue
            h = 8 + random.randint(0, 3)
            for _ in range(random.randint(1, 4)):
                dur = random.choice([1, 1, 2])
                if h + dur > 19:
                    break
                s, e = day.replace(hour=h), day.replace(hour=h + dur)
                status = "CANCELLED" if random.random() < .12 else "APPROVED"
                cur = q("""INSERT INTO bookings(employee_id,room_id,start_time,end_time,purpose,attendees,status,created_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (random.choice([3, 4, 5, 6]), rid, s.strftime("%Y-%m-%d %H:%M"), e.strftime("%Y-%m-%d %H:%M"),
                         random.choice(purposes), random.randint(2, rooms[rid - 1][3]), status,
                         (s - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")))
                audit.log(conn, 4, "booking", cur.lastrowid, "CREATED", "seed data")
                h += dur + random.choice([0, 1, 2])
    conn.commit()


if __name__ == "__main__":
    database.init_db()
    c = database.get_db()
    seed(c)
    c.close()
