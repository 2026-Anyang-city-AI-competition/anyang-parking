import json
import csv
from pathlib import Path

INPUT = Path("data/raw/gg_apt_detail.jsonl")
OUTPUT = Path("data/processed/gg_apt.csv")

OUTPUT.parent.mkdir(parents=True, exist_ok=True)


def get_item(response):
    try:
        return response["response"]["body"]["item"]
    except Exception:
        return {}


rows = []

with open(INPUT, "r", encoding="utf-8") as f:
    for line in f:
        data = json.loads(line)

        basic = get_item(data.get("basic", {}))
        detail = get_item(data.get("detail", {}))

        row = {
            "kaptCode": data.get("kaptCode"),
            "kaptName": data.get("kaptName"),
            "region1": data.get("region1"),
            "region2": data.get("region2"),
            "region3": data.get("region3"),

            # 기본정보
            "address": basic.get("kaptAddr"),
            "road_address": basic.get("doroJuso"),
            "households": basic.get("kaptdaCnt"),
            "building_count": basic.get("kaptDongCnt"),

            # 상세정보
            "parking_ground": detail.get("kaptdPcnt"),
            "parking_underground": detail.get("kaptdPcntu"),
            "busstop_distance": detail.get("busStopDistance"),
            "subway_line": detail.get("subwayLine"),
            "subway_station": detail.get("subwayStation"),
            "subway_distance": detail.get("subwayDistance"),
            "convenience_facilities": detail.get("convenientFacility"),
            "education_facilities": detail.get("educationFacility"),
        }

        rows.append(row)

fieldnames = rows[0].keys()

with open(OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"총 {len(rows)}개 단지 변환 완료")
print(f"저장 위치: {OUTPUT}")