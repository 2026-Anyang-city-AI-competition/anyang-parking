#!/usr/bin/env python3
"""여러 곳에서 모은 폴링 DB를 하나로 병합. 중복은 PK로 자동 무시.
   python3 src/utils/merge_db.py data/raw/parking.db data/raw/parking_vm.db [...]"""
import os, shutil, sqlite3, sys

argv = sys.argv[1:]
TABLES = ["lots", "obs"]
if argv and argv[0] == "--tables":          # 예: --tables gits_lots,gits_obs
    TABLES = argv[1].split(","); argv = argv[2:]
if len(argv) < 2:
    sys.exit("사용법: merge_db.py [--tables t1,t2] <대상DB> <소스DB> [소스DB ...]")
main, others = argv[0], list(argv[1:])

if not os.path.exists(main):
    shutil.copy(others[0], main)
    print(f"{main} 이 없어서 {others[0]} 를 복사해 생성")
    others = others[1:]

c = sqlite3.connect(main)
for i, o in enumerate(others):
    if not os.path.exists(o):
        print(f"건너뜀(없음): {o}"); continue
    c.execute(f"ATTACH DATABASE ? AS s{i}", (o,))
    before = c.execute(f"SELECT COUNT(*) FROM {TABLES[-1]}").fetchone()[0]
    for t in TABLES:
        c.execute(f"INSERT OR IGNORE INTO {t} SELECT * FROM s{i}.{t}")
    c.commit()
    after = c.execute(f"SELECT COUNT(*) FROM {TABLES[-1]}").fetchone()[0]
    print(f"{o}: +{after - before:,}행")
    c.execute(f"DETACH s{i}")

n, lo, hi = c.execute(f"SELECT COUNT(*), MIN(ts_kst), MAX(ts_kst) FROM {TABLES[-1]}").fetchone()
print(f"\n총 {n:,}행 / {lo} ~ {hi}")
