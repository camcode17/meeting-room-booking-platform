# Corporate Meeting Room & Facility Booking Platform

A web application that lets employees of a multi-office organisation find, request and manage meeting rooms and shared equipment, while preventing double bookings. It includes approval workflows for high-capacity or special rooms, recurring reservations, cancellation rules, complete audit history, and utilization analytics for facility managers.

**Core engine:** the Facility Availability & Booking Conflict Engine (`services/conflict_engine.py`), which checks business rules, room clashes, maintenance periods and equipment clashes for every requested time slot.

Built with **Python, Flask, SQLite, HTML/CSS (Jinja templates)**.

## Features
- **Facility, room and equipment management** with location, type, capacity, status and booking rules
- **Availability search** by date, time, capacity and equipment needs
- **Conflict detection** across rooms, equipment, maintenance periods and blocked slots, with alternative rooms and times suggested
- **Approval workflow** routed by configurable rules (high-capacity rooms go to the facility team, long bookings to the manager)
- **Recurring bookings** (every weekday or weekly), validated for every date before confirmation
- **Edit and cancel bookings**, with cancellation rules; cancelled slots are released immediately
- **Role-based access:** Employee, Manager, Facility Admin
- **Analytics:** room utilization, booking frequency, cancellation rate, peak hours and equipment usage
- **Audit history** of every booking, approval, cancellation and change, with user and timestamp
- **Automated tests** covering the main business rules

## How to run
```
python -m venv venv
venv\Scripts\activate        # Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
python app.py
```
Then open http://127.0.0.1:5000. Demo data is created automatically on first run.

Run the tests:
```
python -m unittest discover -s tests -v
```

## Demo users
| User | Role | Can do |
|---|---|---|
| Ravi Kumar, Sara Khan, Kiran Shah | Employee | Search, book, edit, cancel own bookings |
| Mohan Iyer | Manager | Everything above, plus approve team requests and view analytics |
| Anita Rao, Dev Menon | Facility Admin | Everything above, plus manage facilities, rooms, equipment, maintenance, and view the audit log |

## Project structure
| Module | File | Database tables |
|---|---|---|
| Facility, room, equipment, employee management | `services/facility_service.py` | facilities, rooms, equipment, employees |
| Booking requests and changes | `services/booking_service.py` | bookings, booking_equipment |
| Availability management | `services/availability.py` | maintenance_blocks, bookings |
| Conflict detection engine | `services/conflict_engine.py` | reads all of the above |
| Approval management | `services/approval_service.py` | approvals |
| Cancellation and recurring bookings | `services/booking_service.py` | bookings |
| Facility analytics | `services/analytics.py` | reads bookings |
| Audit history | `services/audit.py` | audit_log |
| Business rules and thresholds | `config.py` | none |

## Key design decisions
- Two time periods overlap when `start1 < end2 AND start2 < end1`, so back-to-back bookings are allowed.
- Only pending and approved bookings block a slot, so cancelling or rejecting a booking frees the room instantly.
- Booking checks and inserts run in one database transaction to prevent two users taking the same slot at the same moment.
- Recurring requests are all-or-nothing: if any date conflicts, nothing is saved.

## Limitations and future work
Login is a role picker for demonstration (no passwords), there is no CSRF protection, and there are no email notifications. These would be the next steps for a production version.

## Author
Ayushi 
Avni