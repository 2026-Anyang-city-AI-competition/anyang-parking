import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.daily_data_audit import audit

KST = timezone(timedelta(hours=9))


def build(rows, path):
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE obs(ts_kst TEXT, parking_id INTEGER,
                       cell_cnt INT, park_count INT, PRIMARY KEY(ts_kst, parking_id))""")
        con.executemany("INSERT INTO obs VALUES (?,?,?,?)", rows)


def series(parking_id, counts, cells=100, start=None, step=5):
    """5분 간격 관측 열. start 를 안 주면 지금부터 거슬러 올라간다."""
    start = start or datetime.now(KST) - timedelta(minutes=step * len(counts))
    return [((start + timedelta(minutes=step * i)).isoformat(), parking_id, cells, value)
            for i, value in enumerate(counts)]


class DailyAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "parking.db"

    def codes(self, findings):
        return {code for code, _, count in findings if count > 0}

    def test_healthy_feed_reports_nothing(self):
        counts = [40 + (i % 7) for i in range(240)]
        build(series(1, counts), self.db)
        findings, per_lot, summary = audit(self.db, hours=24)
        self.assertNotIn("over_capacity", self.codes(findings))
        self.assertNotIn("step_change", self.codes(findings))
        self.assertEqual(summary["moving_lots"], 1)
        self.assertFalse(per_lot.iloc[0]["frozen"])

    def test_over_capacity_is_flagged(self):
        counts = [40] * 100 + [120] * 20        # 정원 100면인데 120대
        build(series(1, counts), self.db)
        findings, per_lot, _ = audit(self.db, hours=24)
        self.assertIn("over_capacity", self.codes(findings))
        self.assertEqual(int(per_lot.iloc[0]["over_capacity"]), 20)

    def test_step_change_is_flagged(self):
        counts = [10] * 50 + [95] + [95] * 49   # 5분 만에 10 → 95
        build(series(1, counts), self.db)
        findings, per_lot, _ = audit(self.db, hours=24)
        self.assertIn("step_change", self.codes(findings))
        self.assertGreaterEqual(int(per_lot.iloc[0]["step_changes"]), 1)

    def test_frozen_lot_is_counted_but_not_called_broken(self):
        # 항상 만차일 수도 있다. 세어서 보고만 하고 문제로 올리지 않는다.
        build(series(1, [100] * 240), self.db)
        findings, per_lot, summary = audit(self.db, hours=24)
        self.assertEqual(summary["frozen_lots"], 1)
        self.assertTrue(per_lot.iloc[0]["frozen"])
        self.assertNotIn("frozen_count", self.codes(findings))

    def test_polling_gap_is_flagged(self):
        now = datetime.now(KST)
        early = series(1, [40] * 100, start=now - timedelta(hours=20))
        late = series(1, [40] * 100, start=now - timedelta(hours=8))
        build(early + late, self.db)
        findings, _, summary = audit(self.db, hours=24)
        self.assertIn("polling_gap", self.codes(findings))
        self.assertGreater(summary["lost_hours"], 1)

    def test_empty_window_is_reported_not_crashed(self):
        build(series(1, [40] * 10, start=datetime.now(KST) - timedelta(days=30)), self.db)
        findings, per_lot, _ = audit(self.db, hours=24)
        self.assertIn("no_data", self.codes(findings))
        self.assertTrue(per_lot.empty)

    def test_zero_capacity_rows_do_not_divide_by_zero(self):
        rows = series(1, [40] * 120) + [(datetime.now(KST).isoformat(), 2, 0, 5)]
        build(rows, self.db)
        findings, per_lot, _ = audit(self.db, hours=24)
        self.assertNotIn(2, set(per_lot.parking_id))


if __name__ == "__main__":
    unittest.main()
