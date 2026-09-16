import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from src.serve.access_check import check_access
from src.serve.access_rules import AccessRule, TimeWindow


def rule(entry=((0, 1440),), exit_=((0, 1440),), overnight=False):
    return AccessRule(39, "안양7동노외", "weekday",
                      tuple(TimeWindow(*w) for w in entry),
                      tuple(TimeWindow(*w) for w in exit_), (TimeWindow(540, 1020),),
                      "paid_window_free_outside", overnight, "confirmed_restricted", True,
                      date(2026, 1, 1), None, date(2026, 9, 16), "field", "photo", "")


class Repository:
    def __init__(self, default, overrides=None):
        self.default = default
        self.overrides = overrides or {}
        self.calls = []

    def lookup(self, pid, day, is_holiday=False):
        self.calls.append((day, is_holiday))
        return self.overrides.get(day, self.default)


class AccessCheckTests(unittest.TestCase):
    def check(self, repository, arrival, minutes, **kwargs):
        return check_access(repository, 39, arrival, minutes,
                            holiday_checker=lambda day: False, **kwargs)

    def test_fee_hours_do_not_restrict_access(self):
        result = self.check(Repository(rule()), datetime(2026, 9, 16, 16), 240)
        self.assertTrue(result.available)
        self.assertFalse(result.excluded)

    def test_shutter_closes_before_departure(self):
        result = self.check(Repository(rule(((600, 1080),), ((600, 1080),))),
                            datetime(2026, 9, 16, 16), 240)
        self.assertTrue(result.excluded)
        self.assertEqual(result.reason, "closes_before_departure")
        self.assertIn("18:00:00", result.access_closes_at)

    def test_arrival_outside_entry_hours(self):
        result = self.check(Repository(rule(((600, 1080),), ((600, 1080),))),
                            datetime(2026, 9, 16, 9), 60)
        self.assertEqual(result.reason, "entry_closed_at_arrival")

    def test_exact_closing_time_is_excluded(self):
        result = self.check(Repository(rule(((600, 1080),), ((600, 1080),))),
                            datetime(2026, 9, 16, 16), 120)
        self.assertTrue(result.excluded)

    def test_entry_closes_but_exit_is_24h(self):
        result = self.check(Repository(rule(((600, 1080),))), datetime(2026, 9, 16, 16), 240)
        self.assertTrue(result.available)

    def test_closure_gap_requires_permission_to_remain(self):
        windows = ((0, 720), (780, 1440))
        repository = Repository(rule(windows, windows))
        result = self.check(repository, datetime(2026, 9, 16, 11), 180)
        self.assertEqual(result.reason, "closed_interval_crossed")
        repository.default = replace(repository.default, overnight_allowed=True)
        self.assertTrue(self.check(repository, datetime(2026, 9, 16, 11), 180).available)

    def test_midnight_is_not_closing_for_24h_access(self):
        self.assertTrue(self.check(Repository(rule()), datetime(2026, 9, 18, 23), 60).available)
        self.assertTrue(self.check(Repository(rule()), datetime(2026, 9, 18, 23), 180).available)

    def test_next_day_schedule_is_checked(self):
        saturday = rule((), ())
        repository = Repository(rule(), {date(2026, 9, 19): saturday})
        result = self.check(repository, datetime(2026, 9, 18, 23), 180)
        self.assertTrue(result.excluded)

    def test_missing_rule_is_unknown_not_open_or_excluded(self):
        result = self.check(Repository(None), datetime(2026, 9, 16, 16), 240)
        self.assertIsNone(result.available)
        self.assertFalse(result.excluded)
        self.assertEqual(result.reason, "access_schedule_unknown")

    def test_general_public_not_allowed(self):
        repository = Repository(replace(rule(), general_public=False))
        self.assertEqual(self.check(repository, datetime(2026, 9, 16, 16), 60).reason,
                         "general_public_not_allowed")

    def test_optional_safety_margin(self):
        repository = Repository(rule(((600, 1080),), ((600, 1080),)))
        result = self.check(repository, datetime(2026, 9, 16, 16), 115, safety_margin_minutes=10)
        self.assertEqual(result.reason, "insufficient_exit_margin")
        self.assertEqual(result.to_dict()["safety_margin_minutes"], 10)

    def test_holiday_checker_is_used_for_each_date(self):
        repository = Repository(rule())
        check_access(repository, 39, datetime(2026, 9, 20, 23), 180,
                     holiday_checker=lambda day: day == date(2026, 9, 21))
        self.assertIn((date(2026, 9, 21), True), repository.calls)

    def test_timezone_conversion_and_invalid_duration(self):
        result = self.check(Repository(rule()), datetime(2026, 9, 16, 7, tzinfo=timezone.utc), 60)
        self.assertIn("16:00:00+09:00", result.arrival_at)
        for minutes in (0, -1, float("nan"), True):
            with self.assertRaises(ValueError):
                self.check(Repository(rule()), datetime(2026, 9, 16, 16), minutes)


if __name__ == "__main__":
    unittest.main()
