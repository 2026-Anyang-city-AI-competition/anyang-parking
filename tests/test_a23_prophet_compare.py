"""A23 M2 비교군: Prophet 학습 입력의 시간 경계와 세 모델 동일 행 채점을 검증한다."""
import unittest

import numpy as np
import pandas as pd

from src.analysis import a23_common as a23
from src.analysis import a23_prophet_compare as prophet_compare
from src.analysis.a23_model_upgrade import M0, M1

M2 = prophet_compare.M2


def base_grid(start="2026-08-31", end="2026-09-14 23:55", pids=(1, 2)):
    times = pd.date_range(start, end, freq="5min", tz="Asia/Seoul")
    return pd.concat([
        pd.DataFrame({"parking_id": pid, "ts_kst": times,
                      "occ": 50 + 20 * np.sin(np.arange(len(times)) * 2 * np.pi / 288 + pid)})
        for pid in pids], ignore_index=True)


def paired_rows(horizon=360, dates=("2026-09-12",), pids=(1,), per_day=6):
    rows = []
    for test_date in dates:
        start = pd.Timestamp(test_date, tz="Asia/Seoul")
        for pid in pids:
            for i in range(per_day):
                target = start + pd.Timedelta(hours=8 + i)
                rows.append(dict(
                    parking_id=pid, ts_kst=target - pd.Timedelta(minutes=horizon),
                    target_time=target, horizon=horizon, test_date=test_date,
                    eligible=True, weekday_group="weekend", actual_occ=55.0,
                    occ_now=50.0,
                    **{a23.pred_column(M0): 52.0, a23.pred_column(M1): 54.0},
                    **{a23.pred_column(b): 50.0 for b in a23.BASELINES}))
    return pd.DataFrame(rows)


class HistoryBoundaryTests(unittest.TestCase):
    def test_history_stops_before_fold_start(self):
        base = base_grid()
        start = pd.Timestamp("2026-09-12", tz="Asia/Seoul")
        history = prophet_compare.history_for(base, 1, start)
        self.assertLess(history["ds"].max(), start.tz_localize(None))
        self.assertGreater(len(history), 0)

    def test_history_is_one_lot_only(self):
        base = base_grid()
        start = pd.Timestamp("2026-09-12", tz="Asia/Seoul")
        first = prophet_compare.history_for(base, 1, start)
        second = prophet_compare.history_for(base, 2, start)
        self.assertEqual(len(first), len(second))
        self.assertFalse(np.allclose(first["y"].to_numpy(), second["y"].to_numpy()))

    def test_gated_observations_are_dropped(self):
        """label_valid로 NaN이 된 관측은 Prophet 입력에 들어가지 않는다."""
        base = base_grid()
        gated = base["ts_kst"].dt.hour.lt(6)
        base.loc[gated, "occ"] = np.nan
        history = prophet_compare.history_for(base, 1, pd.Timestamp("2026-09-12",
                                                                    tz="Asia/Seoul"))
        self.assertTrue(history["y"].notna().all())
        self.assertFalse((history["ds"].dt.hour < 6).any())

    def test_thin_history_returns_null_forecast(self):
        targets = pd.DatetimeIndex(
            pd.date_range("2026-09-12 08:00", periods=3, freq="h", tz="Asia/Seoul"))
        thin = pd.DataFrame({"ds": pd.date_range("2026-09-11", periods=10, freq="5min"),
                             "y": np.arange(10.0)})
        forecast = prophet_compare.forecast_lot(thin, targets)
        self.assertTrue(forecast.isna().all())
        self.assertEqual(list(forecast.index), list(targets))


class ThreeWayTests(unittest.TestCase):
    def test_rows_without_m2_are_excluded_and_counted(self):
        frame = paired_rows()
        frame[a23.pred_column(M2)] = [60.0, 60.0, np.nan, np.nan, 60.0, 60.0]
        table = prophet_compare.three_way(frame, (360,))
        row = table[table["row_set"].eq("core")].iloc[0]
        self.assertEqual(row["n"], 4)
        self.assertEqual(row["n_dropped_without_m2"], 2)

    def test_best_model_is_the_lowest_mae(self):
        frame = paired_rows()
        frame[a23.pred_column(M2)] = 55.0
        table = prophet_compare.three_way(frame, (360,))
        row = table[table["row_set"].eq("core")].iloc[0]
        self.assertAlmostEqual(row[f"{M2}_mae"], 0.0)
        self.assertAlmostEqual(row[f"{M1}_mae"], 1.0)
        self.assertAlmostEqual(row[f"{M0}_mae"], 3.0)
        self.assertEqual(row["best_model"], M2)

    def test_all_models_scored_on_identical_rows(self):
        frame = paired_rows()
        frame[a23.pred_column(M2)] = [60.0, np.nan, 60.0, 60.0, 60.0, 60.0]
        table = prophet_compare.three_way(frame, (360,))
        row = table[table["row_set"].eq("core")].iloc[0]
        self.assertEqual(row["n"], 5)
        self.assertTrue(all(row[f"{name}_mae"] == row[f"{name}_mae"] for name in (M0, M1, M2)))

    def test_empty_horizon_reports_null(self):
        table = prophet_compare.three_way(paired_rows().assign(
            **{a23.pred_column(M2): np.nan}), (360,))
        row = table[table["row_set"].eq("core")].iloc[0]
        self.assertEqual(row["n"], 0)
        self.assertIsNone(row["best_model"])


class AddProphetTests(unittest.TestCase):
    def test_only_requested_horizons_get_predictions(self):
        frame = pd.concat([paired_rows(horizon=360), paired_rows(horizon=120)],
                          ignore_index=True)
        scored, audits = prophet_compare.add_prophet(frame, base_grid(), (360,),
                                                     progress=False)
        self.assertTrue(scored.loc[scored["horizon"].eq(120),
                                   a23.pred_column(M2)].isna().all())
        self.assertTrue(scored.loc[scored["horizon"].eq(360),
                                   a23.pred_column(M2)].notna().all())
        self.assertEqual(len(audits), 1)
        self.assertTrue(bool(audits["fitted"].iloc[0]))

    def test_predictions_are_clipped(self):
        frame = paired_rows()
        scored, _ = prophet_compare.add_prophet(frame, base_grid(), (360,), progress=False)
        values = scored[a23.pred_column(M2)].dropna()
        self.assertTrue(values.between(0, 100).all())

    def test_one_fit_per_lot_and_fold(self):
        frame = paired_rows(dates=("2026-09-12", "2026-09-13"), pids=(1, 2))
        _, audits = prophet_compare.add_prophet(frame, base_grid(), (360,), progress=False)
        self.assertEqual(len(audits), 4)
        self.assertFalse(audits.duplicated(["test_date", "parking_id"]).any())


if __name__ == "__main__":
    unittest.main()
