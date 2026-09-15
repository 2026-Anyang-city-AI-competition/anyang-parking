"""A20의 미래 누수·평가 행 일치·시간 분할과 실제 LightGBM 실행을 검증한다."""
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.analysis import a20_matched_model_comparison as a20


def fixtures(start="2026-08-31", end="2026-09-07 00:05"):
    times = pd.date_range(start, end, freq="5min", tz="Asia/Seoul")
    rows = []
    for pid in (1, 2, 3):
        counts = (45 + 25*np.sin(np.arange(len(times))*2*np.pi/288 + pid)).astype(int)
        rows.append(pd.DataFrame({"parking_id": pid, "ts_kst": times,
                                  "cell_cnt": 100, "park_count": counts}))
    obs = pd.concat(rows, ignore_index=True)
    lots = pd.DataFrame({"parking_id": [1, 2, 3, 4], "name": ["A", "B", "C", "D"],
                         "wdays_start": ["09:00"]*4, "wdays_end": ["18:00"]*4,
                         "wend_start": ["00:00"]*4, "wend_end": ["00:00"]*4})
    rules = pd.DataFrame({"parking_id": [1, 2, 3, 4], "name": ["A", "B", "C", "D"],
                          "predict_ok": [1]*4,
                          "access_rule": ["FREE_AFTER_FEE", "SAME_AS_FEE_HOURS", "UNKNOWN", "FREE_AFTER_FEE"],
                          "weekday_access_start": [None, "09:00", None, None],
                          "weekday_access_end": [None, "18:00", None, None],
                          "weekday_access_status": [None, "open", None, None],
                          "saturday_access_status": [None, "closed", None, None],
                          "sunday_access_status": [None, "closed", None, None]})
    return obs, lots, rules


