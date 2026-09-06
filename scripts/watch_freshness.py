#!/usr/bin/env python3
"""
로컬 DB 최신성 감시 — **폴러 감시가 아니라 「로컬이 최신인가」 감시다.**

이틀간 낡은 DB로 A10~A15 를 돌린 사고가 있었다. 그걸 다시는 못 하게 막는다.

  python3 scripts/watch_freshness.py            # 20분 임계
  python3 scripts/watch_freshness.py --max 30
  → 낡았으면 non-zero exit + 경고
"""
import argparse, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KST  = timezone(timedelta(hours=9))
TARGETS = [("parking.db", "obs", "ts_kst"), ("gits.db", "gits_obs", "ts_kst")]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=20, help="허용 지연 분 (기본 20)")
    a = ap.parse_args()
    now, bad = datetime.now(KST), []
    for fn, tbl, col in TARGETS:
        p = ROOT / "data/raw" / fn
        if not p.exists():
            print(f"❌ {fn}: 파일 없음"); bad.append(fn); continue
        try:
            m = sqlite3.connect(f"file:{p}?mode=ro", uri=True).execute(
                f"SELECT MAX({col}) FROM {tbl}").fetchone()[0]
            age = (now - datetime.fromisoformat(m)).total_seconds() / 60
        except Exception as e:
            print(f"❌ {fn}: 읽기 실패 {str(e)[:80]}"); bad.append(fn); continue
        mark = "✅" if age <= a.max else "❌"
        print(f"{mark} {fn:<12} 최신 {m[:19]} · {age:>6.1f}분 전")
        if age > a.max: bad.append(fn)
    if bad:
        print(f"\n⚠️ {a.max}분 넘게 낡음: {', '.join(bad)}")
        print("   → bash scripts/auto_pull.sh   (분석 돌리기 전에 반드시)")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
