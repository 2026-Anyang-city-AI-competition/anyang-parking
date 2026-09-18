"""A23 M1 보강: 추가 피처의 미래 누수 금지, M0과의 행 일치, 비교표를 검증한다."""
import unittest
from contextlib import redirect_stdout
from datetime import date
from io import StringIO

import numpy as np
import pandas as pd

from src.analysis import a20_matched_model_comparison as a20
from src.analysis import a23_common as a23
from src.analysis import a23_model_upgrade as upgrade


def fixtures(start="2026-08-24", end="2026-09-14 23:55"):
    times = pd.date_range(start, end, freq="5min", tz="Asia/Seoul")
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
    return a20.build_features(obs, lots), lots, rules


def prepared(horizon=120):
    base, lots, rules = fixtures()
    frame = a23.add_lag_baselines(a20.build_horizon_frame(base, horizon, rules, lots),
                                  a23.occ_lookup(base))
    return upgrade.add_m1_features(frame), base


class FeatureTests(unittest.TestCase):
    def test_extra_lags_are_observable_at_prediction_time(self):
        """추가 lag의 원본 시각은 항상 관측시각 이하여야 한다(미래 누수 금지)."""
        for horizon in (15, 120, 720, 1440):
            frame, _ = prepared(horizon)
            for offset in a23.LAG_OFFSETS.values():
                source = frame["target_time"] - offset
                self.assertTrue((source <= frame["ts_kst"]).all(),
                                f"H={horizon} {offset}에서 미래값을 참조했습니다")

    def test_extra_lags_match_baseline_values(self):
        frame, _ = prepared()
        np.testing.assert_allclose(frame["tgt_lag_24h"].to_numpy(),
                                   frame[a23.pred_column("lag_24h")].to_numpy())
        np.testing.assert_allclose(frame["tgt_lag_7d"].to_numpy(),
                                   frame[a23.pred_column("lag_7d")].to_numpy())

    def test_lag_delta_is_relative_to_now(self):
        frame, _ = prepared()
        expected = frame["tgt_lag_24h"] - frame["occ_now"]
        np.testing.assert_allclose(frame["tgt_lag_24h_delta"].to_numpy(),
                                   expected.to_numpy())

    def test_time_of_week_is_built_on_target_time(self):
        frame, _ = prepared()
        target = frame["target_time"]
        minute = target.dt.weekday * 1440 + target.dt.hour * 60 + target.dt.minute
        np.testing.assert_allclose(
            frame["tow_sin"].to_numpy(),
            np.sin(2 * np.pi * minute.to_numpy() / upgrade.WEEK_MINUTES))
        np.testing.assert_array_equal(frame["tgt_day_of_week"].to_numpy(),
                                      target.dt.weekday.to_numpy())

    def test_time_of_week_separates_same_clock_time_on_different_days(self):
        frame, _ = prepared()
        pairs = frame.groupby("tgt_day_of_week")[["tow_sin", "tow_cos"]].first()
        self.assertGreater(len(pairs.drop_duplicates()), 1)

    def test_holiday_flag_reads_the_target_date(self):
        frame, _ = prepared()
        holiday = frame["target_time"].dt.date.iloc[0]
        marked = upgrade.add_m1_features(frame, holidays={holiday})
        expected = frame["target_time"].dt.date.eq(holiday).astype(int)
        np.testing.assert_array_equal(marked["tgt_is_holiday"].to_numpy(),
                                      expected.to_numpy())

    def test_constant_extras_flags_unused_features(self):
        frame, _ = prepared()
        # 데이터 기간에 공휴일이 없으면 tgt_is_holiday는 상수라 기여할 수 없다.
        self.assertIn("tgt_is_holiday", upgrade.constant_extras(frame))
        marked = upgrade.add_m1_features(frame, holidays={date(2026, 9, 24)})
        self.assertIn("tgt_is_holiday", upgrade.constant_extras(marked))

    def test_m1_features_extend_m0(self):
        self.assertEqual(upgrade.M1_FEATURES[:len(a20.FEATURES)], a20.FEATURES)
        self.assertEqual(len(upgrade.M1_FEATURES),
                         len(a20.FEATURES) + len(upgrade.M1_EXTRA))


