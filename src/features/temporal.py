#!/usr/bin/env python3
"""
운영시간 상대 피처.

★ 전부 **target_time**(차가 도착하는 시각) 기준이다. 관측 시각 기준이 아니다.
  예측이 답해야 하는 질문은 "도착했을 때 자리가 있나" 이고,
  그 시점의 개·폐장 여부가 점유율을 지배한다.

★ 「00:00~00:00」 은 24시간이 아니라 **미운영**이다. 주말 57곳이 이 표기다.
  진짜 24시간은 「00:00~24:00」(7곳). fare._open_window 와 같은 규약을 쓴다.

  is_operating   운영 중이면 1
  min_to_open    개장까지 남은 분 (운영 중이면 0, 미운영일이면 큰 값)
  min_to_close   폐장까지 남은 분 (운영 중이 아니면 0)
  is_free_now    요금이 0원인 시각이면 1 (일요일·공휴일 또는 운영시간 밖)
"""
CLOSED_SENTINEL = 1440          # 그날 아예 안 여는 경우의 min_to_open


def _hhmm(s):
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def open_window(lot, day):
    """(시작분, 종료분). 종료가 시작보다 작으면 자정 넘김으로 본다.
    day: 0=월 … 6=일.  fare._open_window 와 동일 규약."""
    if day >= 5:
        s, e = lot.get("wend_start"), lot.get("wend_end")
        if s is None and e is None:
            s, e = lot.get("wdays_start"), lot.get("wdays_end")
    else:
        s, e = lot.get("wdays_start"), lot.get("wdays_end")
    a, b = _hhmm(s), _hhmm(e)
    if a is None or b is None:
        return 0, 1440                 # 값 자체가 없으면 24시간 운영
    if a == b:
        return 0, 0                    # ★ 00:00~00:00 = 미운영
    if b < a:
        b += 1440
    return a, b


def oprtime_features(lot, target_time, sunday_free=True):
    """target_time 하나에 대한 피처 dict."""
    day = target_time.weekday()
    pos = target_time.hour * 60 + target_time.minute
    a, b = open_window(lot, day)

    if b <= a:                                     # 그날 미운영
        return {"is_operating": 0, "min_to_open": CLOSED_SENTINEL,
                "min_to_close": 0, "is_free_now": 1}

    inside = a <= pos < b or (b > 1440 and pos + 1440 < b)
    if inside:
        eff = pos + 1440 if pos < a else pos       # 자정 넘김 보정
        out = {"is_operating": 1, "min_to_open": 0,
               "min_to_close": max(0, b - eff)}
    else:
        out = {"is_operating": 0,
               "min_to_open": (a - pos) if pos < a else CLOSED_SENTINEL,
               "min_to_close": 0}
    # 일요일 전면 무료(별표1 비고 4) 또는 운영시간 밖이면 0원
    out["is_free_now"] = int(out["is_operating"] == 0 or (sunday_free and day == 6))
    return out


OPR_COLS = ["is_operating", "min_to_open", "min_to_close", "is_free_now"]
