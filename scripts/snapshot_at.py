#!/usr/bin/env python3
"""
지정 시각의 관측을 두 DB 에서 뽑아 실측값과 나란히 놓는다.

  python3 scripts/snapshot_at.py "2026-09-05 14:30"
  python3 scripts/snapshot_at.py "2026-09-05 14:30" --window 5 --ids 46,16,48
  python3 scripts/snapshot_at.py --survey data/raw/survey_template.csv   # 실측 시트와 자동 대조

★ 점유율 규약 (2026-09-04 실측 확정)
    도시공사 parking.db : occ = park_count   / cell_cnt
    GITS     gits.db    : occ = avail_cnt    / cell_cnt
      GITS 필드명이 avblPklotCnt(available)지만 값은 '주차 대수'다.
      정본 v9 §5-④ 의 (총-잔여)/총 은 틀렸다 — tests/test_occupancy_sign.py 참조
"""
import argparse, sqlite3, sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUC  = ROOT / "data/raw/parking.db"
GITS = ROOT / "data/raw/gits.db"
TAB  = ROOT / "reports/tables"; TAB.mkdir(parents=True, exist_ok=True)

def parse_t(s):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try: return datetime.strptime(s, f)
        except ValueError: pass
    sys.exit(f"시각을 못 읽었다: {s!r}  예: \"2026-09-05 14:30\"")

def near(rows, target, window_min):
    """(ts_str, ...) 목록에서 target 에 가장 가까운 행. window 밖이면 None."""
    best, bd = None, None
    for r in rows:
        try: t = datetime.fromisoformat(r[0]).replace(tzinfo=None)
        except ValueError: continue
        d = abs((t - target).total_seconds())
        if bd is None or d < bd: best, bd = r, d
    if best is None or bd > window_min * 60: return None, None
    return best, bd

def auc_rows(ids, target, w):
    if not AUC.exists(): return {}
    c = sqlite3.connect(AUC)
    lo = (target - timedelta(minutes=w)).isoformat()
    hi = (target + timedelta(minutes=w)).isoformat()
    out = {}
    for pid in ids:
        rows = c.execute("""SELECT o.ts_kst, o.park_count, o.cell_cnt, l.name
                            FROM obs o JOIN lots l USING(parking_id)
                            WHERE o.parking_id=? AND o.ts_kst BETWEEN ? AND ?""",
                         (pid, lo, hi)).fetchall()
        r, d = near(rows, target, w)
        if r: out[pid] = {"ts": r[0], "cars": r[1], "cells": r[2], "name": r[3], "dt": d}
    return out

def gits_rows(names, target, w):
    if not GITS.exists(): return {}
    import re
    c = sqlite3.connect(GITS)
    lo = (target - timedelta(minutes=w)).isoformat()
    hi = (target + timedelta(minutes=w)).isoformat()
    norm = lambda x: re.sub(r"\s", "", x or "")
    rows = c.execute("""SELECT o.ts_kst, o.avail_cnt, o.cell_cnt, l.pkplc_nm
                        FROM gits_obs o JOIN gits_lots l
                          ON o.lae_id=l.lae_id AND o.pkplc_id=l.pkplc_id
                        WHERE l.lae_nm='안양시' AND o.ts_kst BETWEEN ? AND ?""",
                     (lo, hi)).fetchall()
    by = {}
    for ts, av, cc, nm in rows: by.setdefault(norm(nm), []).append((ts, av, cc, nm))
    out = {}
    for want in names:
        k = norm(want)
        if k not in by: continue
        r, d = near(by[k], target, w)
        if r: out[want] = {"ts": r[0], "cars": r[1], "cells": r[2], "dt": d}
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("time", nargs="?", help='예: "2026-09-05 14:30"')
    ap.add_argument("--window", type=int, default=5, help="±분 (기본 5)")
    ap.add_argument("--ids", help="parking_id 쉼표구분")
    ap.add_argument("--survey", help="실측 시트 CSV — 시각·실측값을 읽어 자동 대조")
    a = ap.parse_args()

    survey = []
    if a.survey:
        import csv
        with open(a.survey, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if not (row.get("시각") or "").strip(): continue
                survey.append(row)
        if not survey: sys.exit("실측 시트에 '시각'이 채워진 행이 없다.")
    elif not a.time:
        sys.exit('시각이나 --survey 중 하나는 필요하다. 예: snapshot_at.py "2026-09-05 14:30"')

    def one(target, ids, names, obs_map):
        au = auc_rows(ids, target, a.window)
        gi = gits_rows(names or [v["name"] for v in au.values()], target, a.window)
        print(f"\n### {target:%Y-%m-%d %H:%M} (±{a.window}분)")
        print()
        print("| 주차장 | id | 면수 | 포털 관측 | 포털 점유 | GITS 관측 | GITS 점유 | 실측 대수 | 차이 |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for pid in ids:
            v = au.get(pid)
            if not v:
                print(f"| (id {pid}) | {pid} | — | 관측 없음 | | | | | |"); continue
            nm = v["name"]; g = gi.get(nm)
            po = v["cars"] / v["cells"] if v["cells"] else float("nan")
            gs = f"{g['cars']}대" if g else "—"
            go = f"{g['cars']/g['cells']:.0%}" if g and g["cells"] else "—"
            real = obs_map.get(pid)
            diff = f"{v['cars'] - int(real):+d}" if real not in (None, "") else "—"
            print(f"| {nm} | {pid} | {v['cells']} | {v['cars']}대 ({v['ts'][11:16]}) | "
                  f"{po:.0%} | {gs} | {go} | {real or '—'} | {diff} |")

    if survey:
        from collections import defaultdict
        byt = defaultdict(dict)
        for r in survey:
            t = parse_t(f"{r['날짜']} {r['시각']}")
            byt[t][int(r["parking_id"])] = (r.get("실제_주차대수") or "").strip()
        for t in sorted(byt):
            one(t, list(byt[t]), None, byt[t])
    else:
        target = parse_t(a.time)
        ids = [int(x) for x in a.ids.split(",")] if a.ids else \
              [int(x) for x in sqlite3.connect(AUC).execute(
                  "SELECT DISTINCT parking_id FROM obs LIMIT 5").fetchall() and
               [r[0] for r in sqlite3.connect(AUC).execute(
                   "SELECT DISTINCT parking_id FROM obs ORDER BY parking_id LIMIT 5")]]
        one(target, ids, None, {})

if __name__ == "__main__":
    main()
