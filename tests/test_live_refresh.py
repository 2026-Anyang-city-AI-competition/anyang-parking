import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.serve.predictor import Predictor


class LiveRefreshTests(unittest.TestCase):
    def test_recent_observations_replace_bundled_history_without_retraining(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parking.db"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE lots(
                    parking_id INTEGER PRIMARY KEY, name TEXT, div TEXT, grade INTEGER,
                    cell_cnt INTEGER, lat REAL, lng REAL, wdays_start TEXT, wdays_end TEXT,
                    wend_start TEXT, wend_end TEXT, oneday_amt INTEGER)""")
                db.execute("""CREATE TABLE obs(
                    ts_kst TEXT, parking_id INTEGER, cell_cnt INTEGER, park_count INTEGER,
                    PRIMARY KEY(ts_kst, parking_id))""")
                db.execute("INSERT INTO lots VALUES(31,'테스트노외','노외',1,100,37.4,126.9,"
                           "'09:00','18:00','09:00','18:00',16000)")
                start = datetime(2026, 9, 15, 9, 0, tzinfo=timezone(timedelta(hours=9)))
                db.executemany("INSERT INTO obs VALUES(?,?,?,?)", [
                    ((start+timedelta(minutes=5*i)).isoformat(), 31, 100, 10+i)
                    for i in range(20)
                ])

            predictor = Predictor.__new__(Predictor)
            predictor.ok = False
            predictor.dead, predictor.hist = set(), {}
            predictor._refresh_lock = threading.Lock()
            predictor._last_refresh_attempt = 0.0
            predictor.observation_at = predictor.refreshed_at = None
            predictor.last_refresh_error = None
            predictor.refresh_count = 0
            predictor.prediction_ready_lots = None
            predictor.total_lots = None

            status = predictor.refresh_from_db(path=path, force=True)
            self.assertIsNone(status["refresh_error"])
            self.assertEqual(status["live_lots"], 1)
            self.assertEqual(status["prediction_ready_lots"], 1)
            self.assertEqual(len(predictor.hist), 1)
            self.assertEqual(predictor.hist[31].iloc[-1].occ_now, 29)


if __name__ == "__main__":
    unittest.main()
