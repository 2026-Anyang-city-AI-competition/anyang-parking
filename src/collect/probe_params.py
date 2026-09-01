# src/collect/probe_params.py  (전체 교체)
import os, json, time, requests
from urllib.parse import unquote
from dotenv import load_dotenv

load_dotenv()
KEY = os.environ["KOTSA_KEY"]
if "%" in KEY: KEY = unquote(KEY)
BASE = "https://apis.data.go.kr/B553881/Parking"

def raw(op="PrkSttusInfo", retry=4, **extra):
    p = {"serviceKey": KEY, "pageNo": 1, "numOfRows": 10, "format": 2}
    p.update(extra)
    for a in range(retry):
        try:
            t0 = time.time()
            r = requests.get(f"{BASE}/{op}", params=p, timeout=180)
            return r, time.time() - t0
        except Exception as e:
            if a == retry - 1: return None, str(e)
            time.sleep(3 * (a + 1))

def parse(r, op="PrkSttusInfo"):
    """items 수와 totalCount. 실패해도 안 터짐"""
    if r is None: return None, None
    try:
        j = r.json()
    except Exception:
        return None, None                      # XML 에러 응답
    return len(j.get(op) or []), j.get("totalCount")

print("=== numOfRows별 스윕 총소요 추정 ===")
TOTAL = 787_237
for n in [5000, 10000, 15000, 20000, 30000]:
    ts = []
    for _ in range(3):                      # 3회 반복해서 변동 제거
        r, el = raw(op="PrkRealtimeInfo", numOfRows=n, pageNo=1)
        if r is not None and len(r.content) > 1000: ts.append(el)
    if not ts: print(f"  {n:>6} 전부 실패"); continue
    med = sorted(ts)[len(ts)//2]
    pages = -(-TOTAL // n)
    print(f"  {n:>6} → {pages:>4}p × {med:5.1f}s = 스윕 {pages*med/60:5.1f}분"
          f"   1일 5분주기 {pages*288:>7,}회")
    
# print("=== ① numOfRows 상한 ===")
# base_b = None
# for n in [25000, 30000, 50000, 100000]:
#     r, el = raw(numOfRows=n)
#     if r is None:
#         print(f"  {n:>6} → 실패 {el}"); continue
#     b = len(r.content)
#     if base_b is None: base_b = b
#     got, tc = parse(r)
#     got_s = f"{got:>6}" if got is not None else "  XML "
#     flag = "🟢 늘어남!" if b > base_b * 1.5 else "1000에서 캡"
#     print(f"  {n:>6} → {b:>9,}B ({b/base_b:4.1f}배)  items={got_s}  {el:5.1f}s  {flag}")

# ── ② ID 구조: 지역이 ID에 박혀 있나 ──────────────────
# print("\n=== ② prk_center_id 지역 인코딩 확인 ===")
# r, _ = raw(numOfRows=300, pageNo=1)
# try:
#     rows = r.json()["PrkSttusInfo"]
#     seen = {}
#     for o in rows:
#         seg = o["prk_center_id"].split("-")
#         key = (seg[0], seg[1])
#         sido = o.get("prk_plce_adres_sido", "")
#         gu   = o.get("prk_plce_adres_sigungu", "")
#         seen.setdefault(key, set()).add(f"{sido} {gu}")
#     for k, v in list(seen.items())[:20]:
#         print(f"  {k[0]}-{k[1]}  →  {sorted(v)}")
#     print(f"\n  고유 (seg0,seg1) 조합: {len(seen)}개 / 300행")
#     multi = [k for k, v in seen.items() if len(v) > 1]
#     print(f"  한 조합이 여러 지역에 걸친 경우: {len(multi)}개")
# except Exception as e:
#     print("  실패:", e)