"""A23 본실험: 인증 게이트·방향 재현·미래 누수와 실제 LightGBM 실행을 검증한다."""
import unittest
from contextlib import redirect_stdout
from io import StringIO

import numpy as np
import pandas as pd

from src.analysis import a23_common as a23
from src.analysis import a23_horizon_curve as curve


def scored_rows(horizon, row_set, ml_mae, weekday_mae=None, weekend_mae=None,
                baseline_mae=6.0, n=1000):
    """summarize()가 만드는 표와 같은 모양의 한 지평선 요약."""
    rows = []
    values = {"all": ml_mae, "weekday": weekday_mae if weekday_mae is not None else ml_mae,
              "weekend": weekend_mae if weekend_mae is not None else ml_mae}
    for day, mae in values.items():
        rows.append(dict(horizon=horizon, row_set=row_set, weekday_group=day,
                         method=curve.ML, n=n, n_lots=10, mae=mae, rmse=mae))
        for name in a23.ROW_SETS[row_set]:
            rows.append(dict(horizon=horizon, row_set=row_set, weekday_group=day,
                             method=name, n=n, n_lots=10,
                             mae=baseline_mae if name == "persistence" else baseline_mae + 2,
                             rmse=baseline_mae))
    return rows


def lot_rows(horizon, row_set, pass_count, fail_count, n=100):
    rows = []
    pid = 1
    for _ in range(pass_count):
        rows.append(dict(horizon=horizon, row_set=row_set, parking_id=pid, n=n,
                         ml_mae=3.0, evaluable=True, passes=True))
        pid += 1
    for _ in range(fail_count):
        rows.append(dict(horizon=horizon, row_set=row_set, parking_id=pid, n=n,
                         ml_mae=20.0, evaluable=True, passes=False))
        pid += 1
    return rows


def day_rows(horizon, row_set, better_flags, dates=("2026-09-11", "2026-09-12")):
    return [dict(horizon=horizon, row_set=row_set, test_date=d,
                 is_weekend=pd.Timestamp(d).weekday() >= 5, n=500, ml_mae=4.0,
                 strongest_baseline="persistence", strongest_mae=6.0, ml_better=flag)
            for d, flag in zip(dates, better_flags)]


def certification_for(summary_rows, lots, days, horizons=(60,), weekend_week_count=2):
    return curve.certify(pd.DataFrame(summary_rows), pd.DataFrame(lots), pd.DataFrame(days),
                         horizons, weekend_week_count)


