from unittest.mock import patch

from src.serve.recommend import recommend


class _Predictor:
    def predict(self, pid, arrive, horizon):
        return {"p10": 90, "p50": 95, "p90": 100, "full_prob": .9 if pid == 1 else .1}


def test_high_full_probability_is_demoted_but_estimated_is_not():
    lots = [
        {"parking_id": 1, "name": "가까운만차노상", "lat": 37.4, "lng": 126.9, "grade": 1,
         "div": "노상", "cell_cnt": 20, "avail_now": 20, "straight_m": 100,
         "wdays_start": "00:00", "wdays_end": "24:00", "wend_start": "00:00", "wend_end": "24:00"},
        {"parking_id": 2, "name": "먼여유노상", "lat": 37.41, "lng": 126.91, "grade": 1,
         "div": "노상", "cell_cnt": 20, "avail_now": 1, "straight_m": 200,
         "wdays_start": "00:00", "wdays_end": "24:00", "wend_start": "00:00", "wend_end": "24:00"},
    ]
    cand = {"lots": lots, "dead_feeds": [], "radius_used": 1000, "exhausted": False, "message": None, "unlabeled": []}
    drive = {x["parking_id"]: {"duration": 60, "distance": 100, "estimated": False} for x in lots}
    walk = {x["parking_id"]: {"duration": 60, "distance": 100, "estimated": False} for x in lots}
    with patch("src.serve.recommend.find_candidates", return_value=cand), \
         patch("src.serve.recommend.routing.multi_eta", return_value=drive), \
         patch("src.serve.recommend.walking.walk_times", return_value=walk):
        r = recommend((37.4, 126.9), 60, predictor=_Predictor(), with_alternatives=False)
    assert [x["parking_id"] for x in r["by_walk"]] == [2, 1]
