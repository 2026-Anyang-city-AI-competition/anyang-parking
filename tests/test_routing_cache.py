import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from src.serve import routing


class RoutingCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "routes.sqlite"

        def connect():
            db = sqlite3.connect(self.path)
            db.execute("""CREATE TABLE IF NOT EXISTS route(
                origin_lat REAL, origin_lon REAL, dest_lat REAL, dest_lon REAL,
                distance INT, duration INT, ts TEXT,
                PRIMARY KEY(origin_lat, origin_lon, dest_lat, dest_lon)) WITHOUT ROWID""")
            db.commit()
            return db

        self.connect = connect
        self.db_patch = patch.object(routing, "_route_db", side_effect=connect)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def response(routes):
        response = Mock(status_code=200)
        response.json.return_value = {"routes": routes}
        return response

    def test_second_request_uses_fresh_cache_before_http(self):
        origin = (37.4018, 126.9226)
        dests = {39: (37.3942, 126.9568)}
        route = {"result_code": 0, "key": "39",
                 "summary": {"distance": 4100, "duration": 720}}
        with patch.object(routing.requests, "post", return_value=self.response([route])) as post:
            first = routing.multi_eta(origin, dests, key="key")
            second = routing.multi_eta(origin, dests, key="key")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(first[39]["source"], "kakao")
        self.assertEqual(second[39]["source"], "cache")
        self.assertFalse(second[39]["estimated"])

    def test_http_only_receives_cache_misses(self):
        origin = (37.4018, 126.9226)
        cached_dest = (37.3942, 126.9568)
        new_dest = (37.3897, 126.9507)
        with self.connect() as db:
            db.execute("INSERT INTO route VALUES (?,?,?,?,?,?,?)",
                       (*routing._cache_key(origin, cached_dest), 4000, 700,
                        datetime.now(routing.KST).isoformat()))
        route = {"result_code": 0, "key": "2",
                 "summary": {"distance": 5000, "duration": 800}}
        with patch.object(routing.requests, "post", return_value=self.response([route])) as post:
            result = routing.multi_eta(origin, {1: cached_dest, 2: new_dest}, key="key")
        sent = json.loads(post.call_args.kwargs["data"])["destinations"]
        self.assertEqual([item["key"] for item in sent], ["2"])
        self.assertEqual(result[1]["source"], "cache")
        self.assertEqual(result[2]["source"], "kakao")

    def test_destination_outside_multi_radius_uses_single_route(self):
        origin = (37.5665, 126.9780)
        dest = (37.3920, 126.9510)
        outside = {"result_code": 304, "result_msg": "반경 범위를 벗어남", "key": "39"}
        single = {"result_code": 0,
                  "summary": {"distance": 29240, "duration": 4323}}
        with patch.object(routing.requests, "post", return_value=self.response([outside])), \
             patch.object(routing.requests, "get", return_value=self.response([single])) as get:
            result = routing.multi_eta(origin, {39: dest}, key="key")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(result[39]["source"], "kakao_single")
        self.assertFalse(result[39]["estimated"])
        self.assertEqual(result[39]["duration"], 4323)

        # 보완 성공 결과도 캐시돼 같은 요청에서 외부 API를 다시 부르지 않는다.
        with patch.object(routing.requests, "post") as post, \
             patch.object(routing.requests, "get") as get:
            cached = routing.multi_eta(origin, {39: dest}, key="key")
        post.assert_not_called()
        get.assert_not_called()
        self.assertEqual(cached[39]["source"], "cache")

    def test_recent_stale_cache_is_explicit_estimate_when_api_is_unavailable(self):
        origin = (37.4018, 126.9226)
        dest = (37.3942, 126.9568)
        with self.connect() as db:
            db.execute("INSERT INTO route VALUES (?,?,?,?,?,?,?)",
                       (*routing._cache_key(origin, dest), 4000, 700,
                        (datetime.now(routing.KST)-timedelta(hours=1)).isoformat()))
        result = routing.multi_eta(origin, {1: dest}, key="")
        self.assertEqual(result[1]["source"], "stale_cache")
        self.assertTrue(result[1]["estimated"])

    def test_expired_cache_falls_back(self):
        origin = (37.4018, 126.9226)
        dest = (37.3942, 126.9568)
        with self.connect() as db:
            db.execute("INSERT INTO route VALUES (?,?,?,?,?,?,?)",
                       (*routing._cache_key(origin, dest), 4000, 700,
                        (datetime.now(routing.KST)-timedelta(days=2)).isoformat()))
        result = routing.multi_eta(origin, {1: dest}, key="")
        self.assertEqual(result[1]["source"], "fallback")
        self.assertTrue(result[1]["estimated"])


if __name__ == "__main__":
    unittest.main()