class CertificationGateTests(unittest.TestCase):
    def passing_inputs(self, horizon=60, row_set="core"):
        return (scored_rows(horizon, row_set, 4.0), lot_rows(horizon, row_set, 9, 1),
                day_rows(horizon, row_set, [True, True]))

    def test_all_conditions_met(self):
        summary, lots, days = self.passing_inputs()
        row = certification_for(summary, lots, days).iloc[0]
        self.assertTrue(row["certified"])
        self.assertEqual(row["strongest_baseline"], "persistence")
        self.assertAlmostEqual(row["strongest_mae"], 6.0)

    def test_overall_mae_above_target_fails(self):
        summary = scored_rows(60, "core", 11.0)
        row = certification_for(summary, lot_rows(60, "core", 10, 0),
                                day_rows(60, "core", [True, True])).iloc[0]
        self.assertFalse(row["overall_mae_ok"])
        self.assertFalse(row["certified"])

    def test_weekend_mae_alone_can_fail(self):
        summary = scored_rows(60, "core", 9.0, weekday_mae=8.0, weekend_mae=12.0)
        row = certification_for(summary, lot_rows(60, "core", 10, 0),
                                day_rows(60, "core", [True, True])).iloc[0]
        self.assertTrue(row["weekday_mae_ok"])
        self.assertFalse(row["weekend_mae_ok"])
        self.assertFalse(row["certified"])

    def test_must_beat_strongest_not_weakest(self):
        """가장 약한 기준선만 이긴 경우는 통과가 아니다."""
        summary = scored_rows(60, "core", 7.0, baseline_mae=6.0)
        row = certification_for(summary, lot_rows(60, "core", 10, 0),
                                day_rows(60, "core", [True, True])).iloc[0]
        self.assertEqual(row["strongest_baseline"], "persistence")
        self.assertFalse(row["beats_strongest_baseline"])
        self.assertFalse(row["certified"])

    def test_lot_coverage_below_80_percent_fails(self):
        summary, _, days = self.passing_inputs()
        row = certification_for(summary, lot_rows(60, "core", 7, 3), days).iloc[0]
        self.assertAlmostEqual(row["lot_pass_rate"], 0.7)
        self.assertFalse(row["certified"])

    def test_thin_lots_not_counted_as_evaluable(self):
        summary, _, days = self.passing_inputs()
        thin = lot_rows(60, "core", 9, 1)
        thin.append(dict(horizon=60, row_set="core", parking_id=99, n=5, ml_mae=40.0,
                         evaluable=False, passes=False))
        row = certification_for(summary, thin, days).iloc[0]
        self.assertEqual(row["lots_evaluable"], 10)
        self.assertTrue(row["certified"])

    def test_direction_must_hold_on_every_test_date(self):
        summary, lots, _ = self.passing_inputs()
        row = certification_for(summary, lots, day_rows(60, "core", [True, False])).iloc[0]
        self.assertFalse(row["direction_reproduced"])
        self.assertFalse(row["certified"])

    def test_single_weekend_blocks_certification(self):
        summary, lots, days = self.passing_inputs()
        row = certification_for(summary, lots, days, weekend_week_count=1).iloc[0]
        self.assertFalse(row["weekend_weeks_ok"])
        self.assertFalse(row["certified"])
        # 주말 수는 데이터 길이 문제이므로 탐색 판정에서는 빼고 본다.
        self.assertTrue(row["provisional"])

    def test_provisional_still_needs_every_other_check(self):
        summary = scored_rows(60, "core", 11.0)
        row = certification_for(summary, lot_rows(60, "core", 10, 0),
                                day_rows(60, "core", [True, True]),
                                weekend_week_count=1).iloc[0]
        self.assertFalse(row["provisional"])

    def test_missing_baseline_rows_block_certification(self):
        """기준선 표본이 없으면 비교 자체가 불가능하므로 통과시키지 않는다."""
        summary = [r for r in scored_rows(60, "core", 4.0) if r["method"] == curve.ML]
        row = certification_for(summary, lot_rows(60, "core", 10, 0),
                                day_rows(60, "core", [True, True])).iloc[0]
        self.assertIsNone(row["strongest_baseline"])
        self.assertFalse(row["certified"])


class CeilingTests(unittest.TestCase):
    def certification(self, flags):
        horizons = sorted(flags)
        rows = [dict(row_set="core", horizon=h, certified=flags[h], provisional=True)
                for h in horizons]
        return pd.DataFrame(rows), horizons

    def test_provisional_column_has_its_own_ceiling(self):
        table, horizons = self.certification({15: False, 30: False})
        self.assertIsNone(curve.certified_max_horizon(table, "core", horizons))
        self.assertEqual(curve.certified_max_horizon(table, "core", horizons, "provisional"), 30)

    def test_stops_at_first_failure(self):
        table, horizons = self.certification({15: True, 30: True, 60: False, 120: True})
        self.assertEqual(curve.certified_max_horizon(table, "core", horizons), 30)

    def test_none_when_shortest_fails(self):
        table, horizons = self.certification({15: False, 30: True})
        self.assertIsNone(curve.certified_max_horizon(table, "core", horizons))

    def test_all_pass(self):
        table, horizons = self.certification({15: True, 30: True})
        self.assertEqual(curve.certified_max_horizon(table, "core", horizons), 30)


class WeekendCountTests(unittest.TestCase):
    def test_counts_distinct_weekends(self):
        dates = pd.to_datetime(["2026-09-05", "2026-09-06", "2026-09-12"]).tz_localize("Asia/Seoul")
        self.assertEqual(curve.weekend_weeks(dates), 2)

    def test_same_weekend_counts_once(self):
        dates = pd.to_datetime(["2026-09-12", "2026-09-13"]).tz_localize("Asia/Seoul")
        self.assertEqual(curve.weekend_weeks(dates), 1)

    def test_weekday_only(self):
        dates = pd.to_datetime(["2026-09-15", "2026-09-16"]).tz_localize("Asia/Seoul")
        self.assertEqual(curve.weekend_weeks(dates), 0)