class A20ProtocolTests(unittest.TestCase):
    def test_current_snapshot_uses_three_complete_test_days(self):
        first = pd.Timestamp("2026-08-31 04:56:37+09:00")
        last = pd.Timestamp("2026-09-15 01:22:18+09:00")
        dates = a20.test_dates(first, last)
        self.assertEqual([str(d.date()) for d in dates], ["2026-09-12", "2026-09-13", "2026-09-14"])
        with self.assertRaisesRegex(ValueError, "학습 기간"):
            a20.test_dates(last-pd.Timedelta(days=5), last)
        with self.assertRaises(ValueError):
            a20.test_dates(first, last, test_days=0)

    def test_future_observations_do_not_change_prediction_features(self):
        obs, lots, rules = fixtures(end="2026-09-01")
        cut = pd.Timestamp("2026-08-31 08:00", tz="Asia/Seoul")
        changed = obs.copy()
        changed.loc[changed["ts_kst"] > cut, "park_count"] = 99
        before = a20.build_horizon_frame(a20.build_features(obs, lots), 120, rules, lots)
        after = a20.build_horizon_frame(a20.build_features(changed, lots), 120, rules, lots)
        mask = before["ts_kst"] <= cut
        pd.testing.assert_frame_equal(before.loc[mask, a20.FEATURES], after.loc[mask, a20.FEATURES])
        self.assertFalse(before.loc[mask, "actual_occ"].equals(after.loc[mask, "actual_occ"]))

    def test_gaps_exclude_both_methods_and_do_not_cross_lots(self):
        obs, lots, rules = fixtures(end="2026-09-01")
        origin = pd.Timestamp("2026-08-31 09:00", tz="Asia/Seoul")
        # t-20 is absent, although the explicitly used lag_5/10/15/30/60 are present.
        missing = (obs["parking_id"].eq(1) & obs["ts_kst"].eq(origin-pd.Timedelta(minutes=20)))
        # Both endpoints exist for ID 2, but a future middle observation exceeds capacity.
        obs.loc[obs["parking_id"].eq(2) & obs["ts_kst"].eq(origin+pd.Timedelta(minutes=10)), "park_count"] = 150
        frame = a20.build_horizon_frame(a20.build_features(obs.loc[~missing], lots), 30, rules, lots)
        rows = frame.loc[frame["ts_kst"].eq(origin)].set_index("parking_id")
        self.assertFalse(rows.loc[1, "history_complete"])
        self.assertTrue(rows.loc[1, "future_complete"])
        self.assertFalse(rows.loc[1, "eligible"])
        self.assertFalse(rows.loc[2, "future_complete"])
        self.assertTrue(pd.notna(rows.loc[2, "actual_occ"]))
        self.assertFalse(rows.loc[2, "eligible"])
        self.assertTrue(rows.loc[3, "eligible"])
        self.assertTrue((frame["target_time"]-frame["ts_kst"]).eq(pd.Timedelta(minutes=30)).all())

    def test_target_time_purges_every_split_boundary(self):
        obs, lots, rules = fixtures()
        frame = a20.build_horizon_frame(a20.build_features(obs, lots), 120, rules, lots)
        start = pd.Timestamp("2026-09-05", tz="Asia/Seoul")
        train, validation, test = a20.split_frame(frame, start)
        self.assertLess(train["target_time"].max(), start-pd.Timedelta(days=1))
        self.assertLess(validation["target_time"].max(), start)
        self.assertGreaterEqual(validation["ts_kst"].min(), start-pd.Timedelta(days=1))
        self.assertGreaterEqual(test["ts_kst"].min(), start)
        self.assertLess(test["target_time"].max(), start+pd.Timedelta(days=1))
        for boundary in (start-pd.Timedelta(days=1), start, start+pd.Timedelta(days=1)):
            crossing = boundary-pd.Timedelta(minutes=5)
            self.assertFalse(pd.concat([train, validation, test])["ts_kst"].eq(crossing).any())
        # Fee-operation metadata must not close a FREE_AFTER_FEE lot at the weekend.
        free = test.loc[test["parking_id"].eq(1)]
        self.assertTrue(free["access_group"].eq("accessible").all())
        self.assertTrue(free["opr_is_operating"].eq(0).all())
        self.assertTrue(test.loc[test["parking_id"].eq(2), "access_group"].eq("closed").all())
        self.assertTrue(test.loc[test["parking_id"].eq(3), "access_group"].eq("unknown").all())

    def test_same_rows_empty_groups_and_zero_baseline(self):
        pred = pd.DataFrame({"parking_id": [1, 1, 2], "horizon": [15]*3,
            "target_time": pd.to_datetime(["2026-09-04 12:00+09:00", "2026-09-04 12:05+09:00", "2026-09-05 12:00+09:00"]),
            "actual_occ": [0, 40, 0], "persistence_pred": [0, 20, 0], "ml_pred": [0, 30, 10],
            "access_group": ["accessible", "accessible", "closed"], "weekday_group": ["weekday", "weekday", "weekend"]})
        report = a20.summarize(pred, [15]).set_index(["access_group", "weekday_group"])
        service = report.loc[("accessible", "all")]
        self.assertEqual(service["n"], 2)
        self.assertEqual(service["persistence_mae"], 10)
        self.assertEqual(service["ml_mae"], 5)
        self.assertEqual(service["gain_pct"], 50)
        self.assertEqual(report.loc[("closed", "all"), "status"], "worse")
        self.assertTrue(pd.isna(report.loc[("closed", "all"), "gain_pct"]))
        self.assertEqual(report.loc[("unknown", "all"), "status"], "unavailable")
        self.assertEqual(report.loc[("unknown", "all"), "n"], 0)
        self.assertEqual(sum(report.loc[(a, "all"), "n"] for a in a20.ACCESS_GROUPS), report.loc[("all", "all"), "n"])

    def test_invalid_model_output_fails_instead_of_dropping_rows(self):
        obs, lots, rules = fixtures(end="2026-09-01")
        frame = a20.build_horizon_frame(a20.build_features(obs, lots), 15, rules, lots)
        frame = frame.loc[frame["eligible"]].head(5)
        for invalid in (np.nan, np.inf, -np.inf):
            with self.subTest(invalid=invalid):
                class InvalidModel:
                    def predict(self, features):
                        return np.full(len(features), invalid)
                with self.assertRaisesRegex(ValueError, "같은 평가 행"):
                    a20.predict(InvalidModel(), frame, 1)

    def test_run_with_real_lightgbm_keeps_inputs_and_writes_matched_reports(self):
        obs, lots, rules = fixtures()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path, rules_path = root/"parking.db", root/"rules.csv"
            with sqlite3.connect(db_path) as db:
                obs.assign(ts_kst=obs["ts_kst"].astype(str)).to_sql("obs", db, index=False)
                lots.to_sql("lots", db, index=False)
            rules.to_csv(rules_path, index=False)
            before_db = hashlib.sha256(db_path.read_bytes()).hexdigest()
            before_rules = rules_path.read_bytes()
            tables, predictions = root/"tables", root/"processed/a20"
            tables.mkdir()
            (tables/"a19_persistence_overall.csv").write_text("old baseline\n")
            predictions.parent.mkdir()
            (predictions.parent/"predictor.pkl").write_bytes(b"existing service model")
            with redirect_stdout(io.StringIO()), patch.object(a20, "LGBMRegressor", wraps=a20.LGBMRegressor) as constructor:
                summary = a20.run(db_path, rules_path, tables, predictions, test_days=3, min_train_days=1,
                    model_params={"n_estimators": 8, "n_jobs": 1, "min_child_samples": 5, "num_leaves": 7})
            self.assertEqual(constructor.call_count, 12)
            self.assertEqual(hashlib.sha256(db_path.read_bytes()).hexdigest(), before_db)
            self.assertEqual(rules_path.read_bytes(), before_rules)
            self.assertEqual((predictions.parent/"predictor.pkl").read_bytes(), b"existing service model")
            self.assertEqual((tables/"a19_persistence_overall.csv").read_text(), "old baseline\n")
            pairs = pd.read_csv(predictions/"predictions.csv.gz")
            self.assertFalse(pairs.duplicated(a20.KEYS).any())
            self.assertTrue(pairs["ml_pred"].between(0, 100).all())
            for h in a20.HORIZONS:
                service = pairs.loc[pairs["horizon"].eq(h) & pairs["access_group"].eq("accessible")]
                row = summary.loc[summary["horizon"].eq(h) & summary["access_group"].eq("accessible") & summary["weekday_group"].eq("all")].iloc[0]
                self.assertEqual(row["n"], len(service))
                self.assertAlmostEqual(row["persistence_mae"], (service["actual_occ"]-service["persistence_pred"]).abs().mean())
                self.assertAlmostEqual(row["ml_mae"], (service["actual_occ"]-service["ml_pred"]).abs().mean())
            manifest = json.loads((tables/"a20_manifest.json").read_text())
            self.assertEqual(manifest["matched_n"], len(pairs))
            self.assertEqual(manifest["absent_ids"], [4])
            self.assertEqual(manifest["test_dates"], ["2026-09-04", "2026-09-05", "2026-09-06"])
            splits = pd.read_csv(tables/"a20_splits.csv")
            self.assertTrue((splits["a19_eligible_n"]-splits["test_n"]).eq(splits["excluded_history_or_features_n"]).all())
            self.assertTrue((pd.to_datetime(splits["train_target_max"]) < pd.to_datetime(splits["validation_start"])).all())
            self.assertTrue((pd.to_datetime(splits["validation_target_max"]) < pd.to_datetime(splits["test_start"])).all())
            self.assertTrue((pd.to_datetime(splits["test_target_max"]) < pd.to_datetime(splits["test_end_exclusive"])).all())


if __name__ == "__main__":
    unittest.main()
