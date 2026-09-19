import unittest

import pandas as pd

from scripts.reevaluate_gate import gate_opens_at, weekend_detail


def days(*iso):
    return [pd.Timestamp(d) for d in iso]


class WeekendDetailTests(unittest.TestCase):
    def test_full_weekend_counts_as_a_pair(self):
        # 2026-09-12(토)·09-13(일)
        weekend, pairs = weekend_detail(days("2026-09-12", "2026-09-13"))
        self.assertEqual(len(weekend), 2)
        self.assertEqual(pairs, 1)

    def test_saturday_and_a_different_weeks_sunday_is_not_a_pair(self):
        # ISO 주로는 2주지만 붙어 있는 주말은 없다. 이걸 2회로 읽으면 안 된다.
        weekend, pairs = weekend_detail(days("2026-09-13", "2026-09-19"))
        self.assertEqual(len(weekend), 2)
        self.assertEqual(pairs, 0)

    def test_two_full_weekends(self):
        _, pairs = weekend_detail(days("2026-09-12", "2026-09-13",
                                       "2026-09-19", "2026-09-20"))
        self.assertEqual(pairs, 2)

    def test_weekdays_are_ignored(self):
        weekend, pairs = weekend_detail(days("2026-09-14", "2026-09-15"))
        self.assertEqual(weekend, [])
        self.assertEqual(pairs, 0)


class GateProjectionTests(unittest.TestCase):
    FIRST = pd.Timestamp("2026-08-31 04:56:37+09:00")

    def test_projects_the_day_the_gate_opens(self):
        last = pd.Timestamp("2026-09-19 23:00:00+09:00")
        opens = gate_opens_at(self.FIRST, last, test_days=7, min_train_days=7)
        self.assertIsNotNone(opens)
        self.assertEqual(opens.date().isoformat(), "2026-09-20")

    def test_returns_none_when_it_never_opens_in_range(self):
        last = pd.Timestamp("2026-09-19 23:00:00+09:00")
        # 창을 하루로 두면 한 날짜만 들어가 ISO 주 2회를 채울 수 없다.
        self.assertIsNone(
            gate_opens_at(self.FIRST, last, test_days=1, min_train_days=7, horizon_days=30))

    def test_projection_is_not_before_current_data_end(self):
        last = pd.Timestamp("2026-09-19 23:00:00+09:00")
        opens = gate_opens_at(self.FIRST, last, test_days=7, min_train_days=7)
        self.assertGreater(opens, last.normalize())


if __name__ == "__main__":
    unittest.main()
