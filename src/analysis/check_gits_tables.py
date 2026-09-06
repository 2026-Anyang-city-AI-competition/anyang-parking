import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data/raw/gits.db"

con = sqlite3.connect(DB)

tables = con.execute(
    "SELECT name FROM sqlite_master WHERE type='table';"
).fetchall()

print("DB:", DB)
print("\n테이블 목록:")

for table in tables:
    print("-", table[0])

print("\n컬럼 정보:")

for table in tables:
    table_name = table[0]

    print("\n" + "=" * 60)
    print("TABLE:", table_name)
    print("=" * 60)

    columns = con.execute(
        f"PRAGMA table_info('{table_name}')"
    ).fetchall()

    for col in columns:
        print(col)

con.close()