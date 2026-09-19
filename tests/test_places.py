import sqlite3
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.serve import places
from src.serve.places import (ReverseGeocodeUnavailable, SearchUnavailable,
                              reverse_geocode, search_places)

# 카카오 응답 모양 그대로다. x=경도, y=위도.
CITY_HALL = {"place_name": "안양시청", "x": "126.956861", "y": "37.394259",
             "road_address_name": "경기 안양시 동안구 시민대로 235",
             "address_name": "경기 안양시 동안구 관양동 1500",
             "category_group_name": "공공기관"}
SEOUL = {"place_name": "서울시청", "x": "126.978388", "y": "37.566536",
         "road_address_name": "서울 중구 세종대로 110", "address_name": "서울 중구 태평로1가 31"}
ADDRESS_DOC = {"address_name": "경기 안양시 동안구 관평로 149", "x": "126.960260", "y": "37.391390",
               "road_address": {"address_name": "경기 안양시 동안구 관평로 149"}}
REVERSE_DOC = {
    "road_address": {"address_name": "경기 안양시 동안구 시민대로 235"},
    "address": {"address_name": "경기 안양시 동안구 관양동 1590"},
}


class PlaceSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        patcher = patch.object(places, "CACHE_DB", Path(self.tmp.name) / "places.sqlite")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        throttle = patch.object(places, "MIN_INTERVAL_SEC", 0)
        throttle.start()
        self.addCleanup(throttle.stop)

    def search(self, keyword=None, address=None, **kwargs):
        def call(url, params, key):
            return keyword if url == places.KEYWORD else address
        with patch.object(places, "_call", side_effect=call):
            return search_places(kwargs.pop("query", "안양시청"), key="k", **kwargs)

    def test_coordinate_order_is_not_swapped(self):
        # x=경도, y=위도. 뒤집히면 위도가 126대로 나와 이 검사가 깨진다.
        place = self.search(keyword=[CITY_HALL], address=[])["places"][0]
        self.assertAlmostEqual(place["lat"], 37.394259)
        self.assertAlmostEqual(place["lng"], 126.956861)
        self.assertGreater(place["lat"], 36.0)
        self.assertLess(place["lat"], 39.0)
        self.assertGreater(place["lng"], 125.0)

    def test_anyang_results_rank_before_outside(self):
        result = self.search(keyword=[SEOUL, CITY_HALL], address=[])
        self.assertEqual(result["places"][0]["name"], "안양시청")
        self.assertTrue(result["places"][0]["in_anyang"])
        # 경계 밖이라고 버리지는 않는다. 뒤로 밀 뿐이다.
        self.assertFalse(result["places"][1]["in_anyang"])

    def test_returns_name_road_address_and_coordinates(self):
        place = self.search(keyword=[CITY_HALL], address=[])["places"][0]
        self.assertEqual(place["name"], "안양시청")
        self.assertEqual(place["road_address"], "경기 안양시 동안구 시민대로 235")
        self.assertIn("lat", place)
        self.assertIn("lng", place)

    def test_address_search_supplements_keyword(self):
        result = self.search(keyword=[], address=[ADDRESS_DOC])
        self.assertEqual(len(result["places"]), 1)
        self.assertEqual(result["places"][0]["road_address"], "경기 안양시 동안구 관평로 149")

    def test_duplicates_are_collapsed(self):
        result = self.search(keyword=[CITY_HALL, dict(CITY_HALL)], address=[CITY_HALL])
        self.assertEqual(len(result["places"]), 1)

    def test_fresh_cache_is_reused_without_calling_kakao(self):
        self.search(keyword=[CITY_HALL], address=[])
        with patch.object(places, "_call", side_effect=AssertionError("호출되면 안 된다")):
            result = search_places("안양시청", key="k")
        self.assertEqual(result["source"], "cache")
        self.assertFalse(result["stale"])

    def test_kakao_failure_falls_back_to_stale_cache(self):
        self.search(keyword=[CITY_HALL], address=[])
        with patch.object(places, "CACHE_TTL_SEC", 0), \
             patch.object(places, "_call", return_value=None):
            result = search_places("안양시청", key="k")
        self.assertEqual(result["source"], "cache")
        self.assertTrue(result["stale"])
        self.assertEqual(result["places"][0]["name"], "안양시청")

    def test_kakao_failure_without_cache_raises(self):
        with patch.object(places, "_call", return_value=None):
            with self.assertRaises(SearchUnavailable):
                search_places("처음보는검색어", key="k")

    def test_missing_key_is_not_reported_as_empty_result(self):
        with self.assertRaises(SearchUnavailable):
            search_places("안양시청", key="")

    def test_empty_result_is_distinct_from_failure(self):
        result = self.search(keyword=[], address=[], query="없는장소명")
        self.assertEqual(result["places"], [])
        self.assertEqual(result["source"], "kakao")

    def test_blank_query_rejected_and_long_query_truncated(self):
        with self.assertRaises(ValueError):
            search_places("   ", key="k")
        seen = {}

        def call(url, params, key):
            seen[url] = params["query"]
            return []
        with patch.object(places, "_call", side_effect=call):
            search_places("가" * 200, key="k")
        self.assertEqual(len(seen[places.KEYWORD]), places.MAX_QUERY_LEN)

    def test_throttle_spaces_out_calls(self):
        with patch.object(places, "MIN_INTERVAL_SEC", 0.05):
            places._last_call = 0.0
            started = time.monotonic()
            places._throttle()
            places._throttle()
            self.assertGreaterEqual(time.monotonic() - started, 0.05)

    def test_broken_cache_does_not_break_search(self):
        places.CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        places.CACHE_DB.write_bytes(b"not a sqlite file")
        result = self.search(keyword=[CITY_HALL], address=[])
        self.assertEqual(result["places"][0]["name"], "안양시청")

    def test_malformed_document_is_skipped(self):
        result = self.search(keyword=[{"place_name": "좌표없음"}, CITY_HALL], address=[])
        self.assertEqual(len(result["places"]), 1)

    def test_reverse_geocode_keeps_selected_coordinate_and_returns_address(self):
        with patch.object(places, "_call", return_value=[REVERSE_DOC]) as called:
            place = reverse_geocode(37.394259, 126.956861, key="k")
        self.assertEqual(called.call_args.args[0], places.COORD2ADDRESS)
        self.assertEqual(called.call_args.args[1], {"x": 126.956861, "y": 37.394259})
        self.assertEqual(place["road_address"], "경기 안양시 동안구 시민대로 235")
        self.assertAlmostEqual(place["lat"], 37.394259)
        self.assertAlmostEqual(place["lng"], 126.956861)

    def test_reverse_geocode_reports_failure_and_bad_coordinate(self):
        with patch.object(places, "_call", return_value=None):
            with self.assertRaises(ReverseGeocodeUnavailable):
                reverse_geocode(37.4, 126.9, key="k")
        with self.assertRaises(ValueError):
            reverse_geocode(91, 126.9, key="k")


if __name__ == "__main__":
    unittest.main()
