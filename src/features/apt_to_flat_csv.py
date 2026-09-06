import ast
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

INPUT = ROOT / "data" / "raw" / "gg_apt.csv"
OUTPUT = ROOT / "data" / "processed" / "apt_flat.csv"

df = pd.read_csv(INPUT, encoding="utf-8-sig")

rows = []

def get_item(text):
    try:
        obj = ast.literal_eval(text)
        return (
            obj.get("response", {})
               .get("body", {})
               .get("item", {})
        )
    except Exception:
        return {}

for _, row in df.iterrows():
    basic = get_item(row["basic"])
    detail = get_item(row["detail"])

    ground_parking = pd.to_numeric(
        detail.get("kaptdPcnt"),
        errors="coerce"
    )

    underground_parking = pd.to_numeric(
        detail.get("kaptdPcntu"),
        errors="coerce"
    )

    households = pd.to_numeric(
        basic.get("hoCnt"),
        errors="coerce"
    )

    if pd.isna(households) or households == 0:
        households = pd.to_numeric(
            basic.get("kaptdaCnt"),
            errors="coerce"
        )

    rows.append({
        "kaptCode": row["kaptCode"],
        "kaptName": basic.get("kaptName", row["kaptName"]),
        "region1": row["region1"],
        "region2": row["region2"],
        "region3": row["region3"],

        "address": basic.get("doroJuso"),
        "jibun_address": basic.get("kaptAddr"),

        "apt_type": basic.get("codeAptNm"),

        "households": households,
        "dong_count": pd.to_numeric(
            basic.get("kaptDongCnt"),
            errors="coerce"
        ),

        "ground_parking": ground_parking,
        "underground_parking": underground_parking,

        "parking_total": (
            (0 if pd.isna(ground_parking) else ground_parking)
            +
            (0 if pd.isna(underground_parking) else underground_parking)
        ),

        "used_date": basic.get("kaptUsedate"),
    })

out = pd.DataFrame(rows)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)

out.to_csv(
    OUTPUT,
    index=False,
    encoding="utf-8-sig"
)

print("공동주택 수:", len(out))
print()
print(out.head(10).to_string())
print()
print("세대수 존재:", out["households"].notna().sum())
print("주소 존재:", out["address"].notna().sum())
print("주차대수 존재:", (out["parking_total"] > 0).sum())
print()
print("저장:", OUTPUT)