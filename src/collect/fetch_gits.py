#!/usr/bin/env python3
"""
경기도 교통정보센터(GITS) 주차장 API — 외부 라벨 수집.

  python3 src/collect/fetch_gits.py probe      # 응답 원형·규모·유형 분포
  python3 src/collect/fetch_gits.py info       # 기본정보 전수 → CSV
  python3 src/collect/fetch_gits.py rt-test    # 실시간 여부(10분 2회)
  python3 src/collect/fetch_gits.py poll       # 5분 간격 상시 수집 → gits.db

실측 확인된 사양(2026-09-04, 추측 아님):
  base   https://openapigits.gg.go.kr/api/rest/
  파라미터 serviceKey(필수) · laeId(선택)   ※ apiKey/key/authKey 는 인식 안 됨
  응답   XML. ServiceResult > msgBody > itemList*  (JSON 없음)
  헤더   msgHeader/headerCd  0=정상 · 5=인증키 파라미터 없음 · 7=키 사용중지 · 8=요청제한초과

  getParkingPlaceAvailabilityInfoList  813곳
    laeId laeNm pkplcId pkplcNm pklotCnt avblPklotCnt ocrnDt
  getParkingPlaceInfoList             1,010곳
    +pkplcDivNm(공영/민영) pkplcTypeNm(노상/노외/부설/기타) latCrdn lonCrdn
     roadNmAddr lotnoAddr pklotCnt sbcmpct/pwdbs/female/olman/ev PklotCnt
     lndlvDivNm wkday/sat/hldy OprtStart·EndTime
     parkingBscTime parkingBscFare addUnitTime addUnitFare
     ddPktckFareAplcnTime ddPktckFare mmCmmtktFare

  점유율 = (pklotCnt - avblPklotCnt) / pklotCnt   ※ 도시공사와 달리 '잔여'가 온다
"""
import argparse, sqlite3, sys, time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
RAW  = ROOT / "data/raw"; RAW.mkdir(parents=True, exist_ok=True)
DB   = RAW / "gits.db"
BASE = "https://openapigits.gg.go.kr/api/rest/"
AVAIL, INFO = "getParkingPlaceAvailabilityInfoList", "getParkingPlaceInfoList"
TIMEOUT, RETRY = 60, 5
INTERVAL_OK   = 300      # 기본 5분 (안양 포털과 동일)
INTERVAL_SLOW = 600      # headerCd 8(요청 제한 초과) 시 자동 감속
BACKOFF_RECOVER = 3      # 연속 이만큼 성공하면 기본 간격으로 복귀

KEY = ""
for _l in (ROOT/".env").read_text(encoding="utf-8").splitlines():
    if _l.startswith("GITS_KEY="): KEY = _l.split("=", 1)[1].strip()
if not KEY: sys.exit("GITS_KEY 없음. .env 에 넣을 것.")

class GitsError(RuntimeError):
    """headerCd 를 그대로 들고 다닌다. 8(요청 제한)을 백오프로 구분하기 위해."""
    def __init__(self, code, msg):
        super().__init__(f"headerCd={code} {msg}")
        self.code = str(code)

def call(op, **params):
    p = {"serviceKey": KEY, **{k: v for k, v in params.items() if v}}
    last = None
    for a in range(RETRY):
        try:
            r = requests.get(BASE + op, params=p, timeout=TIMEOUT)
            root = ET.fromstring(r.text)
            cd = (root.findtext("msgHeader/headerCd") or "?").strip()
            if cd != "0":
                raise GitsError(cd, root.findtext("msgHeader/headerMsg"))
            body = root.find("msgBody")
            its = body.findall("itemList") if body is not None else []
            return [{c.tag: c.text for c in it} for it in its]   # list[dict] — pandas 불필요
        except GitsError as e:
            if e.code in ("7", "8"):     # 키 정지·요청 제한은 재시도해도 소용없다
                raise
            last = e; time.sleep(3 * (a + 1))
        except Exception as e:
            last = e; time.sleep(3 * (a + 1))
    raise last

def probe(**_):
    import pandas as pd
    av  = pd.DataFrame(call(AVAIL))
    inf = pd.DataFrame(call(INFO))
    print(f"실시간 {len(av):,}곳 · 기본정보 {len(inf):,}곳")
    print("실시간 필드:", list(av.columns))
    print("기본정보 필드:", list(inf.columns))
    for d, n in ((av, "실시간"), (inf, "기본정보")):
        print(f"\n{n} 지자체 {d.laeNm.nunique()}곳: {Counter(d.laeNm).most_common()}")
    print(f"\n유형: {Counter(inf.pkplcTypeNm).most_common()}")
    print(f"구분: {Counter(inf.pkplcDivNm).most_common()}")
    j = inf.assign(k=inf.laeId+"_"+inf.pkplcId).merge(
        av.assign(k=av.laeId+"_"+av.pkplcId)[["k"]], on="k")
    nw = j[j.pkplcTypeNm == "노외"]
    print(f"\n★ 실시간 라벨 있는 노외 {len(nw)}곳 "
          f"(안양 {len(nw[nw.laeNm=='안양시'])} / 외부 {len(nw[nw.laeNm!='안양시'])})")

def info(**_):
    import pandas as pd
    d = pd.DataFrame(call(INFO)); f = RAW/"gits_info.csv"
    d.to_csv(f, index=False)
    print(f"기본정보 {len(d):,}곳 → {f.name}")

