import unittest

import numpy as np
import pandas as pd

from src.analysis.a26_gate_stratified import MIN_ROWS, annotate, headline, stratify


class FakeGate:
    """짝수 시각만 허용하는 게이트."""

    def validity_mask(self, pids, observed, targets):
        return [t.hour % 2 == 0 for t in targets]


PER_DAY = MIN_ROWS * 4      # 게이트가 절반을 자르므로 칸마다 여유 있게 둔다


def block(start, days, horizon, error, base_error):
    """하루 안에 촘촘히 찍어 요일 구분이 섞이지 않게 한다."""
    stamps = []
    for offset in range(days):
        day = pd.Timestamp(start) + pd.Timedelta(days=offset)
        stamps.append(pd.date_range(day, periods=PER_DAY, freq="1min"))
    ts = pd.DatetimeIndex(np.concatenate(stamps))
    actual = np.full(len(ts), 50.0)
    return pd.DataFrame({
        "parking_id": 1, "horizon": horizon,
        "ts_kst": ts, "target_time": ts,
        "nx": actual, "p50": actual - error, "occ_now": actual - base_error,
    })


def frame(start=None, horizon=60, error=1.0, base_error=2.0, n=None):
    """기본은 평일 2일 + 주말 2일. `start` 를 주면 그 날부터 2일만 만든다."""
    if start is not None:
        out = block(start, 2, horizon, error, base_error)
    else:
        out = pd.concat([block("2026-09-14", 2, horizon, error, base_error),   # 월·화
                         block("2026-09-19", 2, horizon, error, base_error)],  # 토·일
                        ignore_index=True)
    return out.head(n) if n else out


class StratifyTests(unittest.TestCase):
    def annotated(self, **kwargs):
        return annotate(frame(**kwargs), FakeGate())

    def test_four_cells_per_horizon(self):
        table = stratify(self.annotated())
        self.assertEqual(len(table), 4)
        self.assertEqual(set(table.day_group), {"평일", "주말"})
        self.assertEqual(set(table.gate), {"허용", "차단"})

    def test_thin_cells_are_marked_not_scored(self):
        table = stratify(self.annotated(n=40))
        self.assertTrue((table.status == "insufficient").all())
        self.assertTrue(table.ml_mae.isna().all())
        # 평가불가를 통과로 합산하지 않는다.
        self.assertFalse(table.mae_target_ok.any())
        self.assertFalse(table.beats_persistence.any())

    def test_persistence_is_scored_in_every_cell(self):
        table = stratify(self.annotated())
        scored = table[table.status == "ok"]
        self.assertTrue((scored.persistence_mae > 0).all())
        self.assertTrue((scored.ml_mae < scored.persistence_mae).all())

    def test_losing_to_persistence_is_reported_not_hidden(self):
        table = stratify(annotate(frame(error=3.0, base_error=1.0), FakeGate()))
        scored = table[table.status == "ok"]
        self.assertTrue((~scored.beats_persistence).all())
        self.assertTrue((scored.improvement_pct < 0).all())

    def test_weekend_rows_land_in_the_weekend_cell(self):
        # 2026-09-19 는 토요일
        table = stratify(annotate(frame(start="2026-09-19"), FakeGate()))
        weekend = table[(table.day_group == "주말") & (table.status == "ok")]
        self.assertTrue(len(weekend) > 0)
        weekday = table[(table.day_group == "평일") & (table.status == "ok")]
        self.assertEqual(len(weekday), 0)


class HeadlineTests(unittest.TestCase):
    def test_headline_uses_served_rows_only(self):
        table = stratify(annotate(frame(), FakeGate()))
        summary = headline(table)
        served_n = int(table[(table.gate == "허용") & (table.status == "ok")].n.sum())
        self.assertEqual(int(summary.iloc[0]["n"]), served_n)
        self.assertLess(served_n, int(table.n.sum()))

    def test_blocked_cells_do_not_move_the_headline(self):
        good = annotate(frame(), FakeGate())
        # 차단 구간만 크게 망가뜨려도 대표 수치는 그대로여야 한다.
        good.loc[~good.gate_allowed, "ml_ae"] = 99.0
        summary = headline(stratify(good))
        self.assertLess(float(summary.iloc[0]["ml_mae"]), 5.0)


if __name__ == "__main__":
    unittest.main()
