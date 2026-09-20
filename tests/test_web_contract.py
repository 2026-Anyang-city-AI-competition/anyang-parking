"""웹이 읽는 필드가 응답에서 사라지지 않게 고정한다.

`web/src/api/types.ts` 와 짝이다. 서버에서 키 이름을 바꾸면 여기서 먼저 깨진다.
프런트는 타입스크립트라 런타임에 검증하지 않으므로, 계약은 이쪽에서 지킨다.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from src.serve.recommend import recommend

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "web/fixtures"

# App.tsx / present.ts 가 실제로 읽는 키들.
TOP_LEVEL = {"cards", "by_walk", "by_fare", "live_unavailable", "unavailable", "excluded",
             "candidate_count", "radius_used", "exhausted", "message",
             "depart_at", "depart_at_iso", "park_minutes"}
CARD = {"parking_id", "name", "lat", "lng", "grade", "cell_cnt",
        "access", "access_status", "expected_departure_at",
        "drive_min", "walk_min", "total_min", "arrive_at", "estimated",
        "fare", "fare_payg", "fare_daily_pass", "fee_source",
        "prediction_status", "prediction_reason", "avail_now", "avail_pred",
        "full_prob", "cell_cnt", "is_live", "weekday_hours", "weekend_hours", "benefit"}
RANKED = CARD | {"rank", "rank_without_demotion", "demoted", "demotion_reason"}
ROUTE_STATE = {"drive_estimated", "walk_estimated", "route_source", "walk_source"}
FARE = {"total", "reason", "breakdown", "billable_min", "free_minutes",
        "free_minutes_outside_fee_window", "total_prepaid", "fee_source"}

LOTS = [
    {"parking_id": 1, "name": "가까운노상", "lat": 37.4, "lng": 126.9, "grade": 1,
     "div": "노상", "cell_cnt": 20, "avail_now": 5, "straight_m": 100,
     "wdays_start": "00:00", "wdays_end": "24:00", "wend_start": "00:00", "wend_end": "24:00"},
    {"parking_id": 2, "name": "먼노상", "lat": 37.41, "lng": 126.91, "grade": 1,
     "div": "노상", "cell_cnt": 20, "avail_now": 1, "straight_m": 200,
     "wdays_start": "00:00", "wdays_end": "24:00", "wend_start": "00:00", "wend_end": "24:00"},
]


class _Predictor:
    def predict(self, pid, arrive, horizon):
        return {"p10": 10, "p50": 20, "p90": 30, "full_prob": .1,
                "interval_status": "pass", "source": "ml", "model_horizon_min": 15}


class WebContractTests(unittest.TestCase):
    def response(self):
        cand = {"lots": LOTS, "dead_feeds": [], "radius_used": 1000,
                "exhausted": False, "message": None, "unlabeled": []}
        eta = {x["parking_id"]: {"duration": 60, "distance": 100, "estimated": False}
               for x in LOTS}
        with patch("src.serve.recommend.find_candidates", return_value=cand), \
             patch("src.serve.recommend.routing.multi_eta", return_value=eta), \
             patch("src.serve.recommend.walking.walk_times", return_value=eta):
            return recommend((37.4, 126.9), 120, predictor=_Predictor(),
                             with_alternatives=False)

    def test_response_carries_every_field_the_web_reads(self):
        body = self.response()
        self.assertLessEqual(TOP_LEVEL, set(body))
        for card in body["cards"]:
            self.assertLessEqual(CARD, set(card), card["name"])
            self.assertLessEqual(ROUTE_STATE, set(card), card["name"])
            self.assertLessEqual(FARE, set(card["fare"]), card["name"])
        for axis in ("by_walk", "by_fare"):
            for card in body[axis]:
                self.assertLessEqual(RANKED, set(card), f"{axis}:{card['name']}")

    def test_ranks_are_dense_and_per_axis(self):
        body = self.response()
        for axis in ("by_walk", "by_fare"):
            ranks = [c["rank"] for c in body[axis]]
            self.assertEqual(ranks, list(range(1, len(ranks) + 1)), axis)
        # 축별 사본이라 원본 카드는 순위를 갖지 않는다.
        for card in body["cards"]:
            self.assertNotIn("rank", card)

    def test_benefit_codes_produce_per_option_results(self):
        cand = {"lots": LOTS, "dead_feeds": [], "radius_used": 1000,
                "exhausted": False, "message": None, "unlabeled": []}
        eta = {x["parking_id"]: {"duration": 60, "distance": 100, "estimated": False}
               for x in LOTS}
        with patch("src.serve.recommend.find_candidates", return_value=cand), \
             patch("src.serve.recommend.routing.multi_eta", return_value=eta), \
             patch("src.serve.recommend.walking.walk_times", return_value=eta):
            body = recommend((37.4, 126.9), 120, predictor=_Predictor(),
                             with_alternatives=False,
                             benefit_codes=["경형자동차", "다자녀"])
        for card in body["cards"]:
            benefit = card["benefit"]
            self.assertIsNotNone(benefit)
            self.assertEqual({o["code"] for o in benefit["options"]}, {"경형자동차", "다자녀"})
            self.assertEqual(sum(o["applied"] for o in benefit["options"]), 1)
            for option in benefit["options"]:
                if not option["applied"]:
                    self.assertTrue(option["rejected_reason"])

    def test_no_benefit_codes_leaves_benefit_null(self):
        body = self.response()
        for card in body["cards"]:
            self.assertIsNone(card["benefit"])

    def test_live_unavailable_is_the_named_field(self):
        body = self.response()
        self.assertEqual(body["live_unavailable"], body["unavailable"])

    def test_json_serialisable_for_the_browser(self):
        json.dumps(self.response(), ensure_ascii=False)

    def test_fixtures_match_the_same_contract(self):
        files = sorted(FIXTURES.glob("*.json"))
        self.assertEqual(len(files), 3, "fixture 3개가 있어야 한다")
        for path in files:
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertLessEqual(TOP_LEVEL, set(body), path.name)
            for card in body["by_walk"]:
                self.assertLessEqual(RANKED, set(card), f"{path.name}:{card['name']}")
                self.assertLessEqual(FARE, set(card["fare"]), path.name)

    def test_blocked_prediction_nulls_every_prediction_field(self):
        class Gate:
            def check(self, pid, observed, arrival):
                return {"allowed": False, "status": "frozen", "reason": "frozen:no_movement"}
        cand = {"lots": LOTS, "dead_feeds": [], "radius_used": 1000,
                "exhausted": False, "message": None, "unlabeled": []}
        eta = {x["parking_id"]: {"duration": 60, "distance": 100, "estimated": False}
               for x in LOTS}
        with patch("src.serve.recommend.find_candidates", return_value=cand), \
             patch("src.serve.recommend.routing.multi_eta", return_value=eta), \
             patch("src.serve.recommend.walking.walk_times", return_value=eta):
            body = recommend((37.4, 126.9), 120, predictor=_Predictor(),
                             with_alternatives=False, prediction_gate=Gate())
        for card in body["cards"]:
            self.assertEqual(card["prediction_status"], "frozen")
            for field in ("avail_pred", "full_prob", "pred_p10", "pred_p90", "avail_now"):
                self.assertIsNone(card[field], field)
            # 예측을 막아도 위치·요금은 그대로 제공한다.
            self.assertIsNotNone(card["lat"])
            self.assertIsNotNone(card["fare"])


if __name__ == "__main__":
    unittest.main()
