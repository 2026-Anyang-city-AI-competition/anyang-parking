import unittest
from dataclasses import replace
from datetime import datetime

from src.serve.access_rules import TimeWindow
from src.serve.fare import calc_fare
from tests.test_access_check import rule

LOT = {"name": "안양7동노외", "type": "노외", "grade": 2,
       "wdays_start": "00:00", "wdays_end": "24:00",
       "wend_start": "00:00", "wend_end": "24:00"}


class Rules:
    def lookup(self, pid, day, is_holiday=False):
        value = rule()
        if day.weekday() >= 5 or is_holiday:
            return replace(value, fee_mode="free", fee_windows=())
        return value


class SurveyedFareTests(unittest.TestCase):
    def quote(self, start, minutes, rules=None, **kwargs):
        return calc_fare(LOT, start, minutes, access_rules=rules or Rules(), parking_id=39,
                         holiday_checker=lambda day: False, **kwargs)

    def test_id39_four_hours_only_one_hour_billable(self):
        result = self.quote(datetime(2026, 9, 16, 16), 240)
        self.assertEqual(result["billable_min"], 60)
        self.assertEqual(result["free_minutes_outside_fee_window"], 180)
        self.assertEqual(result["total"], 1000)
        self.assertEqual(result["total_prepaid"], 7000)
        self.assertEqual(result["fee_source"], "survey")

    def test_all_free_and_weekend_are_zero(self):
        for start in (datetime(2026, 9, 16, 18), datetime(2026, 9, 19, 10)):
            self.assertEqual(self.quote(start, 240)["total"], 0)

    def test_progressive_daily_pass_and_discount_preserved(self):
        result = self.quote(datetime(2026, 9, 16, 10), 300)
        self.assertEqual(result["total"], 16600)
        self.assertEqual(result["total_prepaid"], 7000)
        self.assertTrue(result["recommend_prepaid"])
        discounted = self.quote(datetime(2026, 9, 16, 16), 240, discount="경형자동차")
        self.assertEqual(discounted["total"], 500)

    def test_friday_to_free_saturday(self):
        result = self.quote(datetime(2026, 9, 18, 16), 1200)
        self.assertEqual(result["billable_min"], 60)
        self.assertEqual(result["total"], 1000)

    def test_multiple_paid_dates_not_silently_one_cap(self):
        result = self.quote(datetime(2026, 9, 16, 16), 1500)
        self.assertIsNone(result["total"])
        self.assertIn("복수 날짜", result["reason"])

    def test_missing_survey_uses_flagged_legacy_fallback(self):
        class Missing:
            def lookup(self, *args, **kwargs):
                return None
        result = self.quote(datetime(2026, 9, 16, 10), 60, rules=Missing())
        self.assertEqual(result["fee_source"], "legacy_db_unverified")
        self.assertEqual(result["total"], 1000)

    def test_holiday_free_rule(self):
        result = calc_fare(LOT, datetime(2026, 9, 16, 10), 240, access_rules=Rules(),
                           parking_id=39, holiday_checker=lambda day: True)
        self.assertEqual(result["total"], 0)


if __name__ == "__main__":
    unittest.main()
