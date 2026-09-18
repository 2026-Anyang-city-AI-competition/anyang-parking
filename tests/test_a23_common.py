"""A23 공통 기반: 확장 지평선과 기준선 4종의 누수·결측·동일 평가행을 검증한다."""
import unittest

import numpy as np
import pandas as pd

from src.analysis import a20_matched_model_comparison as a20
from src.analysis import a23_common as a23


def grid(start="2026-08-31", end="2026-09-10", pids=(1, 2)):
    times = pd.date_range(start, end, freq="5min", tz="Asia/Seoul")
    parts = []
    for pid in pids:
        occ = 40 + 20 * np.sin(np.arange(len(times)) * 2 * np.pi / 288 + pid)
        parts.append(pd.DataFrame({"parking_id": pid, "ts_kst": times, "occ": occ}))
    return pd.concat(parts, ignore_index=True)


def frame_at(base, ts, horizon, pids=(1, 2)):
    """base의 한 시각을 평가행으로 만든 최소 프레임."""
    stamp = pd.Timestamp(ts, tz="Asia/Seoul")
    rows = base.loc[base["ts_kst"].eq(stamp) & base["parking_id"].isin(pids)].copy()
    rows["occ_now"] = rows["occ"]
    rows["target_time"] = rows["ts_kst"] + pd.Timedelta(minutes=horizon)
    rows["horizon"] = horizon
    rows["actual_occ"] = 50.0
    rows["eligible"] = True
    rows["weekday_group"] = np.where(
        rows["target_time"].dt.weekday >= 5, "weekend", "weekday")
    return rows.reset_index(drop=True)


class LagBaselineTests(unittest.TestCase):
    def test_lags_read_exact_offsets(self):
        base = grid()
        lookup = a23.occ_lookup(base)
        frame = a23.add_lag_baselines(frame_at(base, "2026-09-09 10:00", 120), lookup)
        for name, offset in a23.LAG_OFFSETS.items():
            wanted = base.set_index(["parking_id", "ts_kst"])["occ"].reindex(
                pd.MultiIndex.from_arrays(
                    [frame["parking_id"], frame["target_time"] - offset]))
            np.testing.assert_allclose(
                frame[a23.pred_column(name)].to_numpy(), wanted.to_numpy())

    def test_persistence_is_occ_now(self):
        base = grid()
        frame = a23.add_lag_baselines(
            frame_at(base, "2026-09-09 10:00", 60), a23.occ_lookup(base))
        np.testing.assert_allclose(frame[a23.pred_column("persistence")].to_numpy(),
                                   frame["occ_now"].to_numpy())

    def test_missing_history_is_null_not_filled(self):
        """이력이 없는 구간은 근처 값으로 대체하지 않고 NaN으로 남긴다."""
        base = grid(start="2026-09-08")
        frame = a23.add_lag_baselines(
            frame_at(base, "2026-09-08 10:00", 60), a23.occ_lookup(base))
        self.assertTrue(frame[a23.pred_column("lag_7d")].isna().all())

    def test_label_valid_gap_propagates_to_lag(self):
        """label_valid 게이트로 NaN이 된 시각은 기준선에서도 쓰이지 않는다."""
        base = grid()
        gated = pd.Timestamp("2026-09-08 11:00", tz="Asia/Seoul")
        base.loc[base["ts_kst"].eq(gated) & base["parking_id"].eq(1), "occ"] = np.nan
        frame = a23.add_lag_baselines(
            frame_at(base, "2026-09-09 10:00", 60), a23.occ_lookup(base))
        row = frame.loc[frame["parking_id"].eq(1)]
        self.assertTrue(row[a23.pred_column("lag_24h")].isna().all())
        self.assertTrue(frame.loc[frame["parking_id"].eq(2),
                                  a23.pred_column("lag_24h")].notna().all())

    def test_duplicate_grid_rejected(self):
        base = pd.concat([grid(end="2026-08-31 01:00")] * 2, ignore_index=True)
        with self.assertRaises(ValueError):
            a23.occ_lookup(base)


class SeasonalNaiveTests(unittest.TestCase):
    def test_history_only_no_future_leak(self):
        base = grid()
        cutoff = pd.Timestamp("2026-09-08", tz="Asia/Seoul")
        table = a23.fit_seasonal_naive(base, cutoff)
        after = base.loc[base["ts_kst"] >= cutoff].copy()
        after["occ"] = 99.0
        leaked = a23.fit_seasonal_naive(pd.concat([base.loc[base["ts_kst"] < cutoff], after]),
                                        cutoff)
        pd.testing.assert_frame_equal(table, leaked)

    def test_lookup_uses_target_time(self):
        base = grid()
        cutoff = pd.Timestamp("2026-09-08", tz="Asia/Seoul")
        table = a23.fit_seasonal_naive(base, cutoff)
        frame = a23.apply_seasonal_naive(table, frame_at(base, "2026-09-09 10:00", 120),
                                         min_samples=1)
        target = frame["target_time"].iloc[0]
        key = (frame["parking_id"].iloc[0], target.weekday(),
               target.hour * 60 + target.minute)
        expected = table.set_index(a23.SEASONAL_KEYS).loc[key, "seasonal_occ"]
        self.assertAlmostEqual(frame[a23.pred_column("seasonal_naive")].iloc[0],
                               float(expected))

    def test_thin_samples_are_null(self):
        base = grid(start="2026-09-07", end="2026-09-07 23:55")
        table = a23.fit_seasonal_naive(base, pd.Timestamp("2026-09-08", tz="Asia/Seoul"))
        frame = a23.apply_seasonal_naive(table, frame_at(base, "2026-09-07 10:00", 60),
                                         min_samples=2)
        self.assertTrue(frame[a23.pred_column("seasonal_naive")].isna().all())

    def test_empty_history_gives_null_column(self):
        base = grid()
        table = a23.fit_seasonal_naive(base, pd.Timestamp("2026-08-01", tz="Asia/Seoul"))
        frame = a23.apply_seasonal_naive(table, frame_at(base, "2026-09-09 10:00", 60))
        self.assertTrue(table.empty)
        self.assertTrue(frame[a23.pred_column("seasonal_naive")].isna().all())


