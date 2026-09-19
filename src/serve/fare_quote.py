#!/usr/bin/env python3
"""독립 요금 견적 — 추천을 다시 돌리지 않고 시간·할인만 바꿔 계산한다.

  from src.serve.fare_quote import quote_fare
  quote_fare(39, datetime(2026,9,16,16, tzinfo=KST), 240, [], access_rules=repo)

추천 파이프라인과 같은 `calc_fare`·`check_access`를 쓴다. 예측·경로·도보는 쓰지 않는다.
출입 판정은 요금 계산을 막지 않는다 — 이용 불가여도 요금은 참고값으로 돌려주고
`access.available`로 알린다(§2.1: 요금시간과 출입시간은 다른 시간이다).
"""
from datetime import datetime, timedelta, timezone

from src.serve import benefits as B
from src.serve import fare_tables as T
from src.serve.access_check import check_access, is_korean_holiday
from src.serve.candidates import load_lot
from src.serve.fare import calc_fare, resolve_type

KST = timezone(timedelta(hours=9))


class UnknownParking(Exception):
    """`parking.db.lots`에 없는 주차장."""


UnknownBenefit = B.UnknownBenefit


def _kst(value):
    return value.replace(tzinfo=KST) if value.tzinfo is None else value.astimezone(KST)


def quote_fare(parking_id, arrival_at, parking_minutes, benefit_codes=None,
               access_rules=None, safety_margin_minutes=0,
               holiday_checker=is_korean_holiday, lot=None):
    """§4.1 응답 규격의 견적 dict. 계산 불가는 `null + reason`으로 돌려준다."""
    codes = B.validate(list(benefit_codes or []))

    lot = lot if lot is not None else load_lot(parking_id)
    if lot is None:
        raise UnknownParking(parking_id)

    arrival = _kst(arrival_at)
    departure = arrival + timedelta(minutes=parking_minutes)

    access = {"available": None, "status": "unknown", "reason": "access_rules_unavailable",
              "message": "입출차 조건을 확인할 수 없어요.", "safety_margin_minutes": safety_margin_minutes}
    if access_rules is not None:
        decision = check_access(access_rules, parking_id, arrival, parking_minutes,
                                safety_margin_minutes=safety_margin_minutes,
                                holiday_checker=holiday_checker).to_dict()
        access = {"available": decision["available"],
                  "status": ("unknown" if decision["available"] is None
                             else "confirmed_open" if decision["available"]
                             else "confirmed_restricted"),
                  "reason": decision["reason"], "message": decision["message"],
                  "access_closes_at": decision["access_closes_at"],
                  "safety_margin_minutes": decision["safety_margin_minutes"]}

    calc_lot = {**lot, "type": resolve_type(lot.get("name"), lot.get("div"), lot.get("std_type"))}

    def price(code):
        return calc_fare(calc_lot, arrival, parking_minutes, discount=code,
                         access_rules=access_rules, parking_id=parking_id,
                         holiday_checker=holiday_checker)

    # 자격마다 따로 계산하고 가장 싼 하나만 적용한다. 감면율을 곱하지 않는다(§5.1).
    evaluation = B.evaluate(codes, price)
    benefit = evaluation["selected"]
    result = price(benefit)

    payg, prepaid = result["total"], result["total_prepaid"]
    # 일일권은 자동 상한이 아니라 선불 상품이다(별표1 비고 8). 둘을 병기하고
    # 판매 가능 여부는 단정하지 않는다(§4.2).
    recommended = saving = None
    if payg is not None:
        recommended = "daily_pass" if prepaid is not None and prepaid < payg else "payg"
        saving = abs(payg - prepaid) if prepaid is not None else None

    applied = None
    if benefit:
        label, _, evidence = B.BENEFIT_INFO.get(benefit, (benefit, "", ""))
        applied = {"code": benefit, "label": label, **T.DISCOUNTS[benefit],
                   "evidence": evidence, "evidence_note": B.EVIDENCE_NOTE,
                   "combined_from": evaluation["combined_from"]}
    return {
        "parking_id": int(parking_id),
        "name": lot.get("name"),
        "arrival_at": arrival.isoformat(),
        "expected_departure_at": departure.isoformat(),
        "parking_minutes": int(parking_minutes),
        "access": access,
        "fare": {
            "billable_minutes": result["billable_min"],
            "free_minutes_outside_fee_window": result.get("free_minutes_outside_fee_window", 0),
            "discount_free_minutes": result["free_minutes"],
            "payg": payg,
            "daily_pass": prepaid,
            "daily_pass_list_price": result["daily_pass"],
            "daily_pass_purchasable": None,   # 판매·매진 정보가 없으면 단정하지 않는다
            "daily_pass_days_required": result["daily_pass_days_required"],
            "daily_pass_scope": result["daily_pass_scope"],
            "daily_pass_note": result["prepaid_reason"],
            "daily_pass_better_after_min": result["daily_pass_better_after_min"],
            "paid_days": result["paid_days"],
            "recommended_option": recommended,
            "saving": saving,
            "capped": result["capped"],
            "raw_progressive": result["raw_progressive"],
            "applied_benefit": applied,
            # 고른 자격 전부의 계산 결과. 탈락한 자격은 사유가 붙는다.
            "benefit_options": evaluation["options"],
            "benefit_stacking_note": evaluation["stacking"],
            "breakdown": result["breakdown"],
            "fee_schedule": result.get("fee_schedule", []),
            "fee_source": result.get("fee_source", "legacy_db_unverified"),
            "reason": result["reason"],
        },
    }
