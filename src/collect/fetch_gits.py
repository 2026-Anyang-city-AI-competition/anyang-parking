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

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW  = ROOT / "data/raw"; RAW.mkdir(parents=True, exist_ok=True)
DB   = RAW / "gits.db"
BASE = "https://openapigits.gg.go.kr/api/rest/"
AVAIL, INFO = "getParkingPlaceAvailabilityInfoList", "getParkingPlaceInfoList"
TIMEOUT, RETRY, INTERVAL = 60, 5, 300      # 안양 포털과 동일하게 5분

KEY = ""
for _l in (ROOT/".env").read_text(encoding="utf-8").splitlines():
    if _l.startswith("GITS_KEY="): KEY = _l.split("=", 1)[1].strip()
if not KEY: sys.exit("GITS_KEY 없음. .env 에 넣을 것.")

def call(op, **params):
    p = {"serviceKey": KEY, **{k: v for k, v in params.items() if v}}
    last = None
    for a in range(RETRY):
        try:
            r = requests.get(BASE + op, params=p, timeout=TIMEOUT)
            root = ET.fromstring(r.text)
            cd = (root.findtext("msgHeader/headerCd") or "?").strip()
            if cd != "0":
                raise RuntimeError(f"headerCd={cd} {root.findtext('msgHeader/headerMsg')}")
            body = root.find("msgBody")
            its = body.findall("itemList") if body is not None else []
            return pd.DataFrame([{c.tag: c.text for c in it} for it in its])
        except Exception as e:
            last = e; time.sleep(3 * (a + 1))
    raise last

def probe(**_):
    av, inf = call(AVAIL), call(INFO)
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
    d = call(INFO); f = RAW/"gits_info.csv"
    d.to_csv(f, index=False)
    print(f"기본정보 {len(d):,}곳 → {f.name}")

def rt_test(wait=600, **_):
    a = call(AVAIL); print(f"1차 {len(a)}곳 {time.strftime('%H:%M:%S')}")
    time.sleep(wait)
    b = call(AVAIL); print(f"2차 {len(b)}곳 {time.strftime('%H:%M:%S')}")
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
    try:
        d = call(INFO)
        c.executemany("INSERT OR IGNORE INTO gits_lots VALUES ("+",".join("?"*23)+")",
            [(r.laeId, r.laeNm, r.pkplcId, r.pkplcNm, r.pkplcDivNm, r.pkplcTypeNm,
              r.latCrdn, r.lonCrdn, r.roadNmAddr, r.pklotCnt,
              r.wkdayOprtStartTime, r.wkdayOprtEndTime, r.satOprtStartTime, r.satOprtEndTime,
              r.hldyOprtStartTime, r.hldyOprtEndTime, r.parkingBscTime, r.parkingBscFare,
              r.addUnitTime, r.addUnitFare, r.ddPktckFare, r.mmCmmtktFare,
              datetime.now(KST).isoformat()) for r in d.itertuples()])
        c.commit(); print(f"lots {len(d):,}곳 갱신", flush=True)
    except Exception as e:
        print(f"기본정보 실패(계속): {str(e)[:120]}", flush=True)
    while True:
        ts = datetime.now(KST).isoformat()
        try:
            a = call(AVAIL)
            c.executemany("INSERT OR IGNORE INTO gits_obs VALUES (?,?,?,?,?,?)",
                [(ts, r.laeId, r.pkplcId, r.pklotCnt, r.avblPklotCnt, r.ocrnDt)
                 for r in a.itertuples()])
            c.commit()
            n = c.execute("SELECT COUNT(*) FROM gits_obs").fetchone()[0]
            print(f"{ts[:19]} +{len(a)}행 (누적 {n:,})", flush=True)
        except Exception as e:
            print(f"{ts[:19]} 실패: {str(e)[:120]}", flush=True)
        if once: break
        time.sleep(INTERVAL)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="probe",
                    choices=["probe","info","rt-test","poll"])
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    {"probe":probe, "info":info, "rt-test":rt_test, "poll":poll}[a.cmd](once=a.once)
