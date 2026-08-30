#!/usr/bin/env python3
"""여러 곳에서 모은 폴링 DB를 하나로 병합. 중복은 PK로 자동 무시.
   python3 src/utils/merge_db.py data/raw/parking.db data/raw/parking_vm.db [...]"""
import os, shutil, sqlite3, sys

if len(sys.argv) < 3:
    sys.exit("사용법: merge_db.py <대상DB> <소스DB> [소스DB ...]")
main, others = sys.argv[1], list(sys.argv[2:])

if not os.path.exists(main):
    shutil.copy(others[0], main)
    print(f"{main} 이 없어서 {others[0]} 를 복사해 생성")
    others = others[1:]

c = sqlite3.connect(main)
for i, o in enumerate(others):
    if not os.path.exists(o):
        print(f"건너뜀(없음): {o}"); continue
    c.execute(f"ATTACH DATABASE ? AS s{i}", (o,))
    before = c.execute("SELECT COUNT(*) FROM obs").fetchone()[0]
    c.execute(f"INSERT OR IGNORE INTO lots SELECT * FROM s{i}.lots")
    c.execute(f"INSERT OR IGNORE INTO obs  SELECT * FROM s{i}.obs")
    c.commit()
    after = c.execute("SELECT COUNT(*) FROM obs").fetchone()[0]
    print(f"{o}: +{after - before:,}행")
    c.execute(f"DETACH s{i}")

n, lo, hi = c.execute("SELECT COUNT(*), MIN(ts_kst), MAX(ts_kst) FROM obs").fetchone()
print(f"\n총 {n:,}행 / {lo} ~ {hi}")
