"""실제 CSV/DB/외부 API 없이 재현하는 출입·관측·예측 회귀 테스트."""
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts.fill_access_schedule import apply_schedules, validate_rule_ids
from src.analysis import a18_continuity_check as a18
from src.analysis.a19_persistence_baseline import accessible_at, build_series, hhmm_to_min
from src.serve.predictor import HORIZ_GRID
from tests.predictor_stub import BlockAllAccuracy, bare_predictor
from src.serve.request_polling import RequestPoller


class AccessScheduleTests(unittest.TestCase):
    def test_confirmed_sunday_opening_survives_schedule_fill(self):
        rules = pd.DataFrame([
            {"parking_id": 48, "name": "안양역2노상", "access_rule": "SAME_AS_FEE_HOURS"},
            {"parking_id": 53, "name": "안양역1노상", "access_rule": "SAME_AS_FEE_HOURS"},
            {"parking_id": 14, "name": "호계3동노외", "access_rule": "SAME_AS_FEE_HOURS"},
        ])
        result = apply_schedules(rules).set_index("parking_id")
        sunday = datetime(2026, 9, 20, 12)
        self.assertEqual(sunday.weekday(), 6)
        for pid in (48, 53):
            row = result.loc[pid]
            self.assertEqual(row.sunday_access_start, "10:00")
            self.assertEqual(row.sunday_access_end, "22:00")
            self.assertTrue(accessible_at(row, sunday))
            self.assertFalse(accessible_at(row, sunday.replace(hour=22)))
        self.assertFalse(accessible_at(result.loc[14], sunday))
        self.assertNotIn("sunday_access_start", rules.columns)
        pd.testing.assert_frame_equal(apply_schedules(result.reset_index()), result.reset_index())

    def test_missing_or_ambiguous_hours_are_unknown(self):
        sunday = datetime(2026, 9, 20, 12)
        rule = {"access_rule": "SAME_AS_FEE_HOURS"}
        self.assertIsNone(accessible_at(rule, sunday))
        self.assertIsNone(accessible_at({
            **rule, "sunday_access_start": "00:00", "sunday_access_end": "00:00",
        }, sunday))
        self.assertFalse(accessible_at({**rule, "sunday_access_status": "closed"}, sunday))
        self.assertIsNone(hhmm_to_min("25:00"))
        self.assertIsNone(hhmm_to_min("24:01"))
        self.assertIsNone(hhmm_to_min("10:60"))
        self.assertEqual(hhmm_to_min("24:00"), 1440)

    def test_confirmed_free_access_is_not_overwritten(self):
        rules = pd.DataFrame([{
            "parking_id": 48, "name": "안양역2노상", "access_rule": "FREE_AFTER_FEE",
            "sunday_access_start": "00:00", "sunday_access_end": "24:00",
        }])
        result = apply_schedules(rules)
        self.assertEqual(result.iloc[0].sunday_access_start, "00:00")
        self.assertEqual(result.iloc[0].sunday_access_end, "24:00")

    def test_id_name_conflict_fails_before_writing(self):
        lots = pd.DataFrame([{"parking_id": 53, "name": "안양역1노상"}])
        wrong = pd.DataFrame([{
            "parking_id": 53, "name": "박달시장2노외", "access_rule": "SAME_AS_FEE_HOURS",
        }])
        with self.assertRaisesRegex(ValueError, "ID 53"):
            validate_rule_ids(wrong, lots)
        with self.assertRaises(ValueError):
            apply_schedules(wrong)
        correct = wrong.assign(name="안양역1노상")
        pd.testing.assert_frame_equal(validate_rule_ids(correct, lots), correct)
        with self.assertRaisesRegex(ValueError, "고유 정수"):
            validate_rule_ids(pd.concat([correct, correct]), lots)


