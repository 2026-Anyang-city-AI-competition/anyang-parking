"""A24: 잔차 보정이 validation만 보는지, L0 결측 처리와 세 모델 동일 행 비교를 검증한다."""
import unittest
from contextlib import redirect_stdout
from io import StringIO

import numpy as np
import pandas as pd

from src.analysis import a20_matched_model_comparison as a20
from src.analysis import a23_common as a23
from src.analysis import a24_global_vs_local as a24


def fixtures(start="2026-08-24", end="2026-09-14 23:55", pids=(1, 2)):
    times = pd.date_range(start, end, freq="5min", tz="Asia/Seoul")
    obs = pd.concat([
        pd.DataFrame({"parking_id": pid, "ts_kst": times, "cell_cnt": 100,
                      "park_count": (45 + 25 * np.sin(
                          np.arange(len(times)) * 2 * np.pi / 288 + pid)).astype(int)})
        for pid in pids], ignore_index=True)
    lots = pd.DataFrame({"parking_id": list(pids), "name": [f"L{p}" for p in pids],
                         "wdays_start": ["09:00"] * len(pids), "wdays_end": ["18:00"] * len(pids),
                         "wend_start": ["00:00"] * len(pids), "wend_end": ["00:00"] * len(pids)})
    rules = pd.DataFrame({"parking_id": list(pids), "name": [f"L{p}" for p in pids],
                          "predict_ok": [1] * len(pids),
                          "access_rule": ["FREE_AFTER_FEE"] * len(pids)})
    return a20.build_features(obs, lots), lots, rules


def model_rows(g0=4.0, l0=2.0, h0=1.0, dates=("2026-09-12", "2026-09-13"), per_day=10):
    rows = []
    for test_date in dates:
        for _ in range(per_day):
            rows.append(dict(
                parking_id=1, horizon=60, test_date=test_date, eligible=True,
                weekday_group="weekend", actual_occ=50.0,
                **{a23.pred_column(a24.G0): 50.0 + g0,
                   a23.pred_column(a24.L0): 50.0 + l0,
                   a23.pred_column(a24.H0): 50.0 + h0},
                **{a23.pred_column(b): 50.0 for b in a23.BASELINES}))
    return pd.DataFrame(rows)


class LocalFeatureTests(unittest.TestCase):
    def test_lot_identity_features_are_dropped(self):
        for column in a24.LOT_IDENTITY:
            self.assertIn(column, a20.FEATURES)
            self.assertNotIn(column, a24.LOCAL_FEATURES)
        self.assertEqual(len(a24.LOCAL_FEATURES),
                         len(a20.FEATURES) - len(a24.LOT_IDENTITY))


class CorrectionTests(unittest.TestCase):
    def frame(self, n=40, parking_ids=(1, 2)):
        rows = []
        for pid in parking_ids:
            for i in range(n):
                rows.append(dict(parking_id=pid, occ_now=50.0, actual_occ=50.0 + pid))
        frame = pd.DataFrame(rows)
        for column in a20.FEATURES:
            if column not in frame:
                frame[column] = 0.0
        return frame

    class ZeroModel:
        def predict(self, features):
            return np.zeros(len(features))

    def test_correction_is_median_residual_per_lot(self):
        validation = self.frame()
        corrections = a24.residual_corrections(self.ZeroModel(), validation)
        # 예측이 occ_now(50)이고 정답이 50+pid이므로 잔차는 주차장 번호와 같다.
        self.assertAlmostEqual(corrections[1], 1.0)
        self.assertAlmostEqual(corrections[2], 2.0)

    def test_empty_validation_gives_no_correction(self):
        self.assertEqual(a24.residual_corrections(self.ZeroModel(), self.frame().iloc[:0]), {})

    def test_uncorrected_lots_keep_the_global_prediction(self):
        test = self.frame(n=5)
        base_pred = np.full(len(test), 60.0)
        corrected, shift = a24.apply_corrections(base_pred, test, {1: 5.0})
        first = test["parking_id"].eq(1).to_numpy()
        np.testing.assert_allclose(corrected[first], 65.0)
        np.testing.assert_allclose(corrected[~first], 60.0)
        np.testing.assert_allclose(shift[~first], 0.0)

    def test_correction_result_is_clipped(self):
        test = self.frame(n=3, parking_ids=(1,))
        corrected, _ = a24.apply_corrections(np.full(len(test), 98.0), test, {1: 50.0})
        self.assertTrue((corrected <= 100).all())
        corrected, _ = a24.apply_corrections(np.full(len(test), 2.0), test, {1: -50.0})
        self.assertTrue((corrected >= 0).all())


