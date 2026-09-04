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
from datetime import datetime, timedelta

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


def _progressive(rate, billable):
    """누진 계산. 첫 구간은 정액, 이후는 10분 단위 올림."""
    first30, *tiers = rate
    if billable <= 0:
        return 0, []
    total = first30
    bd = [{"seg": T.SEG_NAMES[0], "min": min(billable, 30), "amt": first30}]
    prev = 30
    for i, bound in enumerate(T.SEG_BOUNDS[1:] + (None,)):
        if billable <= prev:
            break
        end = billable if bound is None else min(billable, bound)
        span = end - prev
        units = -(-span // 10)                     # 10분 단위 올림
        amt = units * tiers[i]
        total += amt
        bd.append({"seg": T.SEG_NAMES[i + 1], "min": span, "amt": amt})
        prev = end
    return total, bd


def calc_fare(lot, start_dt, minutes, discount=None, sunday_free=None,
              apply_daily_pass_cap=False):
    """반환 dict. 계산 불가면 None 이 아니라 reason 을 담은 dict 를 준다
    (서비스가 죽으면 안 된다)."""
    out = {"total": None, "reason": None, "breakdown": [], "capped": False,
           "billable_min": 0, "free_minutes": 0, "daily_pass": None,
           "daily_pass_better_after_min": None, "raw_progressive": None,
           # ★ 일일권은 자동 상한이 아니라 '선불 상품'이다(별표1 비고 8).
           #   두 금액을 병기하고 "입차 시 구매" 를 안내한다.
           "total_prepaid": None, "recommend_prepaid": False, "prepaid_saving": None}

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

    billable = operating_minutes(lot, start_dt, minutes, sunday_free)
    out["billable_min"] = billable

    # 별표 2-1 · 15분 미만 전액 면제
    if billable < T.FREE_UNDER_MIN:
        out["total"] = 0
        out["reason"] = f"운영시간 내 {billable}분 — 15분 미만 면제"
        return out

    # 감면 중 '먼저 면제되는 분'을 뺀다
    d = T.DISCOUNTS.get(discount) if discount else None
    if d and d["free_min"]:
        out["free_minutes"] = min(billable, d["free_min"])
        billable = max(0, billable - d["free_min"])
        if billable == 0:
            out["total"] = 0
            out["reason"] = f"{discount}: {d['note']} — 면제 구간 내"
            return out

    total, bd = _progressive(rate, billable)
    out["raw_progressive"] = total
    out["breakdown"] = bd

    # 일일주차권 (별표 5) — 운영시간 길이 기준
    a, b = _open_window(lot, start_dt.weekday())
    dp = T.daily_pass((b - a) / 60, grade)
    out["daily_pass"] = dp
    if dp is None:
        out["reason"] = (f"일일주차권 표 밖 (운영시간 {(b-a)/60:.0f}h, 급지 {grade}) "
                         f"— 상한 없음으로 처리")

    # 비고 10 · 누진 일 최대 상한 25,000. 일일권은 여기 포함하지 않는다(비고 8).
    caps = [T.DAILY_CAP]
    if apply_daily_pass_cap and dp is not None:
        caps.append(dp)                       # 후불에도 일일권 상한을 적용하고 싶을 때만
    cap = min(caps)
    if total > cap:
        total, out["capped"] = cap, True

    # 감면율
    if d and d["rate"] != 1.0:
        total = total * d["rate"]

    # 비고 11 · 100원 미만 절사 (감면 '후')
    total = int(total // T.ROUND_DOWN_TO * T.ROUND_DOWN_TO)
    out["total"] = total

    # ★ 선불 일일권과 병기 — 어느 쪽이 싼지 사용자가 고르게 한다
    if dp is not None:
        prepaid = dp
        if d and d["rate"] != 1.0:            # 별표2-2: 1일주차 요금은 70% 감면
            prepaid = int(dp * 0.3 // T.ROUND_DOWN_TO * T.ROUND_DOWN_TO)
        out["total_prepaid"] = prepaid
        out["recommend_prepaid"] = prepaid < total
        out["prepaid_saving"] = max(0, total - prepaid)

    # ★ 일일권이 더 싸지는 시점
    if dp is not None:
        prev_cap = out.get("_")
        lo, hi = 1, 24 * 60
        found = None
        for m in range(10, hi + 1, 10):
            v, _ = _progressive(rate, m)
            if v > dp:
                found = m - 9                # 그 10분 단위가 시작되는 분
                break
        out["daily_pass_better_after_min"] = found
    return out


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
