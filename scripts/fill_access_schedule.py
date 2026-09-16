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


def main():
    path = PARKING_ACCESS_RULES_CSV
    rules = pd.read_csv(path)
    with sqlite3.connect(f"file:{PARKING_DB}?mode=ro", uri=True) as db:
        lots = pd.read_sql_query("SELECT parking_id,name FROM lots", db)
    result = apply_schedules(validate_rule_ids(rules, lots))
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
    print(f"저장: {path}\n원본 보관: {backup}")


if __name__ == "__main__":
    main()
