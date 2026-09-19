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

    def test_multiple_paid_dates_are_capped_per_day_not_once(self):
        # 수 16:00~17:00(60분) + 목 09:00~17:00(480분). 목요일은 31,000원이라 25,000 상한.
        result = self.quote(datetime(2026, 9, 16, 16), 1500)
        self.assertEqual(result["paid_days"], 2)
        self.assertEqual(result["billable_min"], 540)
        self.assertEqual(result["total"], 1000 + 25000)
        self.assertTrue(result["capped"])
        # 한 줄로 이어 누진하면 마지막 구간 단가가 계속 붙어 더 비싸진다.
        self.assertLess(result["total"], result["raw_progressive"])

    def test_multi_day_does_not_assert_a_prepaid_total(self):
        result = self.quote(datetime(2026, 9, 16, 16), 1500)
        self.assertIsNone(result["total_prepaid"])
        self.assertFalse(result["recommend_prepaid"])
        self.assertEqual(result["daily_pass"], 7000)          # 하루치 정가는 안다
        self.assertEqual(result["daily_pass_days_required"], 2)
        self.assertIn("연장·재구매", result["prepaid_reason"])

    def test_breakdown_sums_to_total_and_shows_each_step(self):
        result = self.quote(datetime(2026, 9, 16, 16), 240, discount="경형자동차")
        self.assertEqual(sum(row["amt"] for row in result["breakdown"]), result["total"])
        kinds = [row["kind"] for row in result["breakdown"]]
        self.assertEqual(kinds[0], "free_window")             # 무료구간이 먼저
        self.assertIn("progressive", kinds)
        self.assertIn("benefit_rate", kinds)                  # 감면은 누진 뒤
        self.assertLess(kinds.index("progressive"), kinds.index("benefit_rate"))

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


class LegacyMultiDayTests(unittest.TestCase):
    """조사 규칙이 없을 때도 날짜별로 나눠 계산한다."""

    LOT = {"type": "노외", "grade": 1, "wdays_start": "09:00", "wdays_end": "22:00",
           "wend_start": "09:00", "wend_end": "22:00"}

    def test_thirty_hours_is_capped_each_day(self):
        # 월 10:00 + 1800분 → 월 10:00~22:00(720분) + 화 09:00~16:00(420분)
        result = calc_fare(self.LOT, datetime(2026, 9, 7, 10), 1800)
        self.assertEqual(result["paid_days"], 2)
        self.assertEqual(result["billable_min"], 720 + 420)
        self.assertTrue(result["capped"])
        # 하루 상한 25,000이 날짜마다 걸린다. 한 번만 걸면 25,000으로 과소청구된다.
        self.assertGreater(result["total"], 25000)
        self.assertLessEqual(result["total"], 2 * 25000)
        self.assertEqual(sum(row["amt"] for row in result["breakdown"]), result["total"])

    def test_single_day_is_unchanged(self):
        # 기존 값이 그대로여야 한다(CLAUDE.md 기준표).
        for minutes, expected in ((120, 5100), (180, 10500), (300, 24900)):
            self.assertEqual(calc_fare(self.LOT, datetime(2026, 9, 7, 10), minutes)["total"],
                             expected, minutes)

    def test_each_day_row_is_labelled_with_its_date(self):
        result = calc_fare(self.LOT, datetime(2026, 9, 7, 10), 1800)
        dates = {row["date"] for row in result["breakdown"] if row["kind"] == "progressive"}
        self.assertEqual(dates, {"2026-09-07", "2026-09-08"})
