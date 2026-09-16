"""도착부터 예상 출차까지 실제 출입 규칙을 검사한다. 요금시간은 사용하지 않는다."""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import math

import holidays

KST = timezone(timedelta(hours=9))


@lru_cache(maxsize=16)
def _holiday_calendar(year):
    return holidays.country_holidays("KR", years=[year])


def is_korean_holiday(day):
    return day in _holiday_calendar(day.year)


@dataclass(frozen=True)
class AccessDecision:
    parking_id: int
    available: bool | None
    excluded: bool
    reason: str
    message: str
    arrival_at: str
    expected_departure_at: str
    safety_margin_minutes: int
    access_closes_at: str | None = None

    def to_dict(self):
        return asdict(self)


def _kst(value):
    if not isinstance(value, datetime):
        raise ValueError("시각은 datetime이어야 합니다")
    return value.replace(tzinfo=KST) if value.tzinfo is None else value.astimezone(KST)


def _merged(windows):
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(end, merged[-1][1])
        else:
            merged.append((start, end))
    return merged


def _containing(windows, value):
    return next(((start, end) for start, end in windows if start <= value < end), None)


def _first_gap(windows, start, end):
    cursor = start
    for lo, hi in _merged(windows):
        if hi <= cursor:
            continue
        if lo > cursor:
            return cursor
        cursor = max(cursor, hi)
        if cursor >= end:
            return None
    return cursor if cursor < end else None


def check_access(repository, parking_id, arrival_at, parking_minutes,
                 safety_margin_minutes=0, holiday_checker=is_korean_holiday):
    """확정 규칙으로 검사한다. 미확인은 available=None이며 임의로 제외하지 않는다.

    출차시각이 실제 폐쇄시각과 같으면 제외한다. 24시간 구간은 다음 날과
    이어서 검사하므로 자정 자체를 폐쇄로 취급하지 않는다. 안전여유는 명시적
    옵션이며 기본 0분이다. 요금 계산과 추천 순위 변경은 이 함수의 책임이 아니다.
    """
    if (isinstance(parking_minutes, bool) or not isinstance(parking_minutes, (int, float))
            or not math.isfinite(parking_minutes) or parking_minutes <= 0):
        raise ValueError("주차시간은 0보다 큰 유한한 분 값이어야 합니다")
    if (isinstance(safety_margin_minutes, bool) or not isinstance(safety_margin_minutes, int)
            or safety_margin_minutes < 0):
        raise ValueError("안전여유는 0 이상의 정수여야 합니다")
    arrival = _kst(arrival_at)
    departure = arrival + timedelta(minutes=parking_minutes)
    buffered_end = departure + timedelta(minutes=safety_margin_minutes)

    def decision(available, reason, message, closes=None):
        return AccessDecision(int(parking_id), available, available is False, reason, message,
                              arrival.isoformat(), departure.isoformat(), safety_margin_minutes,
                              closes.isoformat() if closes else None)

    days = {}
    day = arrival.date()
    while day <= buffered_end.date():
        days[day] = repository.lookup(parking_id, day, is_holiday=holiday_checker(day))
        day += timedelta(days=1)

    arrival_rule = days[arrival.date()]
    departure_rule = days[departure.date()]
    for rule in days.values():
        if rule is not None and not rule.general_public:
            return decision(False, "general_public_not_allowed", "일반 시간제 차량은 이용할 수 없어요.")

    entry_windows, exit_windows = [], []
    for day, rule in days.items():
        if rule is None:
            continue
        midnight = datetime.combine(day, datetime.min.time(), tzinfo=KST)
        for window in rule.entry_windows:
            entry_windows.append((midnight + timedelta(minutes=window.start_min),
                                  midnight + timedelta(minutes=window.end_min)))
        for window in rule.exit_windows:
            exit_windows.append((midnight + timedelta(minutes=window.start_min),
                                 midnight + timedelta(minutes=window.end_min)))
    entry_windows, exit_windows = _merged(entry_windows), _merged(exit_windows)

    if arrival_rule is not None and _containing(entry_windows, arrival) is None:
        return decision(False, "entry_closed_at_arrival", "도착 예정 시각에는 입차할 수 없어요.")
    exit_window = _containing(exit_windows, departure)
    if departure_rule is not None and exit_window is None:
        closes = next((end for start, end in exit_windows if arrival < end <= departure), None)
        return decision(False, "closes_before_departure", "예상 출차 시각에는 출차할 수 없어요.", closes)

    if any(rule is None for rule in days.values()):
        return decision(None, "access_schedule_unknown", "이용 시간대의 입출차 조건이 아직 확인되지 않았어요.")

    if safety_margin_minutes and buffered_end >= exit_window[1]:
        return decision(False, "insufficient_exit_margin", "폐쇄 전 출차 안전여유가 부족해요.", exit_window[1])

    # 입차가 끝나도 출차가 계속 가능하면 폐쇄가 아니다. 두 창의 합집합을 사용한다.
    for day, rule in days.items():
        midnight = datetime.combine(day, datetime.min.time(), tzinfo=KST)
        start, end = max(arrival, midnight), min(departure, midnight + timedelta(days=1))
        if end <= start or rule.overnight_allowed:
            continue
        windows = [(midnight + timedelta(minutes=w.start_min),
                    midnight + timedelta(minutes=w.end_min))
                   for w in rule.entry_windows + rule.exit_windows]
        gap = _first_gap(windows, start, end)
        if gap is not None:
            return decision(False, "closed_interval_crossed", "주차 구간이 출입 폐쇄시간을 걸쳐요.", gap)

    return decision(True, "available", "예상 입차·출차 시간에 이용할 수 있어요.")
