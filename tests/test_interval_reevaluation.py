import unittest

import numpy as np
import pandas as pd

from scripts.reevaluate_intervals import DEFAULT_BAND, coverage


def frame(n=2000, inside_ratio=0.80, state="operating|wd", horizon=60, width=10.0,
          served_ratio=1.0):
    """포함률을 정확히 심은 프레임. 구간 밖 행은 위로 벗어나게 둔다."""
    rng = np.random.default_rng(42)
    actual = np.full(n, 50.0)
    p10 = actual - width / 2
    p90 = actual + width / 2
    outside = rng.permutation(n)[: int(n * (1 - inside_ratio))]
    actual[outside] = p90[outside] + 1.0
    served = int(n * served_ratio)
    p10 = p10.astype(float)
    p90 = p90.astype(float)
    p10[served:] = np.nan
    p90[served:] = np.nan
    return pd.DataFrame({"horizon": horizon, "state": state, "nx": actual,
                         "p10": p10, "p90": p90})


class CoverageTests(unittest.TestCase):
    def test_coverage_inside_the_band_passes(self):
        row = coverage(frame(inside_ratio=0.80)).iloc[0]
        self.assertEqual(row["status"], "ok")
        self.assertTrue(row["pass"])
        self.assertAlmostEqual(row["coverage"], 0.80, places=2)

    def test_too_narrow_fails(self):
        # 포함률이 낮다 = 구간이 좁다 = 거짓 확신을 준다.
        row = coverage(frame(inside_ratio=0.60)).iloc[0]
        self.assertFalse(row["pass"])
        self.assertLess(row["coverage"], DEFAULT_BAND[0])

    def test_too_wide_fails(self):
        row = coverage(frame(inside_ratio=0.95)).iloc[0]
        self.assertFalse(row["pass"])
        self.assertGreater(row["coverage"], DEFAULT_BAND[1])

    def test_thin_cells_are_not_passed(self):
        row = coverage(frame(n=100, inside_ratio=0.80)).iloc[0]
        self.assertEqual(row["status"], "insufficient")
        self.assertFalse(row["pass"])
        self.assertIsNone(row["coverage"])

    def test_rows_without_intervals_are_excluded_from_coverage(self):
        row = coverage(frame(n=4000, inside_ratio=0.80, served_ratio=0.5)).iloc[0]
        self.assertEqual(row["rows_total"], 4000)
        self.assertEqual(row["rows_with_interval"], 2000)
        self.assertAlmostEqual(row["interval_share"], 0.5, places=3)

    def test_band_is_configurable(self):
        data = frame(inside_ratio=0.60)
        self.assertFalse(coverage(data).iloc[0]["pass"])
        self.assertTrue(coverage(data, band=(0.55, 0.65)).iloc[0]["pass"])

    def test_each_horizon_state_cell_is_separate(self):
        data = pd.concat([frame(state="operating|wd"), frame(state="outside|we")])
        table = coverage(data)
        self.assertEqual(len(table), 2)
        self.assertEqual(set(table.key), {"60|operating|wd", "60|outside|we"})


if __name__ == "__main__":
    unittest.main()
