#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.serve.recommend import recommend
from src.serve.predictor import Predictor
from src.serve.access_rules import AccessRulesRepository
from src.serve.prediction_gate import PredictionGate
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

CASES = [
    ("안양시청", (37.394259, 126.956861), datetime(2026, 9, 7, 14, 0, tzinfo=KST), 120, "anyang_city_hall_weekday"),
    ("석수역",   (37.435093, 126.902321), datetime(2026, 9, 12, 11, 0, tzinfo=KST), 60,  "seoksu_station_saturday"),
    ("범계역",   (37.389784, 126.950783), datetime(2026, 9, 13, 15, 0, tzinfo=KST), 180, "beomgye_station_sunday"),
]

predictor = Predictor()
# API 경로(src/serve/api.py)는 요청마다 이걸 호출해서 최근 lag/rolling을 최신 DB로 갱신한다.
# 이 스크립트는 recommend()를 직접 부르므로 그 경로를 안 타 — 안 하면 pickle에 박힌
# 옛 이력으로만 예측해서 매번 no_fresh_history가 난다.
status = predictor.refresh_from_db(force=True)
print(f"predictor refresh: {status}")
# 서비스와 같은 경로로 만든다. 출입 규칙·예측 게이트를 빼면 fixture 가 실제 응답과 달라진다.
access_rules = AccessRulesRepository()
access_rules.refresh(force=True)
prediction_gate = PredictionGate()
prediction_gate.refresh(force=True)

for name, dest, when, mins, fname in CASES:
    result = recommend(dest, mins, start=(37.4018, 126.9226), predictor=predictor,
                       access_rules=access_rules, prediction_gate=prediction_gate)
    # Predictor가 검증한 구간만 같은 스키마로 export한다. 모든 배열을 검사한다.
    for key in ("cards", "by_walk", "by_fare", "unavailable"):
        for card in result.get(key, []):
            if card.get("interval_status") != "pass":
                assert card.get("pred_p10") is None and card.get("pred_p90") is None
    result["model_version"] = predictor.model_version if predictor.ok else None
    result["generated_at"] = datetime.now(KST).isoformat()
    result["scenario_note"] = "실행 시점 추천 결과. 파일명의 요일은 시나리오 구분이며 과거/미래 재현 결과가 아님."
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
