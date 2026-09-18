#!/usr/bin/env python3
"""reports/태영.txt 조사 + data/processed/parking_access_rules.csv(enterable_status)를
재료로 서비스용 data/raw/parking_access_rules.csv(주차장 x 요일그룹 1행)를 만든다.

access_status(입출차 가능 여부)는 access_rule이 아니라 **enterable_status**에서 가져온다.
access_rule은 "API 생존 시간"(label_valid) 축이고 서로 다른 개념이다(A22 참고).

기본은 dry-run이다. --write를 줘야 실제로 data/raw/parking_access_rules.csv에 쓴다.
쓰기 전 항상 src.serve.access_rules.validate_access_rules()로 검증한다.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import PARKING_DB, PARKING_ACCESS_RULES_CSV
from src.serve.access_rules import FIELDS, validate_access_rules, format_result

DAY_GROUPS = ("weekday", "saturday", "sunday_holiday")
CHECKED_AT = date(2026, 9, 15)  # 조사 시점(태영 확인)
EVIDENCE_REF = "docs/reports/태영.txt"
NOTE_SUFFIX = ("(네이버지도 기준, 일부는 공식 운영시간 미표기) "
               "[evidence_method는 임시 official_web — enum에 web_map 추가되면 교체 예정]")
OVERNIGHT_UNSURVEYED = "야간 방치(overnight) 가능 여부는 조사 안 됨, 보수적으로 false"

# access_status는 enterable_status로 결정한다. 이 dict는 이 특수 1곳(월정전용)만 덮어쓴다 —
# enterable_status="unknown"이지만 실제로는 확정된 사실(회원 전용)이라 unknown이 아니다.
ACCESS_STATUS_OVERRIDE = {301: ("confirmed_restricted", False,
                                 "24시간 운영이지만 월정기권 전용 — 일반 시간제 차량 이용 불가(조사 확인)")}

# ── 요금 창(fee_windows) 자료 — access_status와 무관, 태영.txt 운영/요금 시간 그대로 ──
# confirmed_open이 될 59곳 전부를 커버해야 한다(아래 cross-check가 확인한다).

PAID_24H = {12: "관악역1환승"}  # 24시간 유료

WEEKDAY_ONLY_FEE = {  # 평일만 유료, 주말 전부 무료
    13: ("관악역2환승", "09:00", "17:00"), 24: ("관악역3환승", "09:00", "17:00"),
    25: ("관악역4환승", "09:00", "17:00"), 39: ("안양7동노외", "09:00", "17:00"),
    27: ("인덕원동노외", "09:00", "20:00"), 82: ("명학동노외", "09:00", "18:00"),
    40: ("안양6동1노외", "09:00", "18:00"), 42: ("느루소공원지하", "09:00", "18:00"),
    34: ("동편마을지하", "09:00", "18:00"), 121: ("호현마을1노외", "09:00", "18:00"),
    36: ("충훈동지하", "09:00", "18:00"), 37: ("관양동노외", "09:00", "18:00"),
    106: ("시청앞노상", "10:00", "18:00"),
}

WEEKDAY_SAT_FEE_SUN_FREE = {  # 월~토 유료, 일요일만 무료
    30: ("샘모루초교지하", "09:00", "20:00"), 183: ("호계시장2노외", "09:00", "22:00"),
    182: ("호계시장1노외", "09:00", "22:00"), 181: ("덕현공원지하", "09:00", "20:00"),
    29: ("남부노외", "08:00", "18:00"), 421: ("박달고가밑노상(노외)", "09:00", "19:00"),
    33: ("박달시장2노외", "09:00", "19:00"),
}

DAILY_SAME_FEE = {  # 매일 같은 시간만 유료, 그 외(야간) 자동 무료
    112: ("원스퀘어임시", "10:00", "22:00"), 31: ("스마트스퀘어지하", "09:00", "18:00"),
    9: ("예술공원노외", "09:00", "19:00"), 11: ("일번가노외", "10:00", "22:00"),
    17: ("화창초교지하", "09:00", "22:00"), 8: ("안양4동노외", "10:00", "20:00"),
    38: ("삼덕공원지하", "10:00", "20:00"), 28: ("삼덕노외", "10:00", "20:00"),
    26: ("수목원입구노외", "09:00", "19:00"), 20: ("병목안시민공원노외", "09:00", "19:00"),
    21: ("해동놀이터지하", "09:00", "22:00"), 201: ("비산소공원지하", "09:00", "22:00"),
}

# 관악노상 등 active_seq 49~68 20곳. enterable_status=always(입출차 24시간 가능),
# 조사표의 "공식 운영/요금 시간"은 fee_windows다 — entry/exit 제한이 아니다.
# {pid: (name, {day_group: (start,end)|None})}  None=그 요일 무료(fee 없음)
STREET_FEE_WINDOWS = {
    161: ("관악노상1-2", {"weekday": ("09:00", "19:00"), "saturday": None, "sunday_holiday": None}),
    162: ("관악노상1-3", {"weekday": ("09:00", "19:00"), "saturday": None, "sunday_holiday": None}),
    163: ("관악노상1-4", {"weekday": ("09:00", "19:00"), "saturday": None, "sunday_holiday": None}),
    164: ("관악노상1-5", {"weekday": ("09:00", "19:00"), "saturday": None, "sunday_holiday": None}),
    165: ("관악노상2-1", {"weekday": ("09:00", "19:00"), "saturday": None, "sunday_holiday": None}),
    166: ("관악노상2-2", {"weekday": ("09:00", "19:00"), "saturday": None, "sunday_holiday": None}),
    167: ("관악노상2-3", {"weekday": ("09:00", "19:00"), "saturday": None, "sunday_holiday": None}),
    168: ("관악노상2-4", {"weekday": ("09:00", "19:00"), "saturday": None, "sunday_holiday": None}),
    169: ("관악노상2-5", {"weekday": ("09:00", "19:00"), "saturday": None, "sunday_holiday": None}),
    170: ("관악노상2-6", {"weekday": ("09:00", "19:00"), "saturday": None, "sunday_holiday": None}),
    171: ("관악노상2-7", {"weekday": ("09:00", "19:00"), "saturday": None, "sunday_holiday": None}),
    172: ("인덕원동복개노상123", {"weekday": ("09:00", "18:00"), "saturday": None, "sunday_holiday": None}),
    241: ("먹거리촌노상1", {"weekday": ("10:00", "21:00"), "saturday": ("10:00", "21:00"),
                        "sunday_holiday": None}),
    261: ("먹거리촌노상2", {"weekday": ("10:00", "21:00"), "saturday": ("10:00", "21:00"),
                        "sunday_holiday": None}),
    262: ("먹거리촌노상3", {"weekday": ("10:00", "21:00"), "saturday": ("10:00", "21:00"),
                        "sunday_holiday": None}),
    56: ("학원가노상", {"weekday": ("10:00", "20:00"), "saturday": ("10:00", "20:00"),
                     "sunday_holiday": None}),
    23: ("덕천노상", {"weekday": ("09:00", "17:00"), "saturday": None, "sunday_holiday": None}),
    52: ("남부노상", {"weekday": ("10:00", "18:00"), "saturday": None, "sunday_holiday": None}),
    53: ("안양역1노상", {"weekday": ("10:00", "22:00"), "saturday": ("10:00", "22:00"),
                      "sunday_holiday": ("10:00", "22:00")}),
    48: ("안양역2노상", {"weekday": ("10:00", "22:00"), "saturday": ("10:00", "22:00"),
                      "sunday_holiday": ("10:00", "22:00")}),
}

FEE_UNKNOWN_ALWAYS_OPEN = {  # entry는 always로 확인됐지만 공식 요금 시간을 못 찾음
    461: "안양6동3노외", 521: "안양6동4노외", 522: "안양6동5노외(일반)", 441: "율목복지관옆공영",
}

AMBIGUOUS_24H_UNKNOWN_FEE = {18: "개나리놀이터지하", 122: "호현마을2노외"}  # "24시간 운영"만 확인

# 조사표에 전혀 없음 + 고정 feed(predict_ok=0, 조사 대상 아님)
FULLY_UNKNOWN = {10: "인덕원환승", 16: "평촌지하", 108: "석수역좌우측노상", 81: "비호교1,2노외",
                  401: "호원어린이공원노외"}
DEAD_FEED_UNKNOWN = {3: "예술공원고가밑", 6: "명학역노상", 15: "석수대형화물", 19: "냉천놀이터지하",
                      35: "공업부지노외", 41: "안양6동2노외", 45: "평촌역노상", 46: "범계역노상",
                      47: "동안노상", 55: "안양3동노상", 57: "호계고가밑", 61: "수리산1노상",
                      101: "문예노상", 102: "수리산2노상", 103: "인덕원1노상", 104: "인덕원2노상",
                      105: "관양시장노상", 107: "삼막사노상", 109: "관악3노상", 110: "삼봉노상",
                      111: "먹거리촌노외"}


def _row(pid, name, day_group, entry, exit_, fee, fee_mode, overnight, status,
         general_public, note):
    return dict(parking_id=pid, name=name, day_group=day_group,
                entry_windows=entry, exit_windows=exit_, fee_windows=fee, fee_mode=fee_mode,
                overnight_allowed="true" if overnight else "false", access_status=status,
                general_public="true" if general_public else "false",
                effective_from=str(CHECKED_AT), effective_to="", checked_at=str(CHECKED_AT),
                evidence_method="official_web" if status != "unknown" else "",
                evidence_ref=EVIDENCE_REF if status != "unknown" else "", note=note)


def load_enterable():
    df = pd.read_csv(PARKING_ACCESS_RULES_CSV)
    required = {"parking_id", "name", "enterable_status", "weekday_access_start",
                "weekday_access_end", "weekday_access_status", "saturday_access_start",
                "saturday_access_end", "saturday_access_status", "sunday_access_start",
                "sunday_access_end", "sunday_access_status"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"data/processed CSV에 필요한 컬럼이 없습니다: {sorted(missing)}")
    return df.set_index("parking_id")


def check_bucket_consistency(enterable):
    """수작업 fee 버킷이 실제 enterable_status=always와 어긋나는 행을 찾는다."""
    open_buckets = {**{p: n for p, n in PAID_24H.items()},
                    **{p: n for p, (n, *_r) in WEEKDAY_ONLY_FEE.items()},
                    **{p: n for p, (n, *_r) in WEEKDAY_SAT_FEE_SUN_FREE.items()},
                    **{p: n for p, (n, *_r) in DAILY_SAME_FEE.items()},
                    **{p: n for p, (n, *_r) in STREET_FEE_WINDOWS.items()},
                    **FEE_UNKNOWN_ALWAYS_OPEN, **AMBIGUOUS_24H_UNKNOWN_FEE}
    mismatches = []
    for pid, name in open_buckets.items():
        actual = enterable.loc[pid, "enterable_status"] if pid in enterable.index else None
        if actual != "always":
            mismatches.append((pid, name, "always(기대)", actual))
    restricted_fixed = {7: "안양2동노외", 14: "호계3동노외", 22: "박달시장1노상노외"}
    for pid, name in restricted_fixed.items():
        actual = enterable.loc[pid, "enterable_status"] if pid in enterable.index else None
        if actual != "fee_hours_only":
            mismatches.append((pid, name, "fee_hours_only(기대)", actual))
    return mismatches


def build(enterable):
    rows = []

    def open_row(pid, name, fee_by_day, note):
        for day in DAY_GROUPS:
            fee = fee_by_day.get(day)
            fee_windows = f"{fee[0]}-{fee[1]}" if fee else ""
            fee_mode = "paid_window_free_outside" if fee else "free"
            rows.append(_row(pid, name, day, "00:00-24:00", "00:00-24:00", fee_windows,
                              fee_mode, True, "confirmed_open", True, note + " " + NOTE_SUFFIX))

    def open_row_unknown_fee(pid, name, note):
        for day in DAY_GROUPS:
            rows.append(_row(pid, name, day, "00:00-24:00", "00:00-24:00", "", "unknown", True,
                              "confirmed_open", True, note + " " + NOTE_SUFFIX))

    for pid, name in PAID_24H.items():
        open_row(pid, name, {d: ("00:00", "24:00") for d in DAY_GROUPS}, "24시간 운영·24시간 유료")

    for pid, (name, s, e) in WEEKDAY_ONLY_FEE.items():
        open_row(pid, name, {"weekday": (s, e)}, "평일만 유료, 주말 무료개방(입출차 24시간 가능)")

    for pid, (name, s, e) in WEEKDAY_SAT_FEE_SUN_FREE.items():
        open_row(pid, name, {"weekday": (s, e), "saturday": (s, e)},
                  "월~토 유료, 일요일 무료개방(입출차 24시간 가능)")

    for pid, (name, s, e) in DAILY_SAME_FEE.items():
        open_row(pid, name, {d: (s, e) for d in DAY_GROUPS},
                  "매일 같은 시간만 유료, 그 외 무료개방(입출차 24시간 가능)")

    for pid, (name, windows) in STREET_FEE_WINDOWS.items():
        fee_by_day = {d: w for d, w in windows.items() if w is not None}
        open_row(pid, name, fee_by_day,
                  "운영시간 외에도 무료개방·입출차 24시간 가능(조사 확인). "
                  "운영시간 외엔 실시간 API만 멈춤(label_valid 축, access_rule 참고) — 입출차와는 무관")

    for pid, name in FEE_UNKNOWN_ALWAYS_OPEN.items():
        open_row_unknown_fee(pid, name, "입출차 24시간 가능(조사 확인). 공식 요금 징수시간 미확인")

    for pid, name in AMBIGUOUS_24H_UNKNOWN_FEE.items():
        open_row_unknown_fee(pid, name, "24시간 운영으로만 확인, 유료/무료 여부 불명확")

    for pid, (status, general_public, note) in ACCESS_STATUS_OVERRIDE.items():
        name = enterable.loc[pid, "name"] if pid in enterable.index else "?"
        for day in DAY_GROUPS:
            rows.append(_row(pid, name, day, "closed", "closed", "", "unknown", False,
                              status, general_public,
                              note + " " + OVERNIGHT_UNSURVEYED + " " + NOTE_SUFFIX))

    for pid, name in {7: "안양2동노외", 14: "호계3동노외", 22: "박달시장1노상노외"}.items():
        r = enterable.loc[pid]
        # pid 7은 access_rule=FIXED_ACCESS_10_22_DAILY라 요일별 status 컬럼이 비어 있다(SAME_AS_FEE_HOURS
        # 전용 컬럼). "매일 10:00~22:00"이므로 weekday 값을 3요일 모두에 그대로 적용한다.
        fixed_daily = pid == 7
        for day, col_prefix in zip(DAY_GROUPS, ("weekday", "saturday", "sunday")):
            use_prefix = "weekday" if fixed_daily else col_prefix
            status_col, start_col, end_col = (f"{use_prefix}_access_status",
                                                f"{use_prefix}_access_start", f"{use_prefix}_access_end")
            day_status = None if fixed_daily else r[status_col]
            if day_status == "closed" or pd.isna(r[start_col]):
                rows.append(_row(pid, name, day, "closed", "closed", "", "closed", False,
                                  "confirmed_restricted", True,
                                  f"해당 요일 종일 입출차 불가(조사 확인). {OVERNIGHT_UNSURVEYED} {NOTE_SUFFIX}"))
            else:
                win = f"{r[start_col]}-{r[end_col]}"
                rows.append(_row(pid, name, day, win, win, win, "paid_window_free_outside",
                                  False, "confirmed_restricted", True,
                                  f"이 시간에만 입출차 가능, 그 외 입출차 불가(조사 확인). "
                                  f"요금 구간=입출차 가능 구간과 동일. {OVERNIGHT_UNSURVEYED} {NOTE_SUFFIX}"))

    for pid, name in FULLY_UNKNOWN.items():
        note = "조사표에 없음, 출처 확인 필요(예: 네이버지도)"
        if pid == 401:
            note = "조사표에서 보류/미확인으로 명시"
        for day in DAY_GROUPS:
            rows.append(_row(pid, name, day, "", "", "", "unknown", False, "unknown", True,
                              f"{note}. {OVERNIGHT_UNSURVEYED}"))

    for pid, name in DEAD_FEED_UNKNOWN.items():
        for day in DAY_GROUPS:
            rows.append(_row(pid, name, day, "", "", "", "unknown", False, "unknown", True,
                              f"고정 feed, 예측·추천 대상 아님(predict_ok=0). 조사 대상 아님. "
                              f"{OVERNIGHT_UNSURVEYED}"))

    df = pd.DataFrame(rows, columns=list(FIELDS))
    covered = set(df["parking_id"])
    missing = set(enterable.index) - covered
    if missing:
        raise ValueError(f"data/processed엔 있는데 이 스크립트가 안 다루는 parking_id: {sorted(missing)}")
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="실제로 data/raw/parking_access_rules.csv에 쓴다")
    parser.add_argument("--sample-pids", type=int, nargs="*",
                         default=[12, 13, 30, 461, 18, 301, 7, 161, 108])
    args = parser.parse_args()

    enterable = load_enterable()
    mismatches = check_bucket_consistency(enterable)
    print("=== 소스 불일치 확인 (수작업 fee 버킷 vs data/processed enterable_status) ===")
    if mismatches:
        for pid, name, expected, actual in mismatches:
            print(f"  어긋남: pid={pid} {name} 기대={expected} 실제 enterable_status={actual!r}")
    else:
        print("  없음 — 모든 버킷이 enterable_status와 일치")
    print()

    df = build(enterable)
    print(f"총 {df['parking_id'].nunique()}곳 x 3요일그룹 = {len(df)}행")
    print(f"access_status 분포:\n{df['access_status'].value_counts().to_string()}\n")

    scratch = ROOT / "data" / "raw" / "_parking_access_rules.dryrun.csv"
    df.to_csv(scratch, index=False, encoding="utf-8-sig")
    result = validate_access_rules(scratch, PARKING_DB)
    print(format_result(result))
    scratch.unlink()

    print("\n=== 이번에 바뀐 20곳(관악노상 등) 샘플 ===")
    changed_sample = df[df["parking_id"].isin([161, 56, 53])]
    print(changed_sample.to_string(index=False))

    print("\n=== 기타 샘플 ===")
    sample = df[df["parking_id"].isin(args.sample_pids)]
    print(sample.to_string(index=False))

    if args.write:
        if not result.valid:
            print("\n검증 실패 — 쓰지 않음")
            sys.exit(1)
        out = ROOT / "data" / "raw" / "parking_access_rules.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n저장 완료: {out}")
    else:
        print("\n(dry-run — 실제로 쓰려면 --write)")


if __name__ == "__main__":
    main()
