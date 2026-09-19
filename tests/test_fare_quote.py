import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from src.serve.fare_quote import UnknownBenefit, UnknownParking, quote_fare
from tests.test_access_check import rule

KST = timezone(timedelta(hours=9))

LOT = {"parking_id": 39, "name": "안양7동노외", "div": "노외", "grade": 2, "cell_cnt": 50,
       "lat": 37.39, "lng": 126.95, "wdays_start": "00:00", "wdays_end": "24:00",
       "wend_start": "00:00", "wend_end": "24:00", "oneday_amt": 7000}


class Rules:
    """평일 09:00~17:00 과금 · 주말·공휴일 무료 · 24시간 출입."""

    def lookup(self, pid, day, is_holiday=False):
        value = replace(rule(), access_status="confirmed_open")
        if day.weekday() >= 5 or is_holiday:
            return replace(value, fee_mode="free", fee_windows=())
        return value


class ClosedRules(Rules):
    """18:00에 닫는 시설."""

    def lookup(self, pid, day, is_holiday=False):
        from src.serve.access_rules import TimeWindow
        return replace(super().lookup(pid, day, is_holiday),
                       entry_windows=(TimeWindow(540, 1080),),
                       exit_windows=(TimeWindow(540, 1080),))


class FareQuoteTests(unittest.TestCase):
    def quote(self, arrival, minutes, codes=None, rules=None, **kwargs):
        return quote_fare(39, arrival, minutes, codes, access_rules=rules or Rules(),
                          holiday_checker=lambda day: False, lot=LOT, **kwargs)

    def test_id39_example_matches_document_shape(self):
        body = self.quote(datetime(2026, 9, 16, 16, tzinfo=KST), 240)
        self.assertEqual(body["parking_id"], 39)
        self.assertEqual(body["expected_departure_at"], "2026-09-16T20:00:00+09:00")
        self.assertTrue(body["access"]["available"])
        fare = body["fare"]
        self.assertEqual(fare["billable_minutes"], 60)
        self.assertEqual(fare["free_minutes_outside_fee_window"], 180)
        self.assertEqual(fare["payg"], 1000)
        self.assertEqual(fare["daily_pass"], 7000)
        self.assertEqual(fare["recommended_option"], "payg")
        self.assertEqual(fare["saving"], 6000)
        self.assertIsNone(fare["applied_benefit"])
        self.assertEqual(fare["fee_source"], "survey")

    def test_naive_arrival_is_read_as_kst(self):
        body = self.quote(datetime(2026, 9, 16, 16), 240)
        self.assertEqual(body["arrival_at"], "2026-09-16T16:00:00+09:00")

    def test_long_stay_prefers_prepaid_daily_pass(self):
        fare = self.quote(datetime(2026, 9, 16, 10, tzinfo=KST), 300)["fare"]
        self.assertEqual(fare["payg"], 16600)
        self.assertEqual(fare["daily_pass"], 7000)
        self.assertEqual(fare["recommended_option"], "daily_pass")
        self.assertEqual(fare["saving"], 9600)
        # 판매 여부·매진 정보가 없으므로 구매 가능으로 단정하지 않는다.
        self.assertIsNone(fare["daily_pass_purchasable"])

    def test_single_benefit_is_applied_with_evidence_note(self):
        fare = self.quote(datetime(2026, 9, 16, 16, tzinfo=KST), 240, ["경형자동차"])["fare"]
        self.assertEqual(fare["payg"], 500)
        self.assertEqual(fare["applied_benefit"]["code"], "경형자동차")
        self.assertIn("증빙", fare["applied_benefit"]["evidence_note"])

    def test_free_window_returns_zero_with_breakdown_reason(self):
        fare = self.quote(datetime(2026, 9, 16, 18, tzinfo=KST), 240)["fare"]
        self.assertEqual(fare["payg"], 0)
        self.assertEqual(fare["billable_minutes"], 0)

    def test_multiple_paid_dates_are_priced_per_day(self):
        fare = self.quote(datetime(2026, 9, 16, 16, tzinfo=KST), 1500)["fare"]
        self.assertEqual(fare["payg"], 26000)
        self.assertEqual(fare["recommended_option"], "payg")
        # 며칠치 일일권을 살 수 있는지는 확인되지 않아 선불 총액을 주지 않는다.
        self.assertIsNone(fare["daily_pass"])
        self.assertEqual(fare["daily_pass_list_price"], 7000)
        self.assertIsNone(fare["saving"])
        self.assertIn("연장·재구매", fare["daily_pass_note"])

    def test_access_unavailable_still_returns_reference_fare(self):
        body = self.quote(datetime(2026, 9, 16, 16, tzinfo=KST), 240, rules=ClosedRules())
        self.assertFalse(body["access"]["available"])
        self.assertEqual(body["access"]["reason"], "closes_before_departure")
        self.assertEqual(body["fare"]["payg"], 1000)

    def test_unknown_benefit_is_rejected(self):
        with self.assertRaises(UnknownBenefit) as ctx:
            self.quote(datetime(2026, 9, 16, 16, tzinfo=KST), 60, ["없는코드"])
        self.assertEqual(ctx.exception.codes, ["없는코드"])

    def test_multiple_benefits_are_priced_separately_and_cheapest_wins(self):
        body = self.quote(datetime(2026, 9, 16, 16, tzinfo=KST), 240,
                          ["경형자동차", "전통시장"])
        fare = body["fare"]
        codes = {o["code"]: o for o in fare["benefit_options"]}
        self.assertEqual(set(codes), {"경형자동차", "전통시장"})
        # 60분 유료 → 전통시장은 90분 면제라 0원, 경차는 50%라 500원.
        self.assertEqual(codes["전통시장"]["total"], 0)
        self.assertEqual(codes["경형자동차"]["total"], 500)
        self.assertEqual(fare["payg"], 0)
        self.assertEqual(fare["applied_benefit"]["code"], "전통시장")
        # 탈락한 자격도 사유와 함께 남는다.
        self.assertFalse(codes["경형자동차"]["applied"])
        self.assertIn("더 저렴", codes["경형자동차"]["rejected_reason"])

    def test_rates_are_never_multiplied_together(self):
        body = self.quote(datetime(2026, 9, 16, 16, tzinfo=KST), 240,
                          ["경형자동차", "다자녀"])
        # 둘 다 50%. 곱하면 250원이 되지만 겹쳐 적용하지 않는다.
        self.assertEqual(body["fare"]["payg"], 500)
        self.assertIn("조례", body["fare"]["benefit_stacking_note"])

    def test_ordinance_defined_combination_is_offered(self):
        body = self.quote(datetime(2026, 9, 16, 10, tzinfo=KST), 300,
                          ["환승주차", "경형자동차"])
        fare = body["fare"]
        self.assertEqual(fare["applied_benefit"]["code"], "환승주차_경차")
        # 우리가 곱해 만든 값이 아니라 표에 있는 항목임을 밝힌다.
        self.assertEqual(sorted(fare["applied_benefit"]["combined_from"]),
                         ["경형자동차", "환승주차"])

    def test_missing_parking_raises(self):
        with self.assertRaises(UnknownParking):
            quote_fare(999999, datetime(2026, 9, 16, 16, tzinfo=KST), 60, [],
                       access_rules=Rules(), lot=None, holiday_checker=lambda day: False)

    def test_without_access_rules_fare_still_computes_and_access_is_unknown(self):
        body = quote_fare(39, datetime(2026, 9, 16, 16, tzinfo=KST), 240, [],
                          access_rules=None, lot=LOT, holiday_checker=lambda day: False)
        self.assertIsNone(body["access"]["available"])
        self.assertEqual(body["fare"]["fee_source"], "legacy_db_unverified")


if __name__ == "__main__":
    unittest.main()
