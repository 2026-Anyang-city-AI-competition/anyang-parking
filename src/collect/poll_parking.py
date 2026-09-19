#!/usr/bin/env python3
"""
안양시 통합주차포털 실시간 주차 점유율 수집기
  python3 src/collect/poll_parking.py            # 5분 간격 상시 수집
  python3 src/collect/poll_parking.py --analyze  # 계측 불가(값 고정) 주차장 진단
  python3 src/collect/poll_parking.py --export out.csv
  python3 src/collect/poll_parking.py --once     # 1회만 (CI용)
"""
import argparse, csv, json, sqlite3, sys, time, urllib.request
from datetime import datetime, timezone, timedelta

API = "https://parking.auc.or.kr/api/parking/searchParkingList"
DB = "data/raw/parking.db"
INTERVAL = 300          # 5분. 이보다 짧게 하지 말 것
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (research; anyang-parking-study)"


def db_init():
    c = sqlite3.connect(DB)
    # ★ WAL 이어야 폴링이 쓰는 동안에도 API 가 읽을 수 있다. 기본 delete 모드에서는
    #   쓰기가 읽기를 막아 추천 요청이 대기한다. 한 번 켜면 파일에 남는다.
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS lots(
        parking_id INTEGER PRIMARY KEY, name TEXT, div TEXT, gu TEXT,
        gubun TEXT, cell_cnt INT, hndcap_cnt INT, elec_cnt INT,
        lat REAL, lng REAL, grade INT, free_yn TEXT,
        wdays_start TEXT, wdays_end TEXT, wend_start TEXT, wend_end TEXT,
        oneday_amt INT, dflt_amt INT, dflt_tm INT, addr TEXT, first_seen TEXT);
    CREATE TABLE IF NOT EXISTS obs(
        ts_kst TEXT, parking_id INTEGER, cell_cnt INT, park_count INT,
        PRIMARY KEY(ts_kst, parking_id)) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS idx_obs_lot ON obs(parking_id, ts_kst);
    """)
    c.commit()
    return c


def fetch():
    req = urllib.request.Request(API, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))["parkingList"]


def poll_once(conn):
    lots = fetch()
    ts = datetime.now(KST).replace(microsecond=0).isoformat()
    conn.executemany("""INSERT OR IGNORE INTO lots VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(o["PARKING_ID"], o["PARKING_NM"], o["PARKING_DIV_NM"],
          o.get("REGION_DIV_NM"), o.get("PARKING_GUBUN"), o["CELL_CNT"],
          o.get("HNDCAP_CELL_CNT"), o.get("ELEC_CELL_CNT"),
          float(o["LAT"]) if o.get("LAT") else None,
          float(o["LNG"]) if o.get("LNG") else None,
          o.get("GRADE"), o.get("FREE_YN"),
          o.get("WDAYS_START_TM"), o.get("WDAYS_END_TM"),
          o.get("WEND_START_TM"), o.get("WEND_END_TM"),
          o.get("ONEDAY_AMT"), o.get("DFLT_AMT"), o.get("DFLT_TM"),
          o.get("CELL_ADDR_LOAD") or o.get("ADDR"), ts) for o in lots])
    conn.executemany("INSERT OR IGNORE INTO obs VALUES (?,?,?,?)",
        [(ts, o["PARKING_ID"], o["CELL_CNT"], o["PARK_COUNT"]) for o in lots])
    conn.commit()
    return ts, len(lots)


def run(conn):
    fails = 0
    print(f"[start] {INTERVAL}s 간격 수집. Ctrl+C로 중단. DB={DB}", flush=True)
    while True:
        t0 = time.time()
        try:
            ts, n = poll_once(conn)
            total = conn.execute("SELECT COUNT(*) FROM obs").fetchone()[0]
            print(f"[ok] {ts}  lots={n}  누적={total:,}", flush=True)
            fails = 0
        except Exception as e:
            fails += 1
            print(f"[err] {datetime.now(KST):%H:%M:%S} {type(e).__name__}: {e} "
                  f"(연속 {fails})", file=sys.stderr, flush=True)
            if fails >= 3:
                time.sleep(min(600, 30 * 2 ** (fails - 3)))
        time.sleep(max(0, INTERVAL - (time.time() - t0)))


def analyze(conn):
    rows = conn.execute("""
        SELECT l.parking_id, l.name, l.div, l.cell_cnt,
               COUNT(*) n, COUNT(DISTINCT o.park_count) uniq,
               MIN(o.park_count) lo, MAX(o.park_count) hi,
               ROUND(AVG(1.0*o.park_count/NULLIF(o.cell_cnt,0)),3) occ
        FROM obs o JOIN lots l USING(parking_id)
        GROUP BY l.parking_id ORDER BY uniq, l.div""").fetchall()
    if not rows or rows[0][4] < 2:
        return print("관측이 부족해. 최소 몇 시간은 모은 뒤 다시 실행해.")
    dead = [r for r in rows if r[5] <= 1]
    live = [r for r in rows if r[5] > 1]
    print(f"\n관측 {rows[0][4]}회 기준")
    print(f"  라벨 사용 가능(값이 변함): {len(live)}곳")
    print(f"  계측 불가(값 고정)      : {len(dead)}곳\n")
    print("── 계측 불가 목록 (학습에서 제외할 것) ──")
    for r in dead:
        print(f"  {r[1]:<22} {r[2]:<4} {r[3]:>4}면  고정값={r[6]}")
    print("\n── 라벨 사용 가능 (점유율 변동폭 순) ──")
    for r in sorted(live, key=lambda x: -(x[7]-x[6])/max(x[3],1))[:25]:
        print(f"  {r[1]:<22} {r[2]:<4} {r[3]:>4}면  "
              f"범위={r[6]}~{r[7]}  평균점유율={r[8]}")


def export(conn, path):
    cur = conn.execute("""
        SELECT o.ts_kst, l.parking_id, l.name, l.div, l.gu, l.grade, l.free_yn,
               l.lat, l.lng, l.wdays_start, l.wdays_end, l.oneday_amt,
               o.cell_cnt, o.park_count,
               ROUND(1.0*o.park_count/NULLIF(o.cell_cnt,0),4) occ
        FROM obs o JOIN lots l USING(parking_id) ORDER BY o.ts_kst, l.parking_id""")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([d[0] for d in cur.description])
        w.writerows(cur)
    print(f"내보냄: {path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--analyze", action="store_true")
    p.add_argument("--export", metavar="CSV")
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    conn = db_init()
    if a.analyze:   analyze(conn)
    elif a.export:  export(conn, a.export)
    elif a.once:
        ts, n = poll_once(conn)
        print(f"[once] {ts} lots={n}")
    else:           run(conn)