class CollectTests(unittest.TestCase):
    def inputs(self):
        times = pd.date_range("2026-08-24", "2026-09-14 23:55", freq="5min", tz="Asia/Seoul")
        obs = pd.concat([
            pd.DataFrame({"parking_id": pid, "ts_kst": times, "cell_cnt": 100,
                          "park_count": (45 + 25 * np.sin(
                              np.arange(len(times)) * 2 * np.pi / 288 + pid)).astype(int)})
            for pid in (1, 2)], ignore_index=True)
        lots = pd.DataFrame({"parking_id": [1, 2], "name": ["A", "B"],
                             "wdays_start": ["09:00"] * 2, "wdays_end": ["18:00"] * 2,
                             "wend_start": ["00:00"] * 2, "wend_end": ["00:00"] * 2})
        rules = pd.DataFrame({"parking_id": [1, 2], "name": ["A", "B"], "predict_ok": [1, 1],
                              "access_rule": ["FREE_AFTER_FEE"] * 2})
        from src.analysis import a20_matched_model_comparison as a20
        return a20.build_features(obs, lots), lots, rules

    def collected(self, horizons=(60,), dates=("2026-09-12", "2026-09-13")):
        base, lots, rules = self.inputs()
        stamps = pd.to_datetime(list(dates)).tz_localize("Asia/Seoul")
        with redirect_stdout(StringIO()):
            return curve.collect(base, rules, lots, stamps, horizons, progress=False)

    def test_predictions_cover_every_method(self):
        paired, _ = self.collected()
        for name in curve.METHODS:
            self.assertIn(a23.pred_column(name), paired)
        self.assertTrue(paired[a23.pred_column(curve.ML)].between(0, 100).all())

    def test_test_rows_are_labelled_inside_their_fold_day(self):
        paired, _ = self.collected()
        for test_date, group in paired.groupby("test_date"):
            start = pd.Timestamp(test_date, tz="Asia/Seoul")
            self.assertGreaterEqual(group["target_time"].min(), start)
            self.assertLess(group["target_time"].max(), start + pd.Timedelta(days=1))

    def test_long_horizon_still_has_test_rows(self):
        """A20의 관측시각 기준 창에서는 1440분 test 행이 정의상 0이 된다."""
        paired, audits = self.collected(horizons=(1440,), dates=("2026-09-12",))
        self.assertGreater(len(paired), 0)
        self.assertGreater(int(audits["test_n"].iloc[0]), 0)

    def test_train_labels_stay_before_validation_day(self):
        base, lots, rules = self.inputs()
        from src.analysis.a20_matched_model_comparison import build_horizon_frame
        start = pd.Timestamp("2026-09-12", tz="Asia/Seoul")
        frame = build_horizon_frame(base, 720, rules, lots)
        train, validation, test = curve.split_by_label_time(frame, start)
        self.assertLess(train["target_time"].max(), start - pd.Timedelta(days=1))
        self.assertLess(validation["target_time"].max(), start)
        self.assertGreaterEqual(test["target_time"].min(), start)

    def test_no_duplicate_evaluation_rows(self):
        paired, _ = self.collected(horizons=(60, 120))
        self.assertFalse(paired.duplicated(curve.KEYS).any())

    def test_summary_shares_one_row_count_per_group(self):
        paired, _ = self.collected()
        summary = curve.summarize(paired, (60,))
        for (_, _, _), group in summary.groupby(["horizon", "row_set", "weekday_group"]):
            self.assertEqual(group["n"].nunique(), 1)

    def test_by_day_reports_each_test_date(self):
        paired, _ = self.collected()
        by_day = curve.by_day_scores(paired, (60,))
        self.assertEqual(set(by_day["test_date"]), {"2026-09-12", "2026-09-13"})
        self.assertTrue(by_day["is_weekend"].all())

    def test_audit_records_baseline_availability(self):
        _, audits = self.collected()
        for name in a23.BASELINES:
            self.assertIn(f"{name}_available_n", audits)
        self.assertTrue((audits["train_n"] > 0).all())


if __name__ == "__main__":
    unittest.main()
