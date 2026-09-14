#!/usr/bin/env python3
"""
추천 파이프라인 — 목적지 + 주차시간 → 공영주차장 추천 카드.

★★ 시간이 두 종류다. 절대 합치지 않는다.
      차 ETA   → 주차장 도착    ← **혼잡도 예측은 이 시점**
      도보 시간 → 목적지 도착
      총 소요                    사용자에게 보여줄 값
   자리를 찾는 건 주차장에 들어갈 때다. 목적지 도착 시각으로 예측하면 도보 시간만큼 늦게 본다.

★ ② 축은 **도보 최단**이지 차 거리가 아니다.
★ 요금은 후불(누진)과 선불 일일권을 **병기**한다. min() 자동 적용 금지(조례 비고 8).

  python3 src/serve/recommend.py            # 자체 점검
  python3 src/serve/recommend.py 안양시청 120
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.serve.candidates import find_candidates, haversine_m
from src.serve import walking, routing
from src.serve.fare import calc_fare, resolve_type
from src.serve.ranking import rank_cards
from src.features.temporal import oprtime_features

KST = timezone(timedelta(hours=9))
WALK_FAR_MIN = 15          # 도보 15분(≈1km) 초과면 경고. 1km 를 걷게 하면서 추천이라 할 수 없다


def recommend(dest, minutes, start=None, depart_in_min=0, min_n=5,
              full_prob_cutoff=0.5, predictor=None, discount=None,
              with_alternatives=True, now=None):
    """dest=(lat,lon) · minutes=주차할 분 · start=(lat,lon) 출발지(없으면 목적지에서 출발)
    depart_in_min>0 이면 카카오 미래운행(단건)으로 목적지 ETA 하나를 공통 적용한다.
    predictor(lot, arrive_dt) -> (예측 점유율, 만차확률) — 모델이 준비되면 주입."""
    start = start or dest
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)
    depart = now + timedelta(minutes=depart_in_min)

    # 1. 후보
    cand = find_candidates(dest[0], dest[1], min_n=min_n)
    lots = cand["lots"]
    # 죽은 피드도 경로·요금 카드에는 포함하되 랭킹 입력에서는 끝까지 제외한다.
    service_lots = lots + (cand.get("dead_feeds") or [])
    if not service_lots:
        return {"cards": [], "by_fare": [], "by_walk": [], "unavailable": [],
                "radius_used": cand["radius_used"], "exhausted": cand["exhausted"],
                "message": cand["message"], "unlabeled": cand.get("unlabeled") or [],
                "dead_feeds": [], "alternatives": [],
                "depart_at": depart.strftime("%H:%M"), "depart_at_iso": depart.isoformat(),
                "park_minutes": minutes}

    # 2. 차 ETA — 지금 출발이면 다중목적지 1회, 미래 출발이면 미래운행 1회(단건)
    if depart_in_min > 0:
        fe = routing.future_eta(start, dest, depart_in_min)     # ★ 미래운행은 단건만 지원
        drive = {d["parking_id"]: {"duration": fe["duration"], "distance": fe["distance"],
                                   "source": fe["source"], "estimated": fe["estimated"],
                                   "shared": True} for d in service_lots}
    else:
        drive = routing.multi_eta(start, {d["parking_id"]: (d["lat"], d["lng"]) for d in service_lots})

    # 5. 도보 — 주차장 → 목적지
    walk = walking.walk_times(
        {d["parking_id"]: (d["lat"], d["lng"], d["name"]) for d in service_lots}, dest)

    cards = []
    for d in service_lots:
        pid = d["parking_id"]
        dv, wk = drive.get(pid), walk.get(pid)
        drive_s = dv["duration"] if dv else None
        walk_s  = wk["duration"] if wk else None

        # 3. 주차장 도착 시각 = 출발 + 차 ETA   ★ 도보를 더하지 않는다
        arrive = depart + timedelta(seconds=drive_s or 0)

        # 4. 혼잡도 — 모델 전이면 비워 둔다
        is_live = not d.get("dead_feed", False)
        avail_pred = full_prob = p10 = p90 = None
        interval_status = "unavailable"
        prediction_source = "dead_feed" if not is_live else "no_model"
        if predictor is not None:
            try:
                # ★ 예측 시점은 「차가 주차장에 도착하는 시각」이다. 도보를 더하지 않는다.
                h = max(1, int(round((arrive - now).total_seconds() / 60)))
                pr = predictor.predict(pid, arrive, h)
                if is_live:
                    avail_pred, full_prob = pr.get("p50"), pr.get("full_prob")
                    p10, p90 = pr.get("p10"), pr.get("p90")
                    interval_status = pr.get("interval_status", "unverified")
                    prediction_source = pr.get("source")
            except Exception:
                prediction_source = "prediction_error"

        # 6. 요금 — 후불/선불 병기
        lot = {"type": resolve_type(d["name"], d.get("div")), "name": d["name"],
               "grade": d.get("grade"), "wdays_start": d.get("wdays_start"),
               "wdays_end": d.get("wdays_end"), "wend_start": d.get("wend_start"),
               "wend_end": d.get("wend_end")}
        f = calc_fare(lot, arrive, minutes, discount=discount)
        op = oprtime_features(d, arrive)

        est = bool((dv and dv.get("estimated")) or (wk and wk.get("estimated")))
        walk_min = round(walk_s / 60) if walk_s is not None else None
        cards.append({
            "name": d["name"], "parking_id": pid,
            "drive_min": round(drive_s / 60) if drive_s is not None else None,
            "walk_min": walk_min,
            "total_min": (round((drive_s + walk_s) / 60)
                          if None not in (drive_s, walk_s) else None),
            "fare_payg": f["total"], "fare_daily_pass": f["total_prepaid"],
            "daily_pass_better": bool(f["recommend_prepaid"]),
            "avail_now": d.get("avail_now") if is_live else None, "avail_pred": avail_pred,
            "occ_now": (min(120, 100*d["avail_now"]/d["cell_cnt"])
                        if is_live and d.get("avail_now") is not None and d.get("cell_cnt") else None),
            "full_prob": full_prob, "pred_p10": p10, "pred_p90": p90,
            "interval_status": interval_status, "prediction_source": prediction_source,
            "walk_far_warning": bool(walk_min is not None and walk_min > WALK_FAR_MIN),
            "estimated": est,
            "cell_cnt": d.get("cell_cnt"), "straight_m": d.get("straight_m"),
            "grade": d.get("grade"), "is_live": is_live,
            "operating_hours": f"{d.get('wdays_start') or '-'}~{d.get('wdays_end') or '-'}",
            "weekday_hours": f"{d.get('wdays_start') or '-'}~{d.get('wdays_end') or '-'}",
            "weekend_hours": f"{d.get('wend_start') or '-'}~{d.get('wend_end') or '-'}",
            "is_operating": bool(op["is_operating"]),
            "observation_at": d.get("observation_at"),
            "observation_age_min": d.get("observation_age_min"),
            "observation_status": d.get("observation_status", "unavailable"),
            "unavailable_note": None if is_live else "실시간 정보를 제공하지 않는 주차장입니다",
            "arrive_at": arrive.strftime("%H:%M"), "arrive_at_iso": arrive.isoformat(),
            "fare_reason": f.get("reason"),
        })

    # 7. 정렬 — 두 축을 따로 낸다. 가중합으로 섞지 않는다.
    live_cards = [c for c in cards if c["is_live"]]
    unavailable = [c for c in cards if not c["is_live"]]
    by_fare = rank_cards(live_cards, "fare", full_prob_cutoff)
    by_walk = rank_cards(live_cards, "walk", full_prob_cutoff)

    # 8. 대체 주차장 — 순위 밖, 위치만
    alts = []
    if with_alternatives:
        try:
            docs, _ = routing.alt_parkings(dest[0], dest[1], radius=min(cand["radius_used"], 2000))
            alts = [{"name": x["place_name"], "lat": float(x["y"]), "lng": float(x["x"]),
                     "is_public": x["is_public"], "distance_m": int(x.get("distance") or 0)}
                    for x in docs]
        except Exception:
            alts = []

    return {"cards": live_cards, "by_fare": by_fare, "by_walk": by_walk, "unavailable": unavailable,
            "radius_used": cand["radius_used"], "exhausted": cand["exhausted"],
            "message": cand["message"], "unlabeled": cand["unlabeled"],
            "dead_feeds": cand.get("dead_feeds") or [],
            "alternatives": alts,
            "depart_at": depart.strftime("%H:%M"), "depart_at_iso": depart.isoformat(),
            "park_minutes": minutes}


def _fmt(c):
    fp = f"{c['fare_payg']:,}" if c["fare_payg"] is not None else "-"
    dp = f"{c['fare_daily_pass']:,}" if c["fare_daily_pass"] is not None else "-"
    return (f"{c['name'][:14]:<16}"
            f"차{str(c['drive_min']) + '분':>5} 도보{str(c['walk_min']) + '분':>5} "
            f"총{str(c['total_min']) + '분':>5}  "
            f"후불{fp:>7} 선불{dp:>7}{'★' if c['daily_pass_better'] else ' '} "
            f"{str(c['avail_now']) + '대':>6}/{c['cell_cnt']}면"
            + ("  ⚠️멀다" if c["walk_far_warning"] else "")
            + ("  ⚠️추정" if c["estimated"] else ""))


if __name__ == "__main__":
    DESTS = {"안양역": (37.401857, 126.922644), "인덕원역": (37.401494, 126.976680),
             "안양시청": (37.394259, 126.956861), "평촌역": (37.394240, 126.963808),
             "범계역": (37.389784, 126.950783), "석수역": (37.435093, 126.902321)}
    name = sys.argv[1] if len(sys.argv) > 1 else "안양시청"
    mins = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    dest = DESTS.get(name)
    if dest is None: sys.exit(f"목적지를 모른다: {name}. {list(DESTS)}")

    r = recommend(dest, mins, start=(37.4018, 126.9226))   # 안양역 부근에서 출발
    print(f"목적지 {name} · 주차 {mins}분 · 반경 {r['radius_used']}m 로 후보 {len(r['cards'])}곳"
          + ("  ⚠️3km 까지 넓혔는데도 부족" if r["exhausted"] else ""))
    print(f"출발 {r['depart_at']} · 라벨 없는 곳 {len(r['unlabeled'])}곳(순위 밖) · "
          f"대체 주차장 {len(r['alternatives'])}곳\n")
    print("① 최저요금순 (후불 기준)")
    for c in r["by_fare"][:5]: print("  " + _fmt(c))
    print("\n② 도보 최단순")
    for c in r["by_walk"][:5]: print("  " + _fmt(c))
    print("\n카드 원형 1개:")
    import json
    print(json.dumps(r["by_walk"][0], ensure_ascii=False, indent=1))
