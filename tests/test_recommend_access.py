import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from src.serve.recommend import recommend
from tests.test_access_check import rule


def lot(pid, dead=False):
    return {"parking_id": pid, "name": f"주차장{pid}노외", "lat": 37.4, "lng": 126.9,
            "grade": 2, "div": "노외", "cell_cnt": 20, "avail_now": 5,
            "straight_m": pid * 100, "dead_feed": dead,
            "wdays_start": "09:00", "wdays_end": "17:00",
            "wend_start": "00:00", "wend_end": "00:00"}


def candidates(lots, radius, dead=None):
    return {"lots": lots, "dead_feeds": dead or [], "radius_used": radius,
            "exhausted": False, "message": None, "unlabeled": []}


class Rules:
    def lookup(self, pid, day, is_holiday=False):
        return rule(((0, 1080),), ((0, 1080),)) if pid == 1 else rule()


class RecommendAccessTests(unittest.TestCase):
    def run_recommend(self, batches, rules=Rules(), min_n=1):
        predictor = Mock()
        predictor.predict.return_value = {"p50": 30, "full_prob": .1}
        route = lambda start, targets: {pid: {"duration": 0, "estimated": False}
                                       for pid in targets}
        walk = lambda targets, dest: {pid: {"duration": 60, "estimated": False}
                                     for pid in targets}
        with patch("src.serve.recommend.find_candidates", side_effect=batches) as find, \
             patch("src.serve.recommend.routing.multi_eta", side_effect=route) as routing, \
             patch("src.serve.recommend.walking.walk_times", side_effect=walk):
            result = recommend((37.4, 126.9), 240, now=datetime(2026, 9, 16, 16),
                               min_n=min_n, predictor=predictor, access_rules=rules,
                               with_alternatives=False)
        return result, find, routing, predictor

    def test_closed_candidate_removed_and_radius_expanded(self):
        result, find, routing, predictor = self.run_recommend([
            candidates([lot(1)], 1000), candidates([lot(1), lot(2)], 2000)])
        self.assertEqual([c["parking_id"] for c in result["by_walk"]], [2])
        self.assertEqual(result["excluded"][0]["parking_id"], 1)
        self.assertEqual(result["radius_used"], 2000)
        self.assertFalse(result["exhausted"])
        self.assertEqual(find.call_args.kwargs["min_radius"], 2000)
        self.assertEqual(list(routing.call_args.args[1]), [2])
        self.assertEqual(predictor.predict.call_args.args[0], 2)

    def test_all_closed_stops_at_3km(self):
        result, find, _, predictor = self.run_recommend([
            candidates([lot(1)], radius) for radius in (1000, 2000, 3000)])
        self.assertEqual(result["cards"], [])
        self.assertTrue(result["exhausted"])
        self.assertEqual(result["radius_used"], 3000)
        self.assertEqual(len(result["excluded"]), 1)
        self.assertEqual(find.call_count, 3)
        predictor.predict.assert_not_called()

    def test_unknown_not_excluded_and_dead_does_not_fill_minimum(self):
        unknown = Mock()
        unknown.lookup.return_value = None
        result, _, _, _ = self.run_recommend(
            [candidates([lot(2)], 3000, [lot(3, dead=True)])], rules=unknown, min_n=2)
        self.assertEqual(result["candidate_count"], 1)
        self.assertTrue(result["exhausted"])
        self.assertEqual(result["excluded"], [])
        self.assertEqual(result["cards"][0]["access_status"], "unknown")
        self.assertEqual(len(result["unavailable"]), 1)

    def test_future_departure_uses_origin_and_candidate_specific_eta(self):
        lots = [lot(1), lot(2)]
        lots[0]["lat"], lots[0]["lng"] = 37.41, 126.91
        lots[1]["lat"], lots[1]["lng"] = 37.42, 126.92
        cand = candidates(lots, 1000)
        origin = (37.39, 126.88)

        def routes(start, targets):
            self.assertEqual(start, origin)
            self.assertEqual(targets, {1: (37.41, 126.91), 2: (37.42, 126.92)})
            return {
                1: {"duration": 5 * 60, "estimated": False, "source": "kakao"},
                2: {"duration": 15 * 60, "estimated": False, "source": "kakao"},
            }

        walk = lambda targets, dest: {
            pid: {"duration": 60, "estimated": False} for pid in targets
        }
        now = datetime(2026, 9, 16, 10)
        with patch("src.serve.recommend.find_candidates", return_value=cand), \
             patch("src.serve.recommend.routing.multi_eta", side_effect=routes) as multi, \
             patch("src.serve.recommend.routing.future_eta") as future, \
             patch("src.serve.recommend.walking.walk_times", side_effect=walk):
            result = recommend((37.4, 126.9), 60, start=origin, depart_in_min=30,
                               now=now, min_n=2, with_alternatives=False)

        by_id = {card["parking_id"]: card for card in result["cards"]}
        self.assertEqual(by_id[1]["arrive_at"], "10:35")
        self.assertEqual(by_id[2]["arrive_at"], "10:45")
        self.assertEqual(by_id[1]["expected_departure_at"][11:16], "11:35")
        self.assertEqual(by_id[2]["expected_departure_at"][11:16], "11:45")
        self.assertEqual(by_id[1]["route_traffic_basis"], "current")
        self.assertEqual(by_id[2]["route_traffic_basis"], "current")
        multi.assert_called_once()
        future.assert_not_called()


if __name__ == "__main__":
    unittest.main()