class CommonRowTests(unittest.TestCase):
    def prepared(self):
        # seasonal naive가 같은 요일·시각을 2회 이상 보려면 몇 주가 필요하다.
        base = grid(start="2026-08-17")
        frame = a23.add_lag_baselines(
            frame_at(base, "2026-09-09 10:00", 60), a23.occ_lookup(base))
        table = a23.fit_seasonal_naive(base, pd.Timestamp("2026-09-09", tz="Asia/Seoul"))
        return a23.apply_seasonal_naive(table, frame)

    def test_common_requires_every_baseline(self):
        frame = self.prepared()
        self.assertTrue(a23.common_mask(frame).all())
        frame.loc[0, a23.pred_column("lag_7d")] = np.nan
        self.assertFalse(a23.common_mask(frame).iloc[0])

    def test_core_row_set_survives_missing_lag_7d(self):
        """보유 기간이 짧아 lag_7d가 없는 행도 core 집합에서는 평가할 수 있다."""
        frame = self.prepared()
        frame.loc[0, a23.pred_column("lag_7d")] = np.nan
        self.assertTrue(a23.common_mask(frame, a23.BASELINES_CORE).iloc[0])
        scores = a23.baseline_scores(frame, a23.BASELINES_CORE)
        self.assertEqual(set(scores["baseline"]), set(a23.BASELINES_CORE))

    def test_ineligible_row_never_common(self):
        frame = self.prepared()
        frame.loc[0, "eligible"] = False
        self.assertFalse(a23.common_mask(frame).iloc[0])

    def test_scores_share_one_row_count(self):
        frame = self.prepared()
        scores = a23.baseline_scores(frame.loc[a23.common_mask(frame)])
        self.assertEqual(set(scores["baseline"]), set(a23.BASELINES))
        self.assertEqual(scores["n"].nunique(), 1)

    def test_strongest_baseline_is_lowest_mae(self):
        scores = pd.DataFrame({"baseline": list(a23.BASELINES),
                               "mae": [5.0, 3.0, np.nan, 4.0]})
        self.assertEqual(a23.strongest_baseline(scores), ("lag_24h", 3.0))

    def test_strongest_baseline_without_samples(self):
        scores = pd.DataFrame({"baseline": list(a23.BASELINES), "mae": [np.nan] * 4})
        self.assertEqual(a23.strongest_baseline(scores), (None, np.nan))

    def test_empty_rows_give_null_mae(self):
        frame = self.prepared().iloc[:0]
        scores = a23.baseline_scores(frame)
        self.assertTrue(scores["mae"].isna().all())
        self.assertTrue((scores["n"] == 0).all())


class ExtendedHorizonTests(unittest.TestCase):
    def fixtures(self):
        times = pd.date_range("2026-08-31", "2026-09-10", freq="5min", tz="Asia/Seoul")
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
        return obs, lots, rules

    def test_1440_frame_targets_exactly_one_day_ahead(self):
        obs, lots, rules = self.fixtures()
        base = a20.build_features(obs, lots)
        frame = a20.build_horizon_frame(base, 1440, rules, lots)
        offset = (frame["target_time"] - frame["ts_kst"]).unique()
        self.assertEqual(list(offset), [pd.Timedelta(minutes=1440)])
        self.assertTrue(frame["eligible"].any())

    def test_all_a23_horizons_build(self):
        obs, lots, rules = self.fixtures()
        base = a20.build_features(obs, lots)
        for horizon in a23.HORIZONS:
            frame = a20.build_horizon_frame(base, horizon, rules, lots)
            self.assertEqual(int(frame["horizon"].iloc[0]), horizon)

    def test_off_grid_horizon_rejected(self):
        obs, lots, rules = self.fixtures()
        base = a20.build_features(obs, lots)
        for bad in (0, -60, 47):
            with self.assertRaises(ValueError):
                a20.build_horizon_frame(base, bad, rules, lots)

    def test_parse_horizons(self):
        self.assertEqual(a23.parse_horizons(""), a23.HORIZONS)
        self.assertEqual(a23.parse_horizons("15,1440"), (15, 1440))
        with self.assertRaises(ValueError):
            a23.parse_horizons("7")

    def test_prepare_horizon_adds_every_baseline(self):
        obs, lots, rules = self.fixtures()
        base = a20.build_features(obs, lots)
        lookup = a23.occ_lookup(base)
        frame = a23.prepare_horizon(base, 360, rules, lots, lookup,
                                    pd.Timestamp("2026-09-09", tz="Asia/Seoul"))
        for name in a23.BASELINES:
            self.assertIn(a23.pred_column(name), frame)
        row = a23.coverage_row(frame, 360)
        self.assertEqual(row["horizon"], 360)
        self.assertLessEqual(row["common_all_n"], row["common_core_n"])
        self.assertLessEqual(row["common_core_n"], row["eligible_n"])


if __name__ == "__main__":
    unittest.main()