class ObservationGridTests(unittest.TestCase):
    def test_grid_uses_latest_available_observation_without_interpolation(self):
        raw = pd.DataFrame({
            "ts_kst": pd.to_datetime(["2026-09-15 09:04:30", "2026-09-15 09:05:00",
                                      "2026-09-15 09:19:59", "2026-09-15 09:25:00"]),
            "cell_cnt": [100, 100, 100, 100],
            "park_count": [20, 30, 10, 150],
        })
        grid = build_series(raw)
        self.assertEqual(grid.index.min(), pd.Timestamp("2026-09-15 09:05"))
        self.assertEqual(grid.loc["2026-09-15 09:05", "occ"], 30)
        self.assertTrue(pd.isna(grid.loc["2026-09-15 09:10", "occ"]))
        self.assertTrue(pd.isna(grid.loc["2026-09-15 09:15", "occ"]))
        self.assertEqual(grid.loc["2026-09-15 09:20", "occ"], 10)
        self.assertTrue(pd.isna(grid.loc["2026-09-15 09:25", "occ"]))

    def test_a18_reports_tail_outage_null_count_and_wholly_absent_lot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path, rules_path = root / "parking.db", root / "rules.csv"
            with sqlite3.connect(db_path) as db:
                db.execute("CREATE TABLE obs(parking_id INTEGER,ts_kst TEXT,cell_cnt INTEGER,park_count INTEGER)")
                db.executemany("INSERT INTO obs VALUES(?,?,?,?)", [
                    (1, "2026-09-15T09:00:00+09:00", 100, 20),
                    (1, "2026-09-15T09:05:00+09:00", 100, None),
                    (2, "2026-09-15T09:00:00+09:00", 100, 10),
                    (2, "2026-09-15T09:05:00+09:00", 100, 11),
                    (2, "2026-09-15T09:10:00+09:00", 100, 12),
                    (2, "2026-09-15T09:15:00+09:00", 100, 13),
                ])
            pd.DataFrame({
                "parking_id": [1, 2, 3], "name": ["A", "B", "C"], "predict_ok": [1, 1, 1],
            }).to_csv(rules_path, index=False)
            with patch.object(a18, "ROOT", root), patch.object(a18, "DB", db_path), \
                    patch.object(a18, "RULES", rules_path), redirect_stdout(io.StringIO()):
                a18.main()
            result = pd.read_csv(root / "reports/tables/a18_continuity_check.csv").set_index("parking_id")
            self.assertEqual(set(result.index), {1, 2, 3})
            self.assertEqual(result.loc[1, "n_slots_5m"], 4)
            self.assertEqual(result.loc[1, "missing_ratio"], .75)
            self.assertEqual(result.loc[1, "missing_park_count"], 1)
            self.assertEqual(result.loc[1, "usable_ratio"], .5)
            self.assertEqual(result.loc[2, "missing_ratio"], 0)
            self.assertEqual(result.loc[3, "n_valid_slots"], 0)
            self.assertEqual(result.loc[3, "missing_ratio"], 1)
            self.assertTrue(pd.isna(result.loc[3, "usable_ratio"]))


class PredictionRangeTests(unittest.TestCase):
    """학습 범위 밖 요청을 가까운 모델로 위장하지 않는지.

    ★ 경계를 숫자로 박지 않고 `HORIZ_GRID` 에서 끌어온다. 격자를 넓힐 때마다
      이 테스트가 낡아 실제 버그를 가리지 않게 하기 위해서다
      (240·360 을 추가했을 때 실제로 121 을 범위 밖으로 알고 있었다)."""

    def _predictor(self, **kwargs):
        return bare_predictor(**kwargs)

    def test_out_of_range_requests_never_call_a_short_horizon_model(self):
        predictor = self._predictor()
        longest = max(HORIZ_GRID)
        # 모델/이력 속성이 아예 없어도 범위 밖 요청은 안전하게 종료해야 한다.
        for horizon in (0, -5, longest + 1, longest * 4, np.nan, np.inf):
            with self.subTest(horizon=horizon):
                result = predictor.predict(53, datetime(2026, 9, 15, 12), horizon)
                self.assertEqual(result["source"], "unsupported_horizon")
                self.assertIsNone(result["p50"])
                self.assertIsNone(result["full_prob"])
                self.assertIsNone(result["model_horizon_min"])

    def test_in_range_requests_snap_to_a_trained_horizon(self):
        predictor = self._predictor(accuracy_gate=BlockAllAccuracy())
        for horizon in (1, 45, max(HORIZ_GRID)):
            with self.subTest(horizon=horizon):
                result = predictor.predict(53, datetime(2026, 9, 15, 12), horizon)
                # 범위 안이면 격자를 고른 뒤 게이트 판정으로 넘어간다.
                self.assertIn(result["model_horizon_min"], HORIZ_GRID)
                self.assertEqual(result["source"], "lot_horizon_not_certified")

    def test_service_grid_matches_the_trained_horizons(self):
        # 두 상수가 어긋나면 없는 모델을 부르거나 학습해 두고 안 쓴다.
        from src.models.u11_evaluate import HORIZONS
        self.assertEqual(tuple(HORIZ_GRID), tuple(HORIZONS))



class PollRetryTests(unittest.TestCase):
    def test_failure_is_throttled_until_the_next_poll_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parking.db"
            old = (datetime.now(timezone(timedelta(hours=9)))-timedelta(hours=1)).isoformat()
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE obs(ts_kst TEXT)")
                db.execute("INSERT INTO obs VALUES(?)", (old,))
            poller = RequestPoller(path, min_interval_seconds=300)
            with patch("src.serve.request_polling.poll_once", side_effect=TimeoutError) as called, \
                    patch("src.serve.request_polling.time.monotonic", return_value=1000.0) as clock:
                first = poller.poll_if_due()
                second = poller.poll_if_due()
                self.assertEqual(first["status"], "failed")
                self.assertEqual(second["reason"], "retry_backoff")
                self.assertFalse(second["attempted"])
                self.assertEqual(called.call_count, 1)
                clock.return_value = 1300.0
                third = poller.poll_if_due()
                self.assertEqual(third["status"], "failed")
                self.assertEqual(called.call_count, 2)


if __name__ == "__main__":
    unittest.main()
