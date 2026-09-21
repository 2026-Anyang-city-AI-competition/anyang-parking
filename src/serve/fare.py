#!/usr/bin/env python3
"""
주차요금 계산기 — 누진제.

  from src.serve.fare import calc_fare
  calc_fare(lot, start_dt, minutes, discount=None) -> dict | None

lot 은 dict 다 (필요한 키만 쓴다):
  type   "노상"/"노외"/"부설"        grade  1~5
  wdays_start/end · wend_start/end   "HH:MM"  (없으면 24시간 운영으로 본다)

★ 기존 「기본요금 + 고정 단가」 사양은 틀렸다. 2시간에 35%, 3시간에 51% 과소계산됐다.
★ 운영시간 외 구간은 0원이다. 관측의 50.4% 가 이 구간이다.
★ GITS addUnitFare 를 전 구간에 쓰면 안 된다 — 그건 30~60분 구간 값 하나다.

  python3 src/serve/fare.py     # 예시 출력
"""
from datetime import datetime, timedelta, timezone

try:
    from src.serve import fare_tables as T
except ImportError:                      # 단독 실행용
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.serve import fare_tables as T


def resolve_type(name, div=None, std_type=None):
    """포털 `div` 의 '위탁'은 위치 유형이 아니라 운영 방식이라 요금표에 못 넣는다.
    우선순위: 표준데이터 유형 > 주차장명 규칙 > div

    ⚠️ GITS `pkplcTypeNm` 은 이 14곳을 전부 '기타'로 준다.
      정본 §7 의 "GITS 로 해소된다"는 성립하지 않는다(2026-09-04 실측).
      이름 규칙 결과가 정본 §7 의 실측(위탁 14곳 = 노상 13 / 노외 1)과 정확히 일치한다."""
    if std_type in ("노상", "노외", "부설"):
        return std_type
    n = name or ""
    if "노상" in n: return "노상"
    if "노외" in n or "지하" in n or "타워" in n: return "노외"
    if "고가밑" in n: return "노상"          # 도로 하부. 표준데이터에서 노상으로 확인됨
    if div in ("노상", "노외", "부설"): return div
    return None


def _hhmm(s):
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _open_window(lot, day):
    """그 요일의 (시작분, 종료분). 없으면 24시간 운영으로 본다.
    day: 0=월 … 5=토, 6=일"""
    if day >= 5:
        s, e = lot.get("wend_start"), lot.get("wend_end")
        if s is None and e is None:
            s, e = lot.get("wdays_start"), lot.get("wdays_end")
    else:
        s, e = lot.get("wdays_start"), lot.get("wdays_end")
    a, b = _hhmm(s), _hhmm(e)
    if a is None or b is None:
        return 0, 1440                    # 값 자체가 없으면 24시간 운영으로 본다
    if a == b:
        # ★ "00:00~00:00" 은 24시간이 아니라 **미운영**이다.
        #   실측: 주말 start=end 가 89곳 중 58곳(57곳이 00:00~00:00) — 주말 미운영.
        #   진짜 24시간 운영은 "00:00~24:00" 으로 들어온다(7곳).
        #   과소청구가 과다청구보다 안전하므로 미운영으로 처리한다.
        return 0, 0
    if b < a:                             # 자정 넘김
        b += 1440
    return a, b


