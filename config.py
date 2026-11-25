"""Central configuration = the 'configured rules' mentioned in the problem statement.
Change a value here and the whole system's behaviour changes (approval, cancellation, recurrence...)."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("BOOKING_DB", os.path.join(BASE_DIR, "booking.db"))
SECRET_KEY = "change-me-in-production"

DT_FMT = "%Y-%m-%d %H:%M"          # all date-times are stored as text in this format (sorts correctly!)

BUSINESS_START_HOUR = 8             # rooms can only be booked between 08:00 ...
BUSINESS_END_HOUR = 20              # ... and 20:00

# Approval rules
APPROVAL_CAPACITY_THRESHOLD = 20    # rooms with capacity >= 20 need FACILITY_TEAM approval
LONG_BOOKING_HOURS = 4              # bookings longer than 4h by a normal employee need MANAGER approval

# Cancellation rule
MIN_CANCEL_NOTICE_MINUTES = 30      # employees can't cancel within 30 min of start (admins can)

# Recurrence rule
MAX_RECURRENCE_COUNT = 26
