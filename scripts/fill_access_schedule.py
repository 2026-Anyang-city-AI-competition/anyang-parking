"""조사로 확인한 제한 주차장의 요일별 출입시간 보완.

실행: python scripts/fill_access_schedule.py
원본 CSV는 .csv.bak으로 한 번 보관한다. ID/이름이 DB와 다르면 저장하지 않는다.
빈 시간을 휴무로 추정하지 않도록 각 요일의 open/closed 상태도 기록한다.
근거: reports/태영.txt, reports/access_survey_priority.md.
"""
import shutil
import sqlite3
import sys
from pathlib import Path

import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import PARKING_DB, PARKING_ACCESS_RULES_CSV

DAYS = ("weekday", "saturday", "sunday")
# (DB 주차장명, 평일, 토요일, 일요일). None은 조사로 확인한 휴무다.
# 미확인 주차장은 이 표에 추가하지 않는다.
SCHEDULES = {
    14: ("호계3동노외", ("10:00", "18:00"), None, None),
    22: ("박달시장1노상노외", ("09:00", "19:00"), ("09:00", "19:00"), None),
    161: ("관악노상1-2", ("09:00", "19:00"), None, None),
    162: ("관악노상1-3", ("09:00", "19:00"), None, None),
    163: ("관악노상1-4", ("09:00", "19:00"), None, None),
    164: ("관악노상1-5", ("09:00", "19:00"), None, None),
    165: ("관악노상2-1", ("09:00", "19:00"), None, None),
    166: ("관악노상2-2", ("09:00", "19:00"), None, None),
    167: ("관악노상2-3", ("09:00", "19:00"), None, None),
    168: ("관악노상2-4", ("09:00", "19:00"), None, None),
    169: ("관악노상2-5", ("09:00", "19:00"), None, None),
    170: ("관악노상2-6", ("09:00", "19:00"), None, None),
    171: ("관악노상2-7", ("09:00", "19:00"), None, None),
    172: ("인덕원동복개노상123", ("09:00", "18:00"), None, None),
    241: ("먹거리촌노상1", ("10:00", "21:00"), ("10:00", "21:00"), None),
    261: ("먹거리촌노상2", ("10:00", "21:00"), ("10:00", "21:00"), None),
    262: ("먹거리촌노상3", ("10:00", "21:00"), ("10:00", "21:00"), None),
    56: ("학원가노상", ("10:00", "20:00"), ("10:00", "20:00"), None),
    23: ("덕천노상", ("09:00", "17:00"), None, None),
    52: ("남부노상", ("10:00", "18:00"), None, None),
    53: ("안양역1노상", ("10:00", "22:00"), ("10:00", "22:00"), ("10:00", "22:00")),
    48: ("안양역2노상", ("10:00", "22:00"), ("10:00", "22:00"), ("10:00", "22:00")),
}

