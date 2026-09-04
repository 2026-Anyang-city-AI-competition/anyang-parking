import os
import json
import time
import sqlite3
import requests
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DB = ROOT / "data/raw/parking.db"
OUT = ROOT / "data/raw/shops_by_measured_parking.jsonl"

API_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInRadius"
SERVICE_KEY = os.getenv("DATA_GO_KR_SERVICE_KEY")

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

    con = sqlite3.connect(DB)

    lots = con.execute("""
        SELECT parking_id, name, lat, lng
        FROM lots
        WHERE lat IS NOT NULL AND lng IS NOT NULL
    """).fetchall()

    con.close()

    print(f"측정 주차장 수: {len(lots)}")

    with open(OUT, "w", encoding="utf-8") as out:
        for i, (parking_id, name, lat, lng) in enumerate(lots, start=1):

            print(f"[{i}/{len(lots)}] {name}")

            try:
                first = fetch_page(lng, lat, 1)

                body = first.get("body", {})
                total_count = int(body.get("totalCount", 0))
                items = body.get("items", [])

                if isinstance(items, dict):
                    items = [items]

                all_items = list(items)

                total_pages = (total_count + NUM_ROWS - 1) // NUM_ROWS

                for page_no in range(2, total_pages + 1):
                    data = fetch_page(lng, lat, page_no)
                    page_items = data.get("body", {}).get("items", [])

                    if isinstance(page_items, dict):
                        page_items = [page_items]

                    all_items.extend(page_items)
                    time.sleep(0.05)

                result = {
                    "parking_id": parking_id,
                    "parking_name": name,
                    "lat": lat,
                    "lon": lng,
                    "radius": RADIUS,
                    "total_count": total_count,
                    "shops": all_items,
                }

                out.write(json.dumps(result, ensure_ascii=False) + "\n")

                print(f"  → {len(all_items)}개 업소")

            except Exception as e:
                print(f"ERROR: {name} / {e}")

            time.sleep(0.1)

    print(f"\n완료: {OUT}")


if __name__ == "__main__":
    main()