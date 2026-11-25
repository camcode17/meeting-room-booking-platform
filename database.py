"""DATABASE LAYER: connection helper + table definitions (schema).
Every module in the problem statement maps to one or more tables below."""
import sqlite3
import config

SCHEMA = """
-- Facility Management: offices / sites
CREATE TABLE IF NOT EXISTS facilities(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL,
  type TEXT NOT NULL, capacity INTEGER,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','INACTIVE')));

-- Meeting Room Management: rooms live inside a facility; carry booking rules
CREATE TABLE IF NOT EXISTS rooms(
  id INTEGER PRIMARY KEY, facility_id INTEGER NOT NULL REFERENCES facilities(id),
  name TEXT NOT NULL, floor TEXT, capacity INTEGER NOT NULL,
  requires_approval INTEGER NOT NULL DEFAULT 0,      -- special room => always needs approval
  max_hours INTEGER NOT NULL DEFAULT 8,              -- booking rule: max duration
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','INACTIVE')));

-- Equipment Management: room_id set = fixed in a room; room_id NULL = portable, separately reservable
CREATE TABLE IF NOT EXISTS equipment(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL,
  facility_id INTEGER NOT NULL REFERENCES facilities(id),
  room_id INTEGER REFERENCES rooms(id),
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','INACTIVE')));

-- Employee Management: role drives permissions
CREATE TABLE IF NOT EXISTS employees(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('EMPLOYEE','MANAGER','FACILITY_ADMIN')),
  manager_id INTEGER REFERENCES employees(id));

-- Booking Request Management (+ Recurring: occurrences share recurrence_group)
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
  room_id INTEGER NOT NULL REFERENCES rooms(id),
  start_time TEXT NOT NULL, end_time TEXT NOT NULL,
  purpose TEXT NOT NULL, attendees INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED','CANCELLED')),
  approval_required INTEGER NOT NULL DEFAULT 0,
  recurrence_group TEXT,
  created_at TEXT NOT NULL, cancelled_at TEXT, cancelled_by INTEGER);
CREATE INDEX IF NOT EXISTS idx_bookings_room_time ON bookings(room_id, start_time, end_time);

CREATE TABLE IF NOT EXISTS booking_equipment(
  booking_id INTEGER NOT NULL REFERENCES bookings(id),
  equipment_id INTEGER NOT NULL REFERENCES equipment(id),
  PRIMARY KEY(booking_id, equipment_id));

-- Availability Management: maintenance periods and blocked slots (for a room OR a piece of equipment)
CREATE TABLE IF NOT EXISTS maintenance_blocks(
  id INTEGER PRIMARY KEY, room_id INTEGER REFERENCES rooms(id),
  equipment_id INTEGER REFERENCES equipment(id),
  start_time TEXT NOT NULL, end_time TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'MAINTENANCE' CHECK(kind IN ('MAINTENANCE','BLOCKED')),
  reason TEXT, created_by INTEGER,
  CHECK(room_id IS NOT NULL OR equipment_id IS NOT NULL));

-- Approval Management: one row per booking that needs a decision
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id),
  approver_role TEXT NOT NULL CHECK(approver_role IN ('MANAGER','FACILITY_TEAM')),
  reason TEXT,
  decision TEXT NOT NULL DEFAULT 'PENDING' CHECK(decision IN ('PENDING','APPROVED','REJECTED','WITHDRAWN')),
  decided_by INTEGER REFERENCES employees(id), decided_at TEXT, comment TEXT);

-- Audit History: append-only log (we never UPDATE/DELETE rows here)
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, user_id INTEGER, entity TEXT NOT NULL, entity_id INTEGER,
  action TEXT NOT NULL, details TEXT, timestamp TEXT NOT NULL);
"""


def get_db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row          # lets us use row['column_name']
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