def rt_test(wait=600, **_):
    import pandas as pd
    a = pd.DataFrame(call(AVAIL)); print(f"1차 {len(a)}곳 {time.strftime('%H:%M:%S')}")
    time.sleep(wait)
    b = pd.DataFrame(call(AVAIL)); print(f"2차 {len(b)}곳 {time.strftime('%H:%M:%S')}")
    m = a.assign(k=a.laeId+"_"+a.pkplcId).merge(
        b.assign(k=b.laeId+"_"+b.pkplcId), on="k", suffixes=("_1","_2"))
    ch = m.avblPklotCnt_1 != m.avblPklotCnt_2
    print(f"★ {wait//60}분간 변동 {int(ch.sum())}/{len(m)} ({ch.mean():.0%})")

def db_init():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS gits_lots(
        lae_id TEXT, lae_nm TEXT, pkplc_id TEXT, pkplc_nm TEXT,
        div_nm TEXT, type_nm TEXT, lat REAL, lon REAL, addr TEXT,
        cell_cnt INT, wday_st TEXT, wday_en TEXT, sat_st TEXT, sat_en TEXT,
        hldy_st TEXT, hldy_en TEXT, bs_time INT, bs_fare INT,
        add_time INT, add_fare INT, day_fare INT, mon_fare INT, first_seen TEXT,
        PRIMARY KEY(lae_id, pkplc_id));
    CREATE TABLE IF NOT EXISTS gits_obs(
        ts_kst TEXT, lae_id TEXT, pkplc_id TEXT,
        cell_cnt INT, avail_cnt INT, ocrn_dt TEXT,
        PRIMARY KEY(ts_kst, lae_id, pkplc_id)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_gits_obs ON gits_obs(lae_id, pkplc_id, ts_kst);
    """); c.commit(); return c

def poll(once=False, **_):
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    c = db_init()

    def log(m): print(m, flush=True)

    def refresh_lots():
        try:
            d = call(INFO)
            now = datetime.now(KST).isoformat()
            cols = ["laeId","laeNm","pkplcId","pkplcNm","pkplcDivNm","pkplcTypeNm",
                    "latCrdn","lonCrdn","roadNmAddr","pklotCnt",
                    "wkdayOprtStartTime","wkdayOprtEndTime","satOprtStartTime","satOprtEndTime",
                    "hldyOprtStartTime","hldyOprtEndTime","parkingBscTime","parkingBscFare",
                    "addUnitTime","addUnitFare","ddPktckFare","mmCmmtktFare"]
            c.executemany("INSERT OR IGNORE INTO gits_lots VALUES ("+",".join("?"*23)+")",
                [tuple(r.get(k) for k in cols) + (now,) for r in d])
            c.commit(); log(f"[lots] {len(d):,}곳 갱신")
        except Exception as e:
            log(f"[lots] 실패(계속): {str(e)[:120]}")

    def fetch_once():
        # ★ ts 는 fetch '시작' 시각이다. 응답 지연이 타임스탬프에 섞이지 않게.
        ts = datetime.now(KST).replace(microsecond=0).isoformat()
        a = call(AVAIL)
        c.executemany("INSERT OR IGNORE INTO gits_obs VALUES (?,?,?,?,?,?)",
            [(ts, r.get("laeId"), r.get("pkplcId"), r.get("pklotCnt"),
              r.get("avblPklotCnt"), r.get("ocrnDt")) for r in a])
        c.commit()
        return ts, len(a)

    refresh_lots()
    if once:
        ts, n = fetch_once()
        total = c.execute("SELECT COUNT(*) FROM gits_obs").fetchone()[0]
        log(f"[ok] {ts} +{n}행 (누적 {total:,})")
        return

    interval, ok_streak = INTERVAL_OK, 0
    # 벽시계 격자에 정렬한다. sleep(INTERVAL) 을 쓰면 작업 시간이 누적돼 하루 15분씩 밀린다.
    next_t = (time.time() // interval + 1) * interval
    last_lots = time.time()
    while True:
        time.sleep(max(0, next_t - time.time()))
        try:
            ts, n = fetch_once()
            total = c.execute("SELECT COUNT(*) FROM gits_obs").fetchone()[0]
            ok_streak += 1
            log(f"[ok] {ts} +{n}행 (누적 {total:,}) interval={interval}s streak={ok_streak}")
            if interval != INTERVAL_OK and ok_streak >= BACKOFF_RECOVER:
                interval = INTERVAL_OK; ok_streak = 0
                log(f"[backoff] 연속 {BACKOFF_RECOVER}회 성공 → interval={interval}s 복귀")
        except GitsError as e:
            ok_streak = 0
            if e.code == "8":
                interval = INTERVAL_SLOW
                log(f"[backoff] headerCd 8 (요청 제한 초과) → interval={interval}s 로 감속")
            else:
                log(f"[err] {e} interval={interval}s")
        except Exception as e:
            ok_streak = 0
            log(f"[err] {type(e).__name__}: {str(e)[:150]} interval={interval}s")

        if time.time() - last_lots > 86400:      # 기본정보는 하루 1회면 충분
            refresh_lots(); last_lots = time.time()

        next_t += interval
        if next_t <= time.time():                # 밀렸으면 건너뛰고 격자 복귀
            missed = int((time.time() - next_t) // interval) + 1
            next_t += missed * interval
            log(f"⚠️ {missed}주기 건너뜀 (다음 {datetime.fromtimestamp(next_t, KST):%H:%M:%S})")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="probe",
                    choices=["probe","info","rt-test","poll"])
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    {"probe":probe, "info":info, "rt-test":rt_test, "poll":poll}[a.cmd](once=a.once)
