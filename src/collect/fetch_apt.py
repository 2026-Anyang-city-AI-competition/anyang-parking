import json
import time
import requests
from pathlib import Path

BASE = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV5"

DATA_DIR = Path("data/raw")

MANAN_FILE = DATA_DIR / "gg_apt_manan.json"
DONGAN_FILE = DATA_DIR / "gg_apt_dongan.json"

OUT_FILE = DATA_DIR / "gg_apt_detail.jsonl"

# 여기에 공공데이터포털 Decoding 인증키 입력
SERVICE_KEY = "HEoJD1YaPmVf/Iag2HB/nnAz0YDCNxkALwUGH4ZKi75u9mLqcagPT7qrcSygxdpPkJmKYZPJV/rbc3et5qxWXg=="


def load_apartments():
    apartments = []

    for file_path in [MANAN_FILE, DONGAN_FILE]:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        items = data["response"]["body"]["items"]

        if isinstance(items, dict):
            items = [items]

        apartments.extend(items)

    return apartments


def fetch_basic_info(kapt_code):
    url = f"{BASE}/getAphusBassInfoV5"

    params = {
        "serviceKey": SERVICE_KEY,
        "kaptCode": kapt_code,
    }

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()

    return r.json()


def fetch_detail_info(kapt_code):
    url = f"{BASE}/getAphusDtlInfoV5"

    params = {
        "serviceKey": SERVICE_KEY,
        "kaptCode": kapt_code,
    }

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()

    return r.json()


def main():
    apartments = load_apartments()

    print(f"총 단지 수: {len(apartments)}")

    with open(OUT_FILE, "w", encoding="utf-8") as out:
        for i, apt in enumerate(apartments, start=1):
            kapt_code = apt["kaptCode"]
            kapt_name = apt["kaptName"]

            print(f"[{i}/{len(apartments)}] {kapt_name} ({kapt_code})")

            try:
                basic = fetch_basic_info(kapt_code)
                detail = fetch_detail_info(kapt_code)

                result = {
                    "kaptCode": kapt_code,
                    "kaptName": kapt_name,
                    "region1": apt.get("as1"),
                    "region2": apt.get("as2"),
                    "region3": apt.get("as3"),
                    "basic": basic,
                    "detail": detail,
                }

                out.write(
                    json.dumps(result, ensure_ascii=False) + "\n"
                )

            except Exception as e:
                print(f"ERROR: {kapt_name} / {e}")

            time.sleep(0.1)

    print()
    print("완료")
    print(f"저장 위치: {OUT_FILE}")


if __name__ == "__main__":
    main()