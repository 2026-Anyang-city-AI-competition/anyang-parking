import os
import time
import requests
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

INPUT = ROOT / "data" / "processed" / "apt_flat.csv"
OUTPUT = ROOT / "data" / "processed" / "apt_geocoded.csv"

API_KEY = os.getenv("KAKAO_REST_API_KEY")

if not API_KEY:
    raise RuntimeError("KAKAO_REST_API_KEY 환경변수가 없습니다.")

URL = "https://dapi.kakao.com/v2/local/search/address.json"

headers = {
    "Authorization": f"KakaoAK {API_KEY}"
}

df = pd.read_csv(INPUT, encoding="utf-8-sig")

results = []

for i, row in df.iterrows():

    address = str(row.get("address", "")).strip()

    lat = None
    lon = None
    status = "fail"

    if address and address.lower() != "nan":

        try:
            r = requests.get(
                URL,
                headers=headers,
                params={"query": address},
                timeout=10
            )

            if r.status_code == 200:

                data = r.json()

                if data.get("documents"):

                    doc = data["documents"][0]

                    lon = float(doc["x"])
                    lat = float(doc["y"])

                    status = "ok"

                else:
                    print("NO RESULT:", address)

            else:
                print(
                    "HTTP ERROR:",
                    r.status_code,
                    r.text
                )

        except Exception as e:

            status = f"error:{type(e).__name__}"

            print(
                "EXCEPTION:",
                e
            )

    new_row = row.to_dict()

    new_row["latitude"] = lat
    new_row["longitude"] = lon
    new_row["geocode_status"] = status

    results.append(new_row)

    print(
        f"[{i + 1}/{len(df)}] "
        f"{row.get('kaptName')} "
        f"-> {status}"
    )

    time.sleep(0.05)


out = pd.DataFrame(results)

OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True
)

out.to_csv(
    OUTPUT,
    index=False,
    encoding="utf-8-sig"
)

print()
print("총 공동주택:", len(out))
print(
    "좌표 성공:",
    (out["geocode_status"] == "ok").sum()
)
print(
    "실패:",
    (out["geocode_status"] != "ok").sum()
)
print("저장:", OUTPUT)