#!/usr/bin/env python3
"""
GITS 주요도로 목록 수집

환경변수:
    GITS_SERVICE_KEY

출력:
    data/raw/gits_road_list.xml
    data/processed/gits_road_list.csv

실행:
    python src/collect/fetch_gits_roads.py
"""

import os
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[2]

RAW = ROOT / "data/raw"
PROCESSED = ROOT / "data/processed"

RAW.mkdir(parents=True, exist_ok=True)
PROCESSED.mkdir(parents=True, exist_ok=True)

URL = "https://openapigits.gg.go.kr/api/rest/getRoadInfoList"


def elem_to_dict(elem):
    row = {}
    for child in list(elem):
        row[child.tag] = child.text
    return row


def main():
    service_key = os.getenv("GITS_SERVICE_KEY")

    if not service_key:
        raise RuntimeError(
            "GITS_SERVICE_KEY 환경변수가 없습니다.\n"
            "PowerShell에서 먼저:\n"
            "$env:GITS_SERVICE_KEY='발급받은키'"
        )

    print("GITS 주요도로 목록 요청 중...")

    r = requests.get(
        URL,
        params={
            "serviceKey": service_key
        },
        timeout=30
    )

    print("HTTP:", r.status_code)

    if r.status_code != 200:
        print(r.text[:1000])
        r.raise_for_status()

    raw_path = RAW / "gits_road_list.xml"
    raw_path.write_bytes(r.content)

    print("원본 저장:", raw_path)

    root = ET.fromstring(r.content)

    # 응답 구조가 조금 달라도 item 계열을 최대한 탐색
    items = root.findall(".//item")

    if not items:
        # GITS 응답에서 item이 아닌 반복 노드일 가능성 대비
        candidates = []
        for elem in root.iter():
            children = list(elem)
            if len(children) >= 2:
                tags = {c.tag for c in children}
                if "routeId" in tags:
                    candidates.append(elem)
        items = candidates

    rows = [elem_to_dict(x) for x in items]

    if not rows:
        print("\n파싱된 도로 데이터가 없습니다.")
        print("응답 앞부분:")
        print(r.text[:2000])
        return

    df = pd.DataFrame(rows)

    # 보기 편하게 자주 쓰는 컬럼을 앞으로
    preferred = [
        "routeId",
        "roadName",
        "routeName",
        "roadRank",
        "routeTp",
    ]

    ordered = [
        c for c in preferred if c in df.columns
    ] + [
        c for c in df.columns
        if c not in preferred
    ]

    df = df[ordered]

    csv_path = PROCESSED / "gits_road_list.csv"

    df.to_csv(
        csv_path,
        index=False,
        encoding="utf-8-sig"
    )

    print("CSV 저장:", csv_path)
    print("도로 수:", len(df))
    print("컬럼:", list(df.columns))

    print("\n=== 앞 20개 ===")
    print(
        df.head(20).to_string(index=False)
    )

    # 안양 후보를 이름 컬럼에서 자동 검색
    text_cols = [
        c for c in df.columns
        if df[c].dtype == object
    ]

    mask = pd.Series(False, index=df.index)

    for c in text_cols:
        mask |= (
            df[c]
            .astype(str)
            .str.contains(
                "안양|평촌|석수|과천|군포|의왕|경수대로|흥안대로",
                case=False,
                na=False,
                regex=True
            )
        )

    cand = df[mask].copy()

    if len(cand):
        print("\n=== 안양 주변 후보 ===")
        print(
            cand.to_string(index=False)
        )

        cand_path = (
            PROCESSED
            / "gits_road_candidates_anyang.csv"
        )

        cand.to_csv(
            cand_path,
            index=False,
            encoding="utf-8-sig"
        )

        print("후보 저장:", cand_path)

    else:
        print(
            "\n이름만으로 안양 후보를 찾지 못했습니다."
        )
        print(
            "다음 단계에서 routeId별 구간정보와 좌표를 보고 고르면 됩니다."
        )


if __name__ == "__main__":
    main()