def operating_minutes(lot, start_dt, minutes, sunday_free=None):
    """주차 구간 중 '과금 대상' 분만 센다.
    운영시간 외는 0원이고, 일요일·공휴일도 무료다(비고 4)."""
    if sunday_free is None:
        sunday_free = T.SUNDAY_HOLIDAY_FREE
    billable, cur = 0, start_dt
    left = int(minutes)
    while left > 0:
        day = cur.weekday()
        day_start = cur.replace(hour=0, minute=0, second=0, microsecond=0)
        pos = int((cur - day_start).total_seconds() // 60)
        take = min(left, 1440 - pos)
        if not (sunday_free and day == 6):          # 일요일 전체 무료
            a, b = _open_window(lot, day)
            lo, hi = max(pos, a), min(pos + take, b)
            if hi > lo:
                billable += hi - lo
        cur += timedelta(minutes=take)
        left -= take
    return billable


def surveyed_fee_schedule(lot, start_dt, minutes, access_rules, parking_id,
                         sunday_free=None, holiday_checker=None):
    """조사된 과금창 우선. 누락·미확인 요금은 DB 창으로 폴백하고 출처를 표시한다."""
    from src.serve.access_check import is_korean_holiday
    holiday_checker = holiday_checker or is_korean_holiday
    sunday_free = T.SUNDAY_HOLIDAY_FREE if sunday_free is None else sunday_free
    kst = timezone(timedelta(hours=9))
    start = start_dt.replace(tzinfo=kst) if start_dt.tzinfo is None else start_dt.astimezone(kst)
    end = start + timedelta(minutes=minutes)
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    schedule = []
    while day < end:
        tomorrow = day + timedelta(days=1)
        rule = access_rules.lookup(parking_id, day.date(), is_holiday=holiday_checker(day.date()))
        verified = rule is not None and rule.fee_mode != "unknown"
        if verified:
            windows = [(w.start_min, w.end_min) for w in rule.fee_windows]
        elif sunday_free and (day.weekday() == 6 or holiday_checker(day.date())):
            windows = []
        else:
            a, b = _open_window(lot, day.weekday())
            windows = [(a, b)] if b > a else []
        segments = []
        for a, b in windows:
            lo = max(start, day + timedelta(minutes=a))
            hi = min(end, tomorrow, day + timedelta(minutes=b))
            if hi > lo:
                segments.append({"start": lo.isoformat(), "end": hi.isoformat(),
                                 "minutes": (hi - lo).total_seconds() / 60})
        schedule.append({"date": day.date().isoformat(),
                         "source": "survey" if verified else "legacy_db_unverified",
                         "window_minutes": sum(b-a for a, b in windows),
                         "billable_minutes": sum(s["minutes"] for s in segments),
                         "segments": segments})
        day = tomorrow
    return schedule


def legacy_fee_schedule(lot, start_dt, minutes, sunday_free=None):
    """조사 규칙이 없을 때의 날짜별 과금표. `surveyed_fee_schedule` 과 같은 모양이다.

    하루를 넘는 주차를 날짜별로 계산하려면 두 경로가 같은 구조를 줘야 한다."""
    if sunday_free is None:
        sunday_free = T.SUNDAY_HOLIDAY_FREE
    end = start_dt + timedelta(minutes=int(minutes))
    day = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    schedule = []
    while day < end:
        tomorrow = day + timedelta(days=1)
        if sunday_free and day.weekday() == 6:
            windows = []
        else:
            a, b = _open_window(lot, day.weekday())
            windows = [(a, b)] if b > a else []
        segments = []
        for a, b in windows:
            lo = max(start_dt, day + timedelta(minutes=a))
            hi = min(end, tomorrow, day + timedelta(minutes=b))
            if hi > lo:
                segments.append({"start": lo.isoformat(), "end": hi.isoformat(),
                                 "minutes": (hi - lo).total_seconds() / 60})
        schedule.append({"date": day.date().isoformat(), "source": "legacy_db_unverified",
                         "window_minutes": sum(b - a for a, b in windows),
                         "billable_minutes": sum(x["minutes"] for x in segments),
                         "segments": segments})
        day = tomorrow
    return schedule


def _progressive(rate, billable):
    """누진 계산. 첫 구간은 정액, 이후는 10분 단위 올림."""
    first30, *tiers = rate
    if billable <= 0:
        return 0, []
    total = first30
    # 화면엔 정수 분만 보인다(round). 올림 단위(units)는 반올림 전 span 으로 계산해 금액엔 영향 없다.
    bd = [{"seg": T.SEG_NAMES[0], "min": round(min(billable, 30)), "amt": first30}]
    prev = 30
    for i, bound in enumerate(T.SEG_BOUNDS[1:] + (None,)):
        if billable <= prev:
            break
        end = billable if bound is None else min(billable, bound)
        span = end - prev
        units = -(-span // 10)                     # 10분 단위 올림
        amt = units * tiers[i]
        total += amt
        bd.append({"seg": T.SEG_NAMES[i + 1], "min": round(span), "amt": amt})
        prev = end
    return total, bd


def calc_fare(lot, start_dt, minutes, discount=None, sunday_free=None,
              apply_daily_pass_cap=False, access_rules=None, parking_id=None,
              holiday_checker=None):
    """반환 dict. 계산 불가면 None 이 아니라 reason 을 담은 dict 를 준다
    (서비스가 죽으면 안 된다).

    ★ 하루를 넘는 주차는 **날짜별로** 누진과 일 상한을 적용하고 합산한다.
      비고 10 의 상한은 「일 최대요금」이므로 하루 단위로 건다. 여러 날의 유료분을
      한 줄로 이어 누진하면 마지막 구간 단가가 계속 붙어 과다청구가 된다.
    ★ 일일권은 **입차 당일의 유료시간**을 사는 상품으로 본다(별표 5 가 운영시간별로
      값을 정한다). 하루를 넘으면 몇 장이 필요한지는 세지만, 연장·재구매가 가능한지는
      확인된 바 없으므로 선불 총액을 확정하지 않는다.
    """
    out = {"total": None, "reason": None, "breakdown": [], "capped": False,
           "billable_min": 0, "free_minutes": 0, "daily_pass": None,
           "daily_pass_better_after_min": None, "raw_progressive": None,
           # ★ 일일권은 자동 상한이 아니라 '선불 상품'이다(별표1 비고 8).
           #   두 금액을 병기하고 "입차 시 구매" 를 안내한다.
           "total_prepaid": None, "recommend_prepaid": False, "prepaid_saving": None,
           "prepaid_reason": None, "daily_pass_days_required": 0,
           "daily_pass_scope": "입차 당일의 유료시간", "paid_days": 0}

    typ = lot.get("type")
    if typ not in ("노상", "노외", "부설"):
        typ = resolve_type(lot.get("name"), typ, lot.get("std_type"))
    grade = lot.get("grade")
    if typ == "부설":
        out["reason"] = "부설주차장은 추천 대상이 아니다(별표 1 부설 행은 누진 없음)"
        return out
    try:
        grade = int(grade)
    except (TypeError, ValueError):
        out["reason"] = f"급지 결측 (grade={grade!r})"
        return out
    rate = T.PROGRESSIVE.get((typ, grade))
    if rate is None:
        out["reason"] = f"요금표에 없는 조합 (유형={typ!r}, 급지={grade})"
        return out

    surveyed = access_rules is not None and parking_id is not None
    schedule = (surveyed_fee_schedule(lot, start_dt, minutes, access_rules, parking_id,
                                      sunday_free, holiday_checker) if surveyed
                else legacy_fee_schedule(lot, start_dt, minutes, sunday_free))
    billable = sum(day["billable_minutes"] for day in schedule)
    out["fee_schedule"] = schedule
    sources = {day["source"] for day in schedule}
    out["fee_source"] = next(iter(sources)) if len(sources) == 1 else "mixed"
    out["free_minutes_outside_fee_window"] = max(0, minutes - billable)
    out["billable_min"] = billable

    breakdown = []
    if out["free_minutes_outside_fee_window"] > 0:
        breakdown.append({"kind": "free_window", "seg": "요금 징수시간 외",
                          "min": round(out["free_minutes_outside_fee_window"]), "amt": 0})

    # 별표 2-1 · 15분 미만 전액 면제
    if billable < T.FREE_UNDER_MIN:
        out["total"], out["breakdown"] = 0, breakdown
        out["reason"] = "요금 부과시간이 아닙니다."
        return out

    # 감면 중 '먼저 면제되는 분'을 뺀다. 1회 주차 기준이라 첫 유료일부터 소진한다.
    paid = [day for day in schedule if day["billable_minutes"] > 0]
    minutes_by_day = [day["billable_minutes"] for day in paid]
    d = T.DISCOUNTS.get(discount) if discount else None
    if d and d["free_min"]:
        remaining = min(billable, d["free_min"])
        out["free_minutes"] = remaining
        breakdown.append({"kind": "benefit_free", "seg": f"{discount} 면제",
                          "min": round(remaining), "amt": 0})
        for i, day_minutes in enumerate(minutes_by_day):
            used = min(day_minutes, remaining)
            minutes_by_day[i] = day_minutes - used
            remaining -= used
            if remaining <= 0:
                break
        if sum(minutes_by_day) == 0:
            out["total"], out["breakdown"] = 0, breakdown
            out["reason"] = f"{discount}: {d['note']} — 면제 구간 내"
            return out

    out["paid_days"] = sum(1 for m in minutes_by_day if m > 0)
    multi_day = out["paid_days"] > 1

    # 날짜별 누진 + 날짜별 일 상한
    total = raw = 0
    for day, day_minutes in zip(paid, minutes_by_day):
        if day_minutes <= 0:
            continue
        day_total, rows = _progressive(rate, day_minutes)
        raw += day_total
        for row in rows:
            entry = {"kind": "progressive", **row}
            if multi_day:
                entry["date"] = day["date"]
                entry["seg"] = f"{day['date'][5:]} {row['seg']}"
            breakdown.append(entry)
        caps = [T.DAILY_CAP]
        if apply_daily_pass_cap:
            day_pass = _day_pass(day, grade)
            if day_pass is not None:
                caps.append(day_pass)          # 후불에도 일일권 상한을 적용하고 싶을 때만
        cap = min(caps)
        if day_total > cap:
            breakdown.append({"kind": "daily_cap",
                              "seg": (f"{day['date'][5:]} 일 최대 상한" if multi_day
                                      else "일 최대 상한"),
                              "min": 0, "amt": cap - day_total})
            day_total, out["capped"] = cap, True
        total += day_total
    out["raw_progressive"] = raw

    # 감면율
    if d and d["rate"] != 1.0:
        discounted = total * d["rate"]
        breakdown.append({"kind": "benefit_rate",
                          "seg": f"{discount} {round((1 - d['rate']) * 100)}% 감면",
                          "min": 0, "amt": int(discounted - total)})
        total = discounted

    # 비고 11 · 100원 미만 절사 (감면 '후')
    rounded = int(total // T.ROUND_DOWN_TO * T.ROUND_DOWN_TO)
    if rounded != total:
        breakdown.append({"kind": "round_down", "seg": "100원 미만 절사",
                          "min": 0, "amt": int(rounded - total)})
    out["total"] = rounded
    out["breakdown"] = breakdown

    # ── 일일주차권 (별표 5) — 입차 당일의 유료시간 기준 ──────────────
    entry_day = paid[0] if paid else None
    dp = _day_pass(entry_day, grade) if entry_day else None
    if dp is None and entry_day is None:
        a, b = _open_window(lot, start_dt.weekday())
        hours = (b - a) / 60
        dp = T.daily_pass(hours, grade) if float(hours).is_integer() else None
    out["daily_pass"] = dp
    out["daily_pass_days_required"] = out["paid_days"]
    if dp is None:
        hours = (entry_day["window_minutes"] / 60) if entry_day else 0
        out["reason"] = (f"일일주차권 표 밖 (유료시간 {hours:g}h, 급지 {grade}) "
                         f"— 상한 없음으로 처리")

    # ★ 선불 일일권과 병기 — 어느 쪽이 싼지 사용자가 고르게 한다
    if dp is not None and not multi_day:
        prepaid = dp
        if d and d["rate"] != 1.0:            # 별표2-2: 1일주차 요금은 70% 감면
            prepaid = int(dp * 0.3 // T.ROUND_DOWN_TO * T.ROUND_DOWN_TO)
        out["total_prepaid"] = prepaid
        out["recommend_prepaid"] = prepaid < out["total"]
        out["prepaid_saving"] = max(0, out["total"] - prepaid)
    elif dp is not None and multi_day:
        # 하루치 정가는 알지만 며칠치를 살 수 있는지는 규정이 확인되지 않았다.
        out["prepaid_reason"] = (
            f"유료 주차가 {out['paid_days']}일에 걸쳐 일일권 {out['paid_days']}장이 필요합니다. "
            f"연장·재구매 가능 여부가 확인되지 않아 선불 총액은 제시하지 않습니다.")

    # ★ 일일권이 더 싸지는 시점
    if dp is not None:
        found = None
        for m in range(10, 24 * 60 + 1, 10):
            v, _ = _progressive(rate, m)
            if v > dp:
                found = m - 9                # 그 10분 단위가 시작되는 분
                break
        out["daily_pass_better_after_min"] = found
    return out


def _day_pass(day, grade):
    """그 날의 유료시간 길이로 별표 5 를 조회한다. 표 밖이면 None — 보간하지 않는다."""
    if day is None:
        return None
    hours = day["window_minutes"] / 60
    return T.daily_pass(hours, grade) if float(hours).is_integer() else None


if __name__ == "__main__":
    lot1 = {"type": "노외", "grade": 1, "wdays_start": "09:00", "wdays_end": "22:00"}
    lot3 = {"type": "노외", "grade": 3, "wdays_start": "10:00", "wdays_end": "18:00"}
    mon = datetime(2026, 9, 7, 10, 0)        # 월요일 10:00
    print("=== 노외 1급지 (13시간 운영, 일일권 16,000) ===")
    for m in (14, 30, 40, 60, 90, 120, 180, 300):
        r = calc_fare(lot1, mon, m)
        print(f"  {m:>4}분 → {r['total']:>6,}원  {'(상한)' if r['capped'] else ''}")
    r = calc_fare(lot1, mon, 300)
    print(f"  일일권 {r['daily_pass']:,}원 · 일일권이 유리해지는 시점 "
          f"{r['daily_pass_better_after_min']}분")
    print("\n=== 노외 3급지 (8시간 운영, 일일권 5,000) ===")
    for m in (30, 60, 120, 180, 240):
        r = calc_fare(lot3, mon, m)
        print(f"  {m:>4}분 → {r['total']:>6,}원  {'(상한)' if r['capped'] else ''}")
    print("\n=== 내역 예시 (1급지 120분) ===")
    r = calc_fare(lot1, mon, 120)
    for b in r["breakdown"]:
        print(f"  {b['seg']:<10} {b['min']:>4}분  {b['amt']:>6,}원")
    print(f"  합계 {r['total']:,}원")
