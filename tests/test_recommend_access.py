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


if __name__ == "__main__":
    unittest.main()
