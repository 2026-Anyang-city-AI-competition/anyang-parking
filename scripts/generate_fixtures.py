#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.serve.recommend import recommend
from src.serve.predictor import Predictor
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

CASES = [
    ("안양시청", (37.394259, 126.956861), datetime(2026, 9, 7, 14, 0, tzinfo=KST), 120, "anyang_city_hall_weekday"),
    ("석수역",   (37.435093, 126.902321), datetime(2026, 9, 12, 11, 0, tzinfo=KST), 60,  "seoksu_station_saturday"),
    ("범계역",   (37.389784, 126.950783), datetime(2026, 9, 13, 15, 0, tzinfo=KST), 180, "beomgye_station_sunday"),
]

predictor = Predictor()

for name, dest, when, mins, fname in CASES:
    result = recommend(dest, mins, start=(37.4018, 126.9226), predictor=predictor)
    # Remove pred_p10 and pred_p90 from each card as per U9-4 warning
    if "cards" in result:
        for card in result["cards"]:
            card.pop("pred_p10", None)
            card.pop("pred_p90", None)
    # Convert numpy types to Python native types for JSON serialization
    def convert(obj):
        if isinstance(obj, dict):
            return {k: convert(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert(item) for item in obj]
        elif hasattr(obj, 'item'):  # numpy scalar
            return obj.item()
        elif isinstance(obj, (int, float, str, bool)) or obj is None:
            return obj
        else:
            return str(obj)  # fallback

    result_converted = convert(result)
    out_path = ROOT / "web" / "fixtures" / f"{fname}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result_converted, f, ensure_ascii=False, indent=2)
    print(f"Saved {out_path}")
