import unittest

import numpy as np
import pandas as pd

from src.analysis.a25_demotion_value import calibration, summarise


class CalibrationTests(unittest.TestCase):
    def frame(self, prob, actual, occ_now=None, horizon=60):
        return pd.DataFrame({"horizon": horizon, "full_prob": prob, "nx": actual,
                             "occ_now": occ_now if occ_now is not None else actual})

    def test_perfect_probabilities_score_zero_brier(self):
        rows, _ = calibration(self.frame([1.0, 0.0], [95.0, 10.0]))
        self.assertEqual(rows.iloc[0]["brier"], 0.0)

    def test_persistence_is_always_reported_alongside(self):
        rows, _ = calibration(self.frame([0.9, 0.1], [95.0, 10.0], occ_now=[10.0, 95.0]))
        self.assertIn("brier_persistence", rows.columns)
        # persistence 가 틀린 경우라도 열이 비지 않는다 — 항상 병기한다.
        self.assertGreater(rows.iloc[0]["brier_persistence"], rows.iloc[0]["brier"])

    def test_bins_cover_ten_buckets(self):
        prob = np.linspace(0, 0.99, 100)
        _, bins = calibration(self.frame(prob, np.where(prob > .5, 95.0, 10.0)))
        self.assertEqual(len(bins), 10)
        self.assertEqual(int(bins.n.sum()), 100)

    def test_slope_pass_uses_the_stated_band(self):
        prob = np.linspace(0.05, 0.95, 200)
        actual = np.where(np.random.default_rng(42).random(200) < prob, 95.0, 10.0)
        rows, _ = calibration(self.frame(prob, actual))
        slope = rows.iloc[0]["reliability_slope"]
        self.assertEqual(rows.iloc[0]["slope_pass"], bool(0.9 <= slope <= 1.1))


class SummaryTests(unittest.TestCase):
    def queries(self, **overrides):
        base = {"horizon": 60, "axis": "walk", "cutoff": 0.5, "changed": 0,
                "top1_changed": 0, "a_fail": 0, "b_fail": 0, "rescued": 0, "harmed": 0,
                "both_failed": 0, "c_rescued": 0, "extra_walk": 0, "extra_fare": 0,
                "c_extra_walk": 0, "n_candidates": 5, "dropped_candidates": 0}
        return pd.DataFrame([{**base, **overrides}])

    def test_change_rate_denominator_is_query_count(self):
        rows = pd.concat([self.queries(changed=1), self.queries(changed=0),
                          self.queries(changed=0), self.queries(changed=0)])
        self.assertEqual(summarise(rows).iloc[0]["change_rate"], 0.25)

    def test_gain_over_persistence_is_reported(self):
        rows = pd.concat([self.queries(rescued=1, c_rescued=1),
                          self.queries(rescued=1, c_rescued=0)])
        summary = summarise(rows).iloc[0]
        self.assertEqual(summary["rescued"], 2)
        self.assertEqual(summary["rescued_persistence"], 1)
        # 모델의 기여는 총 회피 수가 아니라 기준선과의 차이다.
        self.assertEqual(summary["gain_over_persistence"], 1)

    def test_both_failed_is_not_counted_as_rescue_or_harm(self):
        rows = self.queries(a_fail=1, b_fail=1, both_failed=1)
        summary = summarise(rows).iloc[0]
        self.assertEqual(summary["rescued"], 0)
        self.assertEqual(summary["harmed"], 0)
        self.assertEqual(summary["both_failed"], 1)

    def test_cost_is_measured_only_on_changed_queries(self):
        rows = pd.concat([self.queries(top1_changed=1, extra_walk=10),
                          self.queries(top1_changed=0, extra_walk=0)])
        # 안 바꾼 질의를 평균에 넣으면 대가가 절반으로 희석된다.
        self.assertEqual(summarise(rows).iloc[0]["extra_walk_median"], 10.0)


if __name__ == "__main__":
    unittest.main()