class CollectTests(unittest.TestCase):
    def collected(self, horizons=(120,), dates=("2026-09-12", "2026-09-13")):
        base, lots, rules = fixtures()
        stamps = pd.to_datetime(list(dates)).tz_localize("Asia/Seoul")
        with redirect_stdout(StringIO()):
            return a24.collect(base, rules, lots, stamps, horizons, progress=False)

    def test_every_model_predicts_on_the_same_rows(self):
        paired, _ = self.collected()
        for name in a24.MODELS:
            self.assertIn(a23.pred_column(name), paired)
            self.assertTrue(paired[a23.pred_column(name)].notna().all())
        self.assertFalse(paired.duplicated(a24.KEYS).any())

    def test_predictions_stay_in_range(self):
        paired, _ = self.collected()
        for name in a24.MODELS:
            self.assertTrue(paired[a23.pred_column(name)].between(0, 100).all())

    def test_audit_counts_local_models_and_corrections(self):
        _, audits = self.collected()
        for column in ("local_models_trained", "test_lots_without_local", "corrected_lots",
                       "correction_abs_median"):
            self.assertIn(column, audits)
        self.assertTrue((audits["validation_n"] > 0).all())

    def test_thin_lots_get_no_local_model(self):
        base, lots, rules = fixtures()
        frame = a20.build_horizon_frame(base, 120, rules, lots)
        train, _, test = a24.split_by_label_time(
            frame, pd.Timestamp("2026-09-12", tz="Asia/Seoul"))
        thin = train.loc[train["parking_id"].eq(2)].head(10)
        trimmed = pd.concat([train.loc[train["parking_id"].eq(1)], thin])
        predictions, trained = a24.predict_local(trimmed, test, a20.PARAMS)
        self.assertEqual(trained, [1])
        self.assertTrue(predictions[test["parking_id"].eq(2)].isna().all())
        self.assertTrue(predictions[test["parking_id"].eq(1)].notna().all())


class ThreeWayTests(unittest.TestCase):
    def test_rows_without_local_are_dropped_and_counted(self):
        frame = model_rows()
        frame.loc[:4, a23.pred_column(a24.L0)] = np.nan
        table = a24.three_way(frame, (60,))
        row = table[table["row_set"].eq("core")].iloc[0]
        self.assertEqual(row["n"], 15)
        self.assertEqual(row["n_dropped_without_local"], 5)

    def test_best_model_and_daily_direction(self):
        table = a24.three_way(model_rows(g0=4.0, l0=2.0, h0=1.0), (60,))
        row = table[table["row_set"].eq("core")].iloc[0]
        self.assertEqual(row["best_model"], a24.H0)
        self.assertEqual(row["test_days"], 2)
        self.assertTrue(row["h0_better_everywhere"])
        self.assertTrue(row["l0_better_everywhere"])

    def test_worse_models_report_no_better_days(self):
        table = a24.three_way(model_rows(g0=1.0, l0=5.0, h0=6.0), (60,))
        row = table[table["row_set"].eq("core")].iloc[0]
        self.assertEqual(row["best_model"], a24.G0)
        self.assertEqual(row["l0_better_days"], 0)
        self.assertFalse(row["h0_better_everywhere"])

    def test_empty_rows_report_null(self):
        table = a24.three_way(model_rows().iloc[:0], (60,))
        row = table[table["row_set"].eq("core")].iloc[0]
        self.assertEqual(row["n"], 0)
        self.assertIsNone(row["best_model"])
        self.assertTrue(np.isnan(row["g0_mae"]))


if __name__ == "__main__":
    unittest.main()
