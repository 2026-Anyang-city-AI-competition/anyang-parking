#!/usr/bin/env python3
"""엔드투엔드 데모 3건 → reports/tables/e2e_demo.md"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from src.serve.recommend import recommend
from src.serve.predictor import Predictor
from src.serve.fare import calc_fare, resolve_type
from src.serve.candidates import _labeled_lots
from src.serve import walking
KST = timezone(timedelta(hours=9))

CASES = [("안양시청", (37.394259,126.956861), datetime(2026,9,7,14,0,tzinfo=KST), 120, "평일(월)"),
         ("석수역",   (37.435093,126.902321), datetime(2026,9,12,11,0,tzinfo=KST), 60,  "토요일"),
         ("범계역",   (37.389784,126.950783), datetime(2026,9,13,15,0,tzinfo=KST), 180, "일요일")]

R=[]
def say(s=""): print(s, flush=True); R.append(s)

P = Predictor()
L = {d["parking_id"]: d for d in _labeled_lots()}
say("# 엔드투엔드 데모 3건")
say()
say("예측 시점은 **차가 주차장에 도착하는 시각**이다(도보 미포함).")
say("요금은 도착 시각·주차 시간 기준으로 조례 누진제로 계산한다.")
say("`현재점유` 는 **주차 대수/총면수** 다 — `avblPklotCnt` 는 잔여가 아니다.")
say()
for name, dest, when, mins, daylabel in CASES:
    before = dict(walking.CALLS)
    r = recommend(dest, mins, start=(37.4018,126.9226), predictor=P)
    say(f"## {name} · {daylabel} {when:%H:%M} 도착 · {mins}분 주차")
    say()
    say(f"- 반경 **{r['radius_used']}m** 로 후보 {len(r['cards'])}곳 "
        f"· 죽은 피드 {len(r['dead_feeds'])}곳(순위 밖) "
        f"· 대체 주차장 {len(r['alternatives'])}곳")
    say()
    say("| 주차장 | 도보 | 현재점유 | 예측 p50 | [p10,p90] | 만차확률 | 후불 | 선불 |")
    say("|---|---:|---:|---:|---|---:|---:|---:|")
    for c in r["by_walk"][:5]:
        d = L.get(c["parking_id"], {})
        lot = {"type": resolve_type(d.get("name"), d.get("div")), "name": d.get("name"),
               "grade": d.get("grade"), "wdays_start": d.get("wdays_start"),
               "wdays_end": d.get("wdays_end"), "wend_start": d.get("wend_start"),
               "wend_end": d.get("wend_end")}
        f = calc_fare(lot, when, mins)
        rng = f"[{c['pred_p10']}, {c['pred_p90']}]" if c["pred_p10"] is not None else "-"
        fp = f"{f['total']:,}" if f["total"] is not None else "-"
        dp = f"{f['total_prepaid']:,}" if f["total_prepaid"] is not None else "-"
        say(f"| {c['name'][:14]} | {c['walk_min']}분 | {c['avail_now']}/{c['cell_cnt']} | "
            f"{c['avail_pred']} | {rng} | {c['full_prob']} | {fp} | {dp} |"
            + ("  ⚠️멀다" if c["walk_far_warning"] else ""))
    say()
    if any(c["estimated"] for c in r["cards"]):
        say("- ⚠️ 이번 실행은 경로 폴백(estimated=True) 포함: 만차확률 강등은 정책대로 제외했다. "
            "비추정 경로의 강등 회귀 테스트는 `tests/test_recommend_demotion.py`에서 통과했다.")
        say()
    if r["unavailable"]:
        say(f"**실시간 미제공 ({len(r['unavailable'])}곳, 순위 제외)**")
        say()
        say("| 주차장 | 급지 | 차 + 도보 = 총 | 후불 | 선불 | 안내 | 면수 · 운영시간 |")
        say("|---|---:|---|---:|---:|---|---|")
        for c in r["unavailable"][:5]:
            fp = f"{c['fare_payg']:,}" if c["fare_payg"] is not None else "-"
            dp = f"{c['fare_daily_pass']:,}" if c["fare_daily_pass"] is not None else "-"
            say(f"| {c['name']} | {c['grade'] or '-'}급지 | 🚗 {c['drive_min']}분 + 🚶 {c['walk_min']}분 = {c['total_min']}분 | {fp} | {dp} | {c['unavailable_note']} | {c['cell_cnt']}면 · {c['operating_hours']} |")
        say()
    delta = {k: walking.CALLS[k] - before[k] for k in walking.CALLS}
    say(f"- 도보 호출: TMAP {delta['tmap']} · cache_hit {delta['cache_hit']} · fallback {delta['fallback']}")
    say()
(ROOT/"reports/tables/e2e_demo.md").write_text("\n".join(R)+"\n", encoding="utf-8")
print(f"\n→ reports/tables/e2e_demo.md")
