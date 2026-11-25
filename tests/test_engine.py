"""Run:  python -m unittest discover -s tests -v
Each test proves one rule from the problem statement, using a throw-away database."""
import os, sys, tempfile, unittest
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
config.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")
import database, seed
from services import booking_service as bs, approval_service as ap, facility_service as fs, analytics


def next_weekday(n=3):
    d = datetime.now() + timedelta(days=n)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


class T(unittest.TestCase):
    def setUp(self):
        if os.path.exists(config.DB_PATH):
            os.remove(config.DB_PATH)
        database.init_db()
        self.c = database.get_db()
        seed.seed(self.c)
        # remove seeded bookings so tests start clean
        self.c.execute("DELETE FROM approvals"); self.c.execute("DELETE FROM bookings"); self.c.commit()
        g = lambda i: self.c.execute("SELECT * FROM employees WHERE id=?", (i,)).fetchone()
        self.admin, self.mgr, self.ravi, self.sara = g(1), g(3), g(4), g(5)
        self.day = next_weekday()

    def tearDown(self):
        self.c.close()          # Windows cannot delete a database file that is still open

    def book(self, user, room, s, e, **kw):
        return bs.create_booking(self.c, user, room, self.day, s, e, kw.pop("purpose", "x"), kw.pop("att", 2), **kw)

    def test_overlap_blocked(self):
        self.assertTrue(self.book(self.ravi, 2, "10:00", "11:00")["ok"])
        r = self.book(self.sara, 2, "10:30", "11:30")
        self.assertFalse(r["ok"]); self.assertEqual(r["conflicts"][0]["problems"][0]["kind"], "ROOM_BOOKED")

    def test_back_to_back_allowed(self):
        self.assertTrue(self.book(self.ravi, 2, "10:00", "11:00")["ok"])
        self.assertTrue(self.book(self.sara, 2, "11:00", "12:00")["ok"])

    def test_equipment_conflict_across_rooms(self):
        self.assertTrue(self.book(self.ravi, 2, "10:00", "11:00", equipment_ids=[5])["ok"])
        r = self.book(self.sara, 3, "10:00", "11:00", equipment_ids=[5])      # other room, same portable projector
        self.assertFalse(r["ok"]); self.assertEqual(r["conflicts"][0]["problems"][0]["kind"], "EQUIPMENT_BOOKED")

    def test_maintenance_blocks(self):
        fs.add_block(self.c, self.admin, 2, 0, f"{self.day} 09:00", f"{self.day} 12:00", "MAINTENANCE", "AC repair")
        r = self.book(self.ravi, 2, "10:00", "11:00")
        self.assertEqual(r["conflicts"][0]["problems"][0]["kind"], "ROOM_MAINTENANCE")

    def test_cancel_frees_slot(self):
        r = self.book(self.ravi, 2, "10:00", "11:00")
        ok, _ = bs.cancel_booking(self.c, self.ravi, r["booking_ids"][0]); self.assertTrue(ok)
        self.assertTrue(self.book(self.sara, 2, "10:00", "11:00")["ok"])

    def test_only_owner_can_cancel(self):
        r = self.book(self.ravi, 2, "10:00", "11:00")
        self.assertFalse(bs.cancel_booking(self.c, self.sara, r["booking_ids"][0])[0])

    def test_recurring_all_or_nothing(self):
        d = datetime.strptime(self.day, "%Y-%m-%d") + timedelta(weeks=2)       # block the 3rd weekly occurrence
        fs.add_block(self.c, self.admin, 2, 0, f"{d:%Y-%m-%d} 09:00", f"{d:%Y-%m-%d} 12:00", "BLOCKED", "event")
        r = self.book(self.ravi, 2, "10:00", "11:00", recurrence="WEEKLY", count=4)
        self.assertFalse(r["ok"]); self.assertEqual(len(r["conflicts"]), 1)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM bookings").fetchone()[0], 0)

    def test_recurring_ok_and_series_cancel(self):
        r = self.book(self.ravi, 2, "10:00", "11:00", recurrence="WEEKLY", count=3)
        self.assertEqual(len(r["booking_ids"]), 3)
        bs.cancel_booking(self.c, self.ravi, r["booking_ids"][0], series=True)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM bookings WHERE status='CANCELLED'").fetchone()[0], 3)

    def test_approval_flow_high_capacity(self):
        r = self.book(self.ravi, 4, "10:00", "11:00", att=15)                  # Boardroom cap 20 -> approval
        self.assertEqual(r["status"], "PENDING")
        self.assertFalse(ap.decide(self.c, self.mgr, r["booking_ids"][0], "APPROVED")[0])   # manager not allowed
        self.assertTrue(ap.decide(self.c, self.admin, r["booking_ids"][0], "APPROVED")[0])
        self.assertEqual(self.c.execute("SELECT status FROM bookings").fetchone()[0], "APPROVED")

    def test_pending_holds_slot_and_reject_releases(self):
        r = self.book(self.ravi, 4, "10:00", "11:00", att=15)
        self.assertFalse(self.book(self.sara, 4, "10:00", "11:00", att=5)["ok"])
        ap.decide(self.c, self.admin, r["booking_ids"][0], "REJECTED", "no")
        self.assertTrue(self.book(self.sara, 4, "10:00", "11:00", att=5)["ok"])

    def test_long_booking_goes_to_manager(self):
        r = self.book(self.ravi, 2, "09:00", "14:00", att=4)
        self.assertEqual(r["status"], "PENDING")
        self.assertTrue(ap.decide(self.c, self.mgr, r["booking_ids"][0], "APPROVED")[0])

    def test_capacity_rule_and_audit_and_analytics(self):
        self.assertFalse(self.book(self.ravi, 1, "10:00", "11:00", att=10)["ok"])   # huddle room cap 4
        self.book(self.ravi, 2, "10:00", "11:00")
        n = self.c.execute("SELECT COUNT(*) FROM audit_log WHERE action='CREATED'").fetchone()[0]
        self.assertGreaterEqual(n, 1)
        rep = analytics.report(self.c, self.day, self.day)
        self.assertEqual(rep["total_bookings"], 1)

    def test_modify_booking(self):
        r = self.book(self.ravi, 2, "10:00", "11:00"); bid = r["booking_ids"][0]
        self.book(self.sara, 2, "13:00", "14:00")
        mod = lambda s, e, room=2, att=2: bs.modify_booking(self.c, self.ravi, bid, room, self.day, s, e, "x", att)
        self.assertTrue(mod("10:30", "11:30")["ok"])                       # overlaps only its OWN old slot -> allowed
        self.assertFalse(mod("13:00", "14:00")["conflicts"] == [])         # clashes with Sara -> blocked
        self.assertEqual(self.c.execute("SELECT start_time FROM bookings WHERE id=?", (bid,)).fetchone()[0][11:], "10:30")
        self.assertEqual(mod("10:00", "11:00", room=4, att=15)["status"], "PENDING")   # moved to big room -> approval
        self.assertTrue(self.c.execute("SELECT 1 FROM audit_log WHERE action='MODIFIED'").fetchone())
        self.assertFalse(bs.modify_booking(self.c, self.sara, bid, 2, self.day, "10:00", "11:00", "x", 2)["ok"])


if __name__ == "__main__":
    unittest.main()