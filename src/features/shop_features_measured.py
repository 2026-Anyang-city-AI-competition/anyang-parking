import json
import csv
from collections import Counter
from pathlib import Path

INPUT = Path("data/raw/shops_by_measured_parking.jsonl")

OUTPUT = Path("data/processed/shop_features_measured.csv")
CATEGORY_OUTPUT = Path("data/processed/shop_category_measured.csv")


def clean_name(text):
    if not text:
        return "UNKNOWN"

    return (
        str(text)
        .strip()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("(", "")
        .replace(")", "")
    )


rows = []

# 전체 데이터에서 어떤 업종들이 존재하는지 확인용
global_large = Counter()
global_middle = Counter()
global_small = Counter()


with open(INPUT, "r", encoding="utf-8") as f:
    for line in f:
        data = json.loads(line)

        parking_id = data.get("parking_id")
        parking_name = data.get("parking_name")
        shops = data.get("shops", [])

        large_counter = Counter()
        middle_counter = Counter()
        small_counter = Counter()

        for shop in shops:
            # 공식 상권업종 분류
            large_code = shop.get("indsLclsCd")
            large_name = shop.get("indsLclsNm")

            middle_code = shop.get("indsMclsCd")
            middle_name = shop.get("indsMclsNm")

            small_code = shop.get("indsSclsCd")
            small_name = shop.get("indsSclsNm")

            if large_code:
                large_counter[(large_code, large_name)] += 1
                global_large[(large_code, large_name)] += 1

            if middle_code:
                middle_counter[(middle_code, middle_name)] += 1
                global_middle[(middle_code, middle_name)] += 1

            if small_code:
                small_counter[(small_code, small_name)] += 1
                global_small[(small_code, small_name)] += 1

        row = {
            "parking_id": parking_id,
            "parking_name": parking_name,
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "radius": data.get("radius"),
            "shops_total_500": len(shops),
        }

        # 대분류별 개수
        for (code, name), count in large_counter.items():
            key = f"L_{code}_{clean_name(name)}"
            row[key] = count

        # 중분류별 개수
        for (code, name), count in middle_counter.items():
            key = f"M_{code}_{clean_name(name)}"
            row[key] = count

        rows.append(row)


# 모든 행에서 등장한 컬럼 합치기
all_fields = set()

for row in rows:
    all_fields.update(row.keys())


fixed_fields = [
    "parking_id",
    "parking_name",
    "lat",
    "lon",
    "radius",
    "shops_total_500",
]

category_fields = sorted(
    field for field in all_fields
    if field not in fixed_fields
)

fieldnames = fixed_fields + category_fields


# 없는 업종은 0으로 채우기
for row in rows:
    for field in category_fields:
        row.setdefault(field, 0)


with open(
    OUTPUT,
    "w",
    newline="",
    encoding="utf-8-sig"
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=fieldnames
    )

    writer.writeheader()
    writer.writerows(rows)


# 업종 코드 목록도 별도 저장
with open(
    CATEGORY_OUTPUT,
    "w",
    newline="",
    encoding="utf-8-sig"
) as f:

    writer = csv.writer(f)

    writer.writerow([
        "level",
        "code",
        "name",
        "total_count"
    ])

    for (code, name), count in sorted(global_large.items()):
        writer.writerow([
            "large",
            code,
            name,
            count
        ])

    for (code, name), count in sorted(global_middle.items()):
        writer.writerow([
            "middle",
            code,
            name,
            count
        ])

    for (code, name), count in sorted(global_small.items()):
        writer.writerow([
            "small",
            code,
            name,
            count
        ])


print(f"주차장 Feature 생성 완료: {len(rows)}곳")
print(f"저장: {OUTPUT}")
print(f"업종 목록: {CATEGORY_OUTPUT}")