ENTERABLE_STATUSES = frozenset({"always", "fee_hours_only", "unknown"})
ENTERABLE_DEFAULT = ("unknown", "조사 대상 아님(고정 feed)")
# 실제 입출차 가능 여부. access_rule("API 생존 시간")로 추정하지 않고 reports/태영.txt
# 현장 조사표만 원본으로 쓴다. (parking_id: (DB 주차장명, enterable_status, note))
ENTERABLE = {
    10: ("인덕원환승", "unknown", "조사표에 없음"),
    16: ("평촌지하", "unknown", "조사표에 없음"),
    108: ("석수역좌우측노상", "unknown", "조사표에 없음"),
    7: ("안양2동노외", "fee_hours_only", "매일 10:00-22:00만(확정)"),
    112: ("원스퀘어임시", "always", "출입통제 없음"),
    31: ("스마트스퀘어지하", "always", "출입통제 없음"),
    12: ("관악역1환승", "always", "24시간 운영"),
    13: ("관악역2환승", "always", "출입통제 없음"),
    24: ("관악역3환승", "always", "출입통제 없음"),
    25: ("관악역4환승", "always", "출입통제 없음"),
    106: ("시청앞노상", "always", "출입통제 없음"),
    81: ("비호교1,2노외", "unknown", "조사표에 없음"),
    30: ("샘모루초교지하", "always", "출입통제 없음"),
    401: ("호원어린이공원노외", "unknown", "보류/미확인(조사표 명시)"),
    37: ("관양동노외", "always", "주말·그외 무료개방"),
    21: ("해동놀이터지하", "always", "출입통제 없음"),
    39: ("안양7동노외", "always", "출입통제 없음"),
    183: ("호계시장2노외", "always", "출입통제 없음"),
    201: ("비산소공원지하", "always", "출입통제 없음"),
    27: ("인덕원동노외", "always", "출입통제 없음"),
    82: ("명학동노외", "always", "출입통제 없음"),
    461: ("안양6동3노외", "always", "공식 운영시간 미표기 — label_valid 별도 확인 필요"),
    182: ("호계시장1노외", "always", ""),
    40: ("안양6동1노외", "always", ""),
    42: ("느루소공원지하", "always", ""),
    521: ("안양6동4노외", "always", "공식 운영시간 미표기"),
    34: ("동편마을지하", "always", ""),
    522: ("안양6동5노외(일반)", "always", "공식 운영시간 미표기"),
    181: ("덕현공원지하", "always", ""),
    29: ("남부노외", "always", ""),
    9: ("예술공원노외", "always", ""),
    14: ("호계3동노외", "fee_hours_only", "확정(기존 access_rule과 일치)"),
    11: ("일번가노외", "always", ""),
    17: ("화창초교지하", "always", ""),
    36: ("충훈동지하", "always", ""),
    8: ("안양4동노외", "always", ""),
    38: ("삼덕공원지하", "always", ""),
    421: ("박달고가밑노상(노외)", "always", ""),
    28: ("삼덕노외", "always", ""),
    26: ("수목원입구노외", "always", ""),
    18: ("개나리놀이터지하", "always", "24시간 운영"),
    301: ("친목마을노외", "unknown", "24시간이지만 월정전용 — 일반 이용객 불가"),
    22: ("박달시장1노상노외", "fee_hours_only",
         "확정(기존 access_rule과 일치). 초반 'ID 5 박달시장노외' 항목과 혼동 가능, 재확인 필요"),
    122: ("호현마을2노외", "always", "24시간 운영. 재확인 권장(과거 판단 보류 이력 있음)"),
    121: ("호현마을1노외", "always", ""),
    33: ("박달시장2노외", "always", ""),
    441: ("율목복지관옆공영", "always", "공식 시간 매칭 애매 — label_valid 별도 확인 필요"),
    20: ("병목안시민공원노외", "always", ""),
    161: ("관악노상1-2", "always", ""),
    241: ("먹거리촌노상1", "always", ""),
    165: ("관악노상2-1", "always", ""),
    166: ("관악노상2-2", "always", ""),
    162: ("관악노상1-3", "always", ""),
    56: ("학원가노상", "always", ""),
    261: ("먹거리촌노상2", "always", ""),
    163: ("관악노상1-4", "always", ""),
    167: ("관악노상2-3", "always", ""),
    262: ("먹거리촌노상3", "always", ""),
    168: ("관악노상2-4", "always", ""),
    164: ("관악노상1-5", "always", ""),
    169: ("관악노상2-5", "always", ""),
    23: ("덕천노상", "always", ""),
    170: ("관악노상2-6", "always", ""),
    172: ("인덕원동복개노상123", "always", ""),
    171: ("관악노상2-7", "always", ""),
    52: ("남부노상", "always", ""),
    53: ("안양역1노상", "always", ""),
    48: ("안양역2노상", "always", ""),
}


def _name(value):
    return "".join(str(value).split())


def validate_rule_ids(rules, lots):
    """다른 주차장에 조사 결과가 붙으면 자동 수정 대신 실패한다."""
    required = {"parking_id", "name", "access_rule"}
    missing = required - set(rules.columns)
    if missing:
        raise ValueError(f"출입 규칙 필수 컬럼 누락: {sorted(missing)}")
    ids = pd.to_numeric(rules["parking_id"], errors="coerce")
    if ids.isna().any() or (ids % 1 != 0).any() or ids.duplicated().any():
        raise ValueError("parking_id는 비어 있지 않은 고유 정수여야 합니다.")
    result = rules.copy()
    result["parking_id"] = ids.astype(int)
    known = lots.set_index("parking_id")["name"].to_dict()
    errors = []
    for row in result[["parking_id", "name"]].to_dict("records"):
        pid, name = row["parking_id"], row["name"]
        if pid not in known:
            errors.append(f"ID {pid}: DB에 없음")
        elif pd.isna(name) or _name(name) != _name(known[pid]):
            errors.append(f"ID {pid}: CSV={name!r}, DB={known[pid]!r}")
    if errors:
        raise ValueError("ID/주차장명 불일치 — CSV 저장 중단: " + "; ".join(errors))
    return result


