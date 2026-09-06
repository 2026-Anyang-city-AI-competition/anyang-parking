import json
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "data" / "raw" / "gg_apt_detail.jsonl"
OUTPUT = ROOT / "data" / "raw" / "gg_apt.csv"

rows = []

with open(INPUT, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue

        obj = json.loads(line)

        # 원본 전체를 일단 그대로 저장
        rows.append(obj)

df = pd.DataFrame(rows)

print("행 수:", len(df))
print("컬럼 수:", len(df.columns))
print("\n컬럼 목록:")
for c in df.columns:
    print("-", c)

df.to_csv(
    OUTPUT,
    index=False,
    encoding="utf-8-sig"
)

print("\n저장 완료:", OUTPUT)