class RowParityTests(unittest.TestCase):
    def collected(self, horizons=(120,), dates=("2026-09-12", "2026-09-13")):
        base, lots, rules = fixtures()
        stamps = pd.to_datetime(list(dates)).tz_localize("Asia/Seoul")
        with redirect_stdout(StringIO()):
            return upgrade.collect(base, rules, lots, stamps, horizons, progress=False)

    def test_eligible_gate_ignores_m1_extras(self):
        """M1 추가 피처가 비어도 평가행은 줄지 않는다."""
        base, lots, rules = fixtures()
        before = a20.build_horizon_frame(base, 120, rules, lots)["eligible"]
        after, _ = prepared(120)
        self.assertEqual(int(after["eligible"].sum()), int(before.sum()))
        missing_extra = after.loc[after["tgt_lag_7d"].isna() & after["eligible"]]
        self.assertGreater(len(missing_extra), 0)

    def test_both_models_score_the_same_rows(self):
        paired, _ = self.collected()
        for name in (upgrade.M0, upgrade.M1):
            self.assertTrue(paired[a23.pred_column(name)].notna().all())
        self.assertFalse(paired.duplicated(upgrade.KEYS).any())

    def test_predictions_stay_in_range(self):
        paired, _ = self.collected()
        for name in (upgrade.M0, upgrade.M1):
            self.assertTrue(paired[a23.pred_column(name)].between(0, 100).all())

    def test_audit_records_null_rates_and_constants(self):
        _, audits = self.collected()
        for column in upgrade.M1_EXTRA:
            self.assertIn(f"{column}_train_null_rate", audits)
        self.assertIn("constant_extras", audits)


class HeadToHeadTests(unittest.TestCase):
    def frame(self, m0_error, m1_error, dates=("2026-09-12", "2026-09-13")):
        rows = []
        for test_date in dates:
            for i in range(20):
                rows.append(dict(
                    parking_id=1, horizon=60, test_date=test_date, eligible=True,
                    weekday_group="weekend", actual_occ=50.0,
                    **{a23.pred_column(upgrade.M0): 50.0 + m0_error,
                       a23.pred_column(upgrade.M1): 50.0 + m1_error},
                    **{a23.pred_column(b): 50.0 for b in a23.BASELINES}))
        return pd.DataFrame(rows)

    def test_gain_is_positive_when_m1_is_better(self):
        table = upgrade.head_to_head(self.frame(4.0, 1.0), (60,))
        row = table[table["row_set"].eq("core")].iloc[0]
        self.assertAlmostEqual(row["m0_mae"], 4.0)
        self.assertAlmostEqual(row["m1_mae"], 1.0)
        self.assertAlmostEqual(row["gain_pp"], 3.0)
        self.assertAlmostEqual(row["gain_pct"], 75.0)
        self.assertTrue(row["m1_better_everywhere"])

    def test_gain_is_negative_when_m1_is_worse(self):
        table = upgrade.head_to_head(self.frame(1.0, 4.0), (60,))
        row = table[table["row_set"].eq("core")].iloc[0]
        self.assertLess(row["gain_pp"], 0)
        self.assertEqual(row["m1_better_days"], 0)
        self.assertFalse(row["m1_better_everywhere"])

    def test_empty_rows_report_null_not_zero(self):
        table = upgrade.head_to_head(self.frame(1.0, 1.0).iloc[:0], (60,))
        row = table[table["row_set"].eq("core")].iloc[0]
        self.assertEqual(row["n"], 0)
        self.assertTrue(np.isnan(row["m0_mae"]))
        self.assertFalse(row["m1_better_everywhere"])


if __name__ == "__main__":
    unittest.main()
