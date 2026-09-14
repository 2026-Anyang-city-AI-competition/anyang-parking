import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.serve.request_polling import RequestPoller

KST = timezone(timedelta(hours=9))


def make_db(path, observed_at):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE obs(ts_kst TEXT, parking_id INTEGER, cell_cnt INTEGER, park_count INTEGER)")
        db.execute("INSERT INTO obs VALUES(?,?,?,?)", (observed_at, 1, 10, 2))


class RequestPollingTests(unittest.TestCase):
    def test_recent_database_observation_skips_network_poll(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parking.db"
            make_db(path, datetime.now(KST).isoformat())
            poller = RequestPoller(path)
            with patch("src.serve.request_polling.poll_once") as called:
                result = poller.poll_if_due()
            self.assertEqual(result["status"], "skipped")
            called.assert_not_called()

    def test_stale_database_polls_once_and_writes_before_return(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parking.db"
            old = (datetime.now(KST)-timedelta(minutes=10)).isoformat()
            make_db(path, old)

            def fake_poll(db):
                ts = datetime.now(KST).replace(microsecond=0).isoformat()
                db.execute("INSERT INTO obs VALUES(?,?,?,?)", (ts, 1, 10, 3))
                db.commit()
                return ts, 1

            poller = RequestPoller(path)
            with patch("src.serve.request_polling.poll_once", side_effect=fake_poll) as called:
                first = poller.poll_if_due()
                second = poller.poll_if_due()
            self.assertEqual(first["status"], "polled")
            self.assertEqual(second["status"], "skipped")
            self.assertEqual(called.call_count, 1)

    def test_poll_failure_keeps_existing_observation_available(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parking.db"
            old = (datetime.now(KST)-timedelta(minutes=10)).isoformat()
            make_db(path, old)
            with patch("src.serve.request_polling.poll_once", side_effect=TimeoutError):
                result = RequestPoller(path).poll_if_due()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["observation_at"], old)
            self.assertEqual(result["error"], "TimeoutError")


if __name__ == "__main__":
    unittest.main()
