import os
import csv
import json
import time
import requests
from pathlib import Path

API_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInRadius"

SERVICE_KEY = os.getenv("DATA_GO_KR_SERVICE_KEY")

PARKING_CSV = Path("data/raw/std_parking.csv")
OUT_JSONL = Path("data/raw/shops_by_parking.jsonl")

RADIUS = 500
NUM_ROWS = 1000


def fetch_page(cx, cy, page_no):
    params = {
        "ServiceKey": SERVICE_KEY,
        "pageNo": page_no,
        "numOfRows": NUM_ROWS,
        "radius": RADIUS,
        "cx": cx,
        "cy": cy,
        "type": "json",
    }

    r = requests.get(API_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    if not SERVICE_KEY:
        raise RuntimeError("DATA_GO_KR_SERVICE_KEY 환경변수가 없습니다.")

    with open(PARKING_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)  # 헤더 한 줄 건너뛰기
        rows = list(reader)

    print(f"주차장 행 수: {len(rows)}")

    with open(OUT_JSONL, "w", encoding="utf-8") as out:
        for i, row in enumerate(rows, start=1):
            try:
                parking_id = row[0]
                parking_name = row[1]

                lat = float(row[28])
                lon = float(row[29])

                print(f"[{i}/{len(rows)}] {parking_name}")

                first = fetch_page(lon, lat, 1)

                body = first.get("body", {})
                total_count = int(body.get("totalCount", 0))
                items = body.get("items", [])

                if isinstance(items, dict):
                    items = [items]

                all_items = list(items)

                total_pages = (total_count + NUM_ROWS - 1) // NUM_ROWS

                for page_no in range(2, total_pages + 1):
                    data = fetch_page(lon, lat, page_no)
                    page_items = data.get("body", {}).get("items", [])

                    if isinstance(page_items, dict):
                        page_items = [page_items]

                    all_items.extend(page_items)
                    time.sleep(0.05)

                result = {
                    "parking_id": parking_id,
                    "parking_name": parking_name,
                    "lat": lat,
                    "lon": lon,
                    "radius": RADIUS,
                    "total_count": total_count,
                    "shops": all_items,
                }

                out.write(json.dumps(result, ensure_ascii=False) + "\n")

                print(f"  → {len(all_items)}개 업소")

            except Exception as e:
                print(f"ERROR: {e}")

            time.sleep(0.1)

    print()
    print("완료")
    print(f"저장 위치: {OUT_JSONL}")


if __name__ == "__main__":
    main()