def apply_schedules(rules):
    """확인된 SAME_AS_FEE_HOURS 행만 갱신하고 원본 DataFrame은 보존한다."""
    result = rules.copy()
    for day in DAYS:
        for suffix in ("start", "end", "status"):
            column = f"{day}_access_{suffix}"
            if column not in result:
                result[column] = pd.Series(pd.NA, index=result.index, dtype="string")
            else:
                result[column] = result[column].astype("string")
    for pid, (expected, *windows) in SCHEDULES.items():
        mask = result["parking_id"].eq(pid)
        if not mask.any():
            continue
        if not result.loc[mask, "name"].map(_name).eq(_name(expected)).all():
            raise ValueError(f"조사 시간표 ID {pid}의 이름은 {expected!r}이어야 합니다.")
        # 별도 조사로 무료 개방/24시간/미확인으로 분류한 행은 덮어쓰지 않는다.
        mask &= result["access_rule"].eq("SAME_AS_FEE_HOURS")
        for day, window in zip(DAYS, windows):
            result.loc[mask, f"{day}_access_status"] = "closed" if window is None else "open"
            start, end = (pd.NA, pd.NA) if window is None else window
            result.loc[mask, f"{day}_access_start"] = start
            result.loc[mask, f"{day}_access_end"] = end
    return result


def apply_enterable(rules):
    """실제 입출차 가능 여부. access_rule(API 생존 시간)과는 다른 개념이라 여기서 추정하지
    않고 reports/태영.txt 현장 조사표 값만 그대로 옮긴다. 조사표에 없는 곳(고정 feed 포함)은
    unknown으로 남긴다."""
    result = rules.copy()
    result["enterable_status"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["enterable_note"] = pd.Series(pd.NA, index=result.index, dtype="string")
    for pid, (expected_name, status, note) in ENTERABLE.items():
        if status not in ENTERABLE_STATUSES:
            raise ValueError(f"ID {pid}: enterable_status={status!r}는 허용값이 아닙니다.")
        mask = result["parking_id"].eq(pid)
        if not mask.any():
            continue
        if not result.loc[mask, "name"].map(_name).eq(_name(expected_name)).all():
            raise ValueError(f"조사표 ID {pid}의 이름은 {expected_name!r}이어야 합니다.")
        result.loc[mask, "enterable_status"] = status
        result.loc[mask, "enterable_note"] = note
    unset = result["enterable_status"].isna()
    result.loc[unset, "enterable_status"] = ENTERABLE_DEFAULT[0]
    result.loc[unset, "enterable_note"] = ENTERABLE_DEFAULT[1]
    return result


def main():
    path = PARKING_ACCESS_RULES_CSV
    rules = pd.read_csv(path)
    with sqlite3.connect(f"file:{PARKING_DB}?mode=ro", uri=True) as db:
        lots = pd.read_sql_query("SELECT parking_id,name FROM lots", db)
    result = apply_enterable(apply_schedules(validate_rule_ids(rules, lots)))
    backup = path.with_suffix(".csv.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    temporary = path.with_suffix(".csv.tmp")
    try:
        result.to_csv(temporary, index=False, encoding="utf-8-sig")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    columns = ["parking_id", "name"] + [
        f"{day}_access_{suffix}" for day in DAYS for suffix in ("start", "end", "status")
    ]
    print(result.loc[result["parking_id"].isin(SCHEDULES), columns].to_string(index=False))
    enterable_cols = ["parking_id", "name", "enterable_status", "enterable_note"]
    print()
    print(result[enterable_cols].to_string(index=False))
    print(f"enterable_status 분포:\n{result['enterable_status'].value_counts().to_string()}")
    print(f"저장: {path}\n원본 보관: {backup}")


if __name__ == "__main__":
    main()
