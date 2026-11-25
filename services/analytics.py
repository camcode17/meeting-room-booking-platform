"""FACILITY ANALYTICS module - read-only reports for management.
Utilization % = approved booked hours / available hours (weekdays x business hours) in the date range."""
from datetime import datetime, timedelta
import config


def _weekdays(d1, d2):
    n, d = 0, d1
    while d <= d2:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


def report(conn, date_from, date_to):
    d1, d2 = datetime.strptime(date_from, "%Y-%m-%d"), datetime.strptime(date_to, "%Y-%m-%d")
    lo, hi = date_from + " 00:00", (d2 + timedelta(days=1)).strftime("%Y-%m-%d") + " 00:00"
    avail_hours = max(_weekdays(d1, d2), 1) * (config.BUSINESS_END_HOUR - config.BUSINESS_START_HOUR)

    # 1) Room utilization + booking frequency + cancellation rate  (one SQL query, grouped by room)
    rooms = conn.execute(
        """SELECT r.id, r.name, r.capacity, f.name AS facility_name,
             COALESCE(SUM(CASE WHEN b.status='APPROVED'
                  THEN (julianday(b.end_time)-julianday(b.start_time))*24 END),0) AS hours,
             SUM(b.status='APPROVED') AS approved,
             SUM(b.status='CANCELLED') AS cancelled,
             COUNT(b.id) AS total
           FROM rooms r JOIN facilities f ON f.id=r.facility_id
           LEFT JOIN bookings b ON b.room_id=r.id AND b.start_time >= ? AND b.start_time < ?
           GROUP BY r.id ORDER BY hours DESC""", (lo, hi)).fetchall()
    room_rows = []
    for r in rooms:
        util = round(100 * r["hours"] / avail_hours, 1)
        label = "High demand" if util >= 60 else ("Under-utilized" if util < 25 else "Normal")
        room_rows.append({"name": r["name"], "facility": r["facility_name"], "capacity": r["capacity"],
                          "hours": round(r["hours"], 1), "util": util, "bookings": r["total"] or 0,
                          "cancel_rate": round(100 * (r["cancelled"] or 0) / r["total"], 1) if r["total"] else 0,
                          "label": label})

    # 2) Peak demand: count approved bookings overlapping each hour / each weekday
    bookings = conn.execute("SELECT start_time, end_time FROM bookings WHERE status='APPROVED' AND start_time>=? AND start_time<?",
                            (lo, hi)).fetchall()
    by_hour = {h: 0 for h in range(config.BUSINESS_START_HOUR, config.BUSINESS_END_HOUR)}
    by_day = {i: 0 for i in range(5)}
    for b in bookings:
        s, e = datetime.strptime(b["start_time"], config.DT_FMT), datetime.strptime(b["end_time"], config.DT_FMT)
        for h in by_hour:
            if s.hour <= h < (e.hour + (1 if e.minute else 0)):
                by_hour[h] += 1
        if s.weekday() < 5:
            by_day[s.weekday()] += 1

    # 3) Equipment usage
    equipment = conn.execute(
        """SELECT q.name, q.type, COUNT(b.id) AS uses FROM equipment q
           LEFT JOIN booking_equipment be ON be.equipment_id=q.id
           LEFT JOIN bookings b ON b.id=be.booking_id AND b.status='APPROVED' AND b.start_time>=? AND b.start_time<?
           WHERE q.room_id IS NULL GROUP BY q.id ORDER BY uses DESC""", (lo, hi)).fetchall()

    total = sum(r["bookings"] for r in room_rows)
    cancelled = conn.execute("SELECT COUNT(*) FROM bookings WHERE status='CANCELLED' AND start_time>=? AND start_time<?",
                             (lo, hi)).fetchone()[0]
    return {
        "rooms": room_rows, "by_hour": by_hour,
        "by_day": {["Mon", "Tue", "Wed", "Thu", "Fri"][k]: v for k, v in by_day.items()},
        "equipment": equipment, "total_bookings": total,
        "overall_cancel_rate": round(100 * cancelled / total, 1) if total else 0,
        "avg_util": round(sum(r["util"] for r in room_rows) / len(room_rows), 1) if room_rows else 0,
        "available_hours": avail_hours,
    }
