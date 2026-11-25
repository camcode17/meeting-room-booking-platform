"""WEB LAYER (controllers). Each route: check permission -> call a service -> render a template.
No business rules live here; they live in services/. Run:  python app.py  -> http://127.0.0.1:5000"""
import os
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, g, render_template, request, redirect, url_for, session, flash, abort

import config, database, seed
from services import (audit, availability, booking_service, approval_service, facility_service, analytics)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY


# ---------- plumbing: DB per request, login (simple "login as employee" for the demo) ----------
def db():
    if "db" not in g:
        g.db = database.get_db()
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


@app.before_request
def load_user():
    g.user = None
    if session.get("uid"):
        g.user = db().execute("SELECT * FROM employees WHERE id=?", (session["uid"],)).fetchone()
    if g.user is None and request.endpoint not in ("login", "static"):
        return redirect(url_for("login"))


@app.context_processor
def inject_user():
    return {"user": g.get("user")}


def roles(*allowed):
    """Employee Management -> permissions: restrict a route to certain roles."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if g.user["role"] not in allowed:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        session["uid"] = int(request.form["employee_id"])
        return redirect(url_for("dashboard"))
    return render_template("login.html", employees=db().execute("SELECT * FROM employees ORDER BY role, name").fetchall())


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- dashboard ----------
@app.route("/")
def dashboard():
    now = datetime.now().strftime(config.DT_FMT)
    upcoming = db().execute(
        """SELECT b.*, r.name AS room_name FROM bookings b JOIN rooms r ON r.id=b.room_id
           WHERE b.employee_id=? AND b.status IN ('PENDING','APPROVED') AND b.start_time>=?
           ORDER BY b.start_time LIMIT 6""", (g.user["id"], now)).fetchall()
    pending = len(approval_service.pending_for(db(), g.user)) if g.user["role"] != "EMPLOYEE" else 0
    return render_template("dashboard.html", upcoming=upcoming, pending=pending)


# ---------- Availability Management: search by date/time/capacity/equipment ----------
@app.route("/availability")
def availability_page():
    f = {"date": request.args.get("date", datetime.now().strftime("%Y-%m-%d")),
         "start": request.args.get("start", "10:00"), "end": request.args.get("end", "11:00"),
         "capacity": request.args.get("capacity", "1"), "types": request.args.getlist("types")}
    types = [r["type"] for r in db().execute("SELECT DISTINCT type FROM equipment ORDER BY type")]
    rooms = items = None
    searched = "date" in request.args
    if searched:
        s, e = f"{f['date']} {f['start']}", f"{f['date']} {f['end']}"
        problems = []
        if e <= s:
            flash("End time must be after start time.", "error")
        else:
            rooms = availability.available_rooms(db(), s, e, int(f["capacity"] or 1), f["types"])
            items = availability.portable_equipment_status(db(), s, e)
    return render_template("availability.html", f=f, types=types, rooms=rooms, items=items, searched=searched)


# ---------- Booking Request / Recurring ----------
@app.route("/book", methods=["GET", "POST"])
def book():
    src = request.form if request.method == "POST" else request.args
    f = {"room_id": src.get("room_id", ""), "date": src.get("date", (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")),
         "start": src.get("start", "10:00"), "end": src.get("end", "11:00"), "attendees": src.get("attendees", "4"),
         "purpose": src.get("purpose", ""), "recurrence": src.get("recurrence", "NONE"), "count": src.get("count", "1"),
         "equipment": [int(x) for x in src.getlist("equipment")]}
    rooms = db().execute("""SELECT r.*, f.name AS facility_name FROM rooms r JOIN facilities f ON f.id=r.facility_id
                            WHERE r.status='ACTIVE' AND f.status='ACTIVE' ORDER BY f.name, r.capacity""").fetchall()
    equipment = db().execute("""SELECT q.*, f.name AS facility_name FROM equipment q JOIN facilities f ON f.id=q.facility_id
                                WHERE q.room_id IS NULL AND q.status='ACTIVE' ORDER BY q.facility_id, q.type""").fetchall()
    result = None
    if request.method == "POST":
        try:
            result = booking_service.create_booking(
                db(), g.user, int(f["room_id"] or 0), f["date"], f["start"], f["end"], f["purpose"],
                int(f["attendees"] or 0), f["equipment"], f["recurrence"], int(f["count"] or 1))
        except ValueError:
            result = {"ok": False, "errors": ["Invalid number entered."], "conflicts": [], "alternatives": None}
        if result["ok"]:
            msg = ("Request submitted and awaiting approval." if result["status"] == "PENDING"
                   else "Booking confirmed.") + f" ({len(result['booking_ids'])} occurrence(s))"
            flash(msg, "ok")
            return redirect(url_for("my_bookings"))
    return render_template("book.html", f=f, rooms=rooms, equipment=equipment, result=result)


# ---------- My bookings + Cancellation ----------
@app.route("/bookings")
def my_bookings():
    show_all = request.args.get("all") == "1" and g.user["role"] == "FACILITY_ADMIN"
    return render_template("bookings.html", bookings=booking_service.list_bookings(db(), g.user, show_all),
                           show_all=show_all, now=datetime.now().strftime(config.DT_FMT))


@app.route("/bookings/<int:bid>/cancel", methods=["POST"])
def cancel(bid):
    ok, msg = booking_service.cancel_booking(db(), g.user, bid, series=request.form.get("series") == "1")
    flash(msg, "ok" if ok else "error")
    return redirect(request.referrer or url_for("my_bookings"))



@app.route("/bookings/<int:bid>/edit", methods=["GET", "POST"])
def edit_booking(bid):
    """Change/reschedule one booking (owner or facility admin)."""
    b = db().execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    if not b or (b["employee_id"] != g.user["id"] and g.user["role"] != "FACILITY_ADMIN"):
        abort(404)
    src = request.form if request.method == "POST" else {}
    f = {"room_id": src.get("room_id", b["room_id"]), "date": src.get("date", b["start_time"][:10]),
         "start": src.get("start", b["start_time"][11:]), "end": src.get("end", b["end_time"][11:]),
         "attendees": src.get("attendees", b["attendees"]), "purpose": src.get("purpose", b["purpose"]),
         "equipment": ([int(x) for x in request.form.getlist("equipment")] if request.method == "POST" else
                       [r["equipment_id"] for r in db().execute("SELECT equipment_id FROM booking_equipment WHERE booking_id=?", (bid,))])}
    rooms = db().execute("""SELECT r.*, f.name AS facility_name FROM rooms r JOIN facilities f ON f.id=r.facility_id
                            WHERE r.status='ACTIVE' AND f.status='ACTIVE' ORDER BY f.name, r.capacity""").fetchall()
    equipment = db().execute("""SELECT q.*, f.name AS facility_name FROM equipment q JOIN facilities f ON f.id=q.facility_id
                                WHERE q.room_id IS NULL AND q.status='ACTIVE' ORDER BY q.facility_id, q.type""").fetchall()
    result = None
    if request.method == "POST":
        try:
            result = booking_service.modify_booking(db(), g.user, bid, int(f["room_id"] or 0), f["date"], f["start"],
                                                    f["end"], f["purpose"], int(f["attendees"] or 0), f["equipment"])
        except ValueError:
            result = {"ok": False, "errors": ["Invalid number entered."], "conflicts": [], "alternatives": None}
        if result["ok"]:
            flash("Booking updated." + (" It needs approval again." if result["status"] == "PENDING" else ""), "ok")
            return redirect(url_for("my_bookings"))
    return render_template("edit.html", f=f, bid=bid, rooms=rooms, equipment=equipment, result=result)

# ---------- Approval Management ----------
@app.route("/approvals")
@roles("MANAGER", "FACILITY_ADMIN")
def approvals():
    return render_template("approvals.html", queue=approval_service.pending_for(db(), g.user))


@app.route("/approvals/<int:bid>", methods=["POST"])
@roles("MANAGER", "FACILITY_ADMIN")
def decide(bid):
    decision = "APPROVED" if request.form.get("decision") == "approve" else "REJECTED"
    ok, msg = approval_service.decide(db(), g.user, bid, decision, request.form.get("comment", ""))
    flash(msg, "ok" if ok else "error")
    return redirect(url_for("approvals"))


# ---------- Facility / Room / Equipment / Employee / Maintenance admin ----------
@app.route("/admin")
@roles("FACILITY_ADMIN")
def admin():
    return render_template("admin.html", **facility_service.lists(db()))


@app.route("/admin/<kind>", methods=["POST"])
@roles("FACILITY_ADMIN")
def admin_action(kind):
    F, c = request.form, db()
    try:
        if kind == "facility":
            facility_service.add_facility(c, g.user, F["name"], F["location"], F["type"], int(F["capacity"] or 0))
        elif kind == "room":
            facility_service.add_room(c, g.user, int(F["facility_id"]), F["name"], F["floor"], int(F["capacity"]),
                                      1 if F.get("requires_approval") else 0, int(F["max_hours"]))
        elif kind == "equipment":
            facility_service.add_equipment(c, g.user, F["name"], F["type"], int(F["facility_id"]), int(F["room_id"] or 0))
        elif kind == "employee":
            facility_service.add_employee(c, g.user, F["name"], F["email"], F["role"], int(F["manager_id"] or 0))
        elif kind == "status":
            facility_service.toggle_status(c, g.user, F["table"], int(F["id"]))
        elif kind == "block":
            s, e = f"{F['date']} {F['start']}", f"{F['date']} {F['end']}"
            if e <= s:
                raise ValueError("End must be after start.")
            n = facility_service.add_block(c, g.user, int(F["room_id"] or 0), int(F["equipment_id"] or 0), s, e,
                                           F["kind"], F["reason"])
            flash(f"Block added. WARNING: {n} existing booking(s) overlap it - review them." if n else "Block added.",
                  "error" if n else "ok")
            return redirect(url_for("admin"))
        else:
            abort(404)
        flash("Saved.", "ok")
    except Exception as ex:                       # e.g. duplicate email, bad number
        c.rollback()
        flash(f"Could not save: {ex}", "error")
    return redirect(url_for("admin"))


# ---------- Facility Analytics ----------
@app.route("/analytics")
@roles("MANAGER", "FACILITY_ADMIN")
def analytics_page():
    today = datetime.now()
    d_from = request.args.get("from", (today - timedelta(days=20)).strftime("%Y-%m-%d"))
    d_to = request.args.get("to", today.strftime("%Y-%m-%d"))
    return render_template("analytics.html", r=analytics.report(db(), d_from, d_to), d_from=d_from, d_to=d_to)


# ---------- Audit History ----------
@app.route("/audit")
@roles("FACILITY_ADMIN")
def audit_page():
    return render_template("audit.html", rows=audit.recent(db(), 300, request.args.get("entity") or None))


if not os.path.exists(config.DB_PATH):            # first run: create tables + demo data
    database.init_db()
    _c = database.get_db()
    seed.seed(_c)
    _c.close()

if __name__ == "__main__":
    app.run(debug=True)
