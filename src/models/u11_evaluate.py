"""재현 가능한 U11 평가. 실행: python -m src.models.u11_evaluate

학습 / 하루 보정 / 하루 test; 경계는 라벨 시각으로 purge.
하이퍼파라미터·0.5 컷오프·CQR 0.80은 test 이전에 고정한다.
"""
import hashlib
import json
import pickle
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.linear_model import LogisticRegression

from src.analysis.a16_stratified_weekend import feats, INTX
from src.features.temporal import oprtime_features, OPR_COLS
from src.serve.candidates import find_candidates
from src.serve.fare import calc_fare, resolve_type
from src.serve.ranking import rank_cards
from src.serve.walking import CACHE, _grid

ROOT = Path(__file__).resolve().parents[2]
TAB = ROOT / "reports/tables"
OUT = ROOT / "data/processed/u11"
HORIZONS = (15, 30, 60, 120)
FEATURES = INTX + ["opr_" + c for c in OPR_COLS]
PARAMS = dict(n_estimators=120, learning_rate=.08, num_leaves=31,
              min_child_samples=40, random_state=42, n_jobs=4, verbosity=-1)
DESTS = [("안양시청", 37.394259, 126.956861), ("석수역", 37.435093, 126.902321),
         ("범계역", 37.389784, 126.950783), ("안양역", 37.401857, 126.922644),
         ("평촌역", 37.394240, 126.963808), ("인덕원역", 37.401494, 126.976680)]


def logodds(p):
    p = np.clip(p, 1e-6, 1-1e-6)
    return np.log(p / (1-p)).reshape(-1, 1)


def load_snapshot():
    """DB의 같은 읽기 트랜잭션에서 메타/관측을 읽고 스냅샷을 보존한다."""
    with sqlite3.connect(f"file:{ROOT / 'data/raw/parking.db'}?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        obs = pd.read_sql_query("SELECT parking_id,ts_kst,park_count,cell_cnt FROM obs", db)
        lots = pd.read_sql_query("SELECT * FROM lots", db)
    OUT.mkdir(parents=True, exist_ok=True)
    obs.to_parquet(OUT / "observations.parquet", index=False)
    lots.to_parquet(OUT / "lots.parquet", index=False)
    stamp = hashlib.sha256(pd.util.hash_pandas_object(obs, index=False).values.tobytes()).hexdigest()
    obs["ts_kst"] = pd.to_datetime(obs.ts_kst, format="mixed").dt.tz_localize(None)
    info = dict(raw_n=len(obs), lots=len(lots), start=str(obs.ts_kst.min()),
                end=str(obs.ts_kst.max()), snapshot_hash=stamp)
    obs = obs[(obs.cell_cnt > 0) & (obs.park_count >= 0)].copy()
    obs["occ"] = (100*obs.park_count/obs.cell_cnt).clip(0, 120)
    # 관측을 다음 5분 격자에서 이용 가능하도록 배치한다. 미래 보간·역방향 채움 없음.
    obs["ts_kst"] = obs.ts_kst.dt.ceil("5min")
    parts = []
    for pid, g in obs.groupby("parking_id"):
        s = g.groupby("ts_kst").occ.last().asfreq("5min").rename("occ").reset_index()
        s["parking_id"] = pid
        parts.append(s)
    d = feats(pd.concat(parts, ignore_index=True), lots)
    return d, lots, info


def horizon_frame(d, h, meta):
    t = d.copy()
    t["target_time"] = t.ts_kst + pd.Timedelta(minutes=h)
    t["nx"] = t.groupby("parking_id").occ.shift(-(h//5))
    t["y"] = t.nx - t.occ_now
    cache = {}
    rows = []
    for pid, target in zip(t.parking_id, t.target_time):
        key = (pid, target.dayofweek, target.hour, target.minute)
        if key not in cache:
            cache[key] = oprtime_features(meta[pid], target.to_pydatetime())
        rows.append(cache[key])
    op = pd.DataFrame(rows, index=t.index).add_prefix("opr_")
    t = pd.concat([t, op], axis=1)
    t["state"] = np.where(t.opr_is_operating.eq(1), "operating", "outside") + np.where(
        t.target_time.dt.dayofweek.ge(5), "|we", "|wd")
    return t


def split_frame(t, start):
    cal_start = start - pd.Timedelta(days=1)
    valid = t.dropna(subset=FEATURES + ["nx"])
    train = valid[valid.target_time < cal_start].copy()
    cal = valid[(valid.ts_kst >= cal_start) & (valid.target_time < start)].copy()
    test = valid[(valid.ts_kst >= start) & (valid.target_time < start+pd.Timedelta(days=1))].copy()
    assert train.target_time.max() < cal.ts_kst.min()
    assert cal.target_time.max() < test.ts_kst.min()
    return train, cal, test


def fit_models(train, cal):
    models = {a: LGBMRegressor(objective="quantile", alpha=a, **PARAMS).fit(train[FEATURES], train.y)
              for a in (.1, .5, .9)}
    clf = LGBMClassifier(objective="binary", **PARAMS).fit(train[FEATURES], train.nx.ge(90).astype(int))
    platt = None
    if cal.nx.ge(90).nunique() == 2:
        platt = LogisticRegression(C=1, max_iter=500).fit(
            logodds(clf.predict_proba(cal[FEATURES])[:, 1]), cal.nx.ge(90).astype(int))
    q = quantiles(models, cal)
    adjustments = {}
    # 없는 주말 집단을 0으로 보정했다고 주장하지 않는다. 미검증은 None.
    for state in ("operating|wd", "operating|we", "outside|wd", "outside|we"):
        mask = cal.state.eq(state).to_numpy(dtype=bool)
        if mask.sum() < 50:
            adjustments[state] = None
            continue
        scores = np.maximum(q[0, mask]-cal.nx.values[mask], cal.nx.values[mask]-q[2, mask])
        level = min(1, np.ceil((len(scores)+1)*.8)/len(scores))
        # 음수 보정(축소)도 허용; 실제 CQR의 signed score를 그대로 사용한다.
        adjustments[state] = float(np.quantile(scores, level, method="higher"))
    return models, clf, platt, adjustments


def quantiles(models, frame):
    return np.sort(np.array([np.clip(frame.occ_now.values+m.predict(frame[FEATURES]), 0, 120)
                             for m in models.values()]), axis=0)


def predict_frame(bundle, frame):
    models, clf, platt, adjustments = bundle
    q = quantiles(models, frame)
    p = clf.predict_proba(frame[FEATURES])[:, 1]
    if platt is not None:
        p = platt.predict_proba(logodds(p))[:, 1]
    out = frame[["parking_id", "ts_kst", "target_time", "occ_now", "nx", "state", "opr_is_operating"]].copy()
    adj = frame.state.map(adjustments)
    out["p10"] = np.clip(q[0]-adj, 0, 120)
    out["p90"] = np.clip(q[2]+adj, 0, 120)
    # 축소로 역전되면 허위 0폭 구간을 만들지 않고 평가불가로 처리한다.
    crossed = out.p10 > out.p90
    out.loc[crossed, ["p10", "p90"]] = np.nan
    out["p50"] = q[1]
    out["full_prob"] = p
    out["calibrated"] = platt is not None
    return out


def segment_masks(frame):
    op = frame.opr_is_operating.eq(1).to_numpy(dtype=bool)
    we = frame.target_time.dt.dayofweek.ge(5).to_numpy(dtype=bool)
    return {"전체": np.ones(len(frame), bool), "운영중": op, "운영외": ~op,
            "평일": ~we, "주말": we, "운영중·평일": op & ~we,
            "운영중·주말": op & we, "운영외·평일": ~op & ~we, "운영외·주말": ~op & we}


def coverage_rows(pred, fold, h):
    rows = []
    for name, mask in segment_masks(pred).items():
        assert mask.dtype == bool
        p = pred.loc[mask]
        valid = p.p10.notna() & p.p90.notna()
        n = len(p)
        inside = int(((p.nx >= p.p10) & (p.nx <= p.p90) & valid).sum())
        cov = inside/int(valid.sum()) if valid.any() else np.nan
        status = ("unavailable" if n == 0 or valid.sum() != n else
                  "pass" if .77 <= cov <= .83 else "fail")
        rows.append(dict(fold=fold, horizon=h, segment=name, n=n, n_interval=int(valid.sum()),
                         inside=inside, coverage=cov, avg_width=(p.p90-p.p10).mean(), status=status,
                         ml_mae=abs(p.nx-p.p50).mean(), persistence_mae=abs(p.nx-p.occ_now).mean()))
    return rows


def route_snapshot(lots):
    """TMAP 실측 캐시만 사용. 경로 없는 질의는 제외 사유로 기록한다."""
    routes = {}
    if CACHE.exists():
        with sqlite3.connect(f"file:{CACHE}?mode=ro", uri=True) as db:
            for row in db.execute("SELECT parking_id,glat,glon,duration,ts FROM walk WHERE ts != 'TEST'"):
                routes[(int(row[0]), row[1], row[2])] = (int(row[3]), row[4])
    return routes


def rank_queries(pred, lots, dead, routes, fold, h, start):
    rows, skips = [], []
    labeled = []
    for r in lots.to_dict("records"):
        if pd.notna(r.get("lat")) and pd.notna(r.get("lng")):
            labeled.append({**r, "dead_feed": r["parking_id"] in dead})
    for name, lat, lon in DESTS:
        near = find_candidates(lat, lon, labeled=labeled, unlabeled=[])["lots"]
        grid = _grid(lat, lon)
        for hour in range(0, 24, 2):
            ts = start + pd.Timedelta(hours=hour)
            snap = pred[pred.ts_kst.eq(ts)].set_index("parking_id")
            missing = [r["parking_id"] for r in near if r["parking_id"] not in snap.index]
            missing_routes = [r["parking_id"] for r in near if (r["parking_id"], *grid) not in routes]
            query = dict(fold=fold, horizon=h, dest=name, ts_kst=str(ts))
            if missing or missing_routes or len(near) < 2:
                skips.append({**query, "missing_observations":len(missing),
                              "missing_routes":len(missing_routes), "n_candidates":len(near)})
                continue
            cards = []
            target = ts + pd.Timedelta(minutes=h)
            for r in near:
                pid = r["parking_id"]; p = snap.loc[pid]
                fare = calc_fare({**r, "type":resolve_type(r["name"], r.get("div"))}, target.to_pydatetime(), 60)["total"]
                cards.append(dict(parking_id=pid, is_live=True, estimated=False,
                                  walk_min=round(routes[(pid,*grid)][0]/60), fare_payg=fare,
                                  occ_now=float(p.occ_now), full_prob=float(p.full_prob), actual=float(p.nx)))
            if any(c["fare_payg"] is None for c in cards):
                skips.append({**query,"missing_observations":0,"missing_routes":0,"n_candidates":len(near),"reason":"unknown_fare"})
                continue
            for axis in ("walk", "fare"):
                ranks = {m: rank_cards(cards, axis, mode=m) for m in ("A", "B", "C")}
                a,b,c = [ranks[m][0] for m in ("A","B","C")]
                fail_a,fail_b,fail_c = (x["actual"] >= 90 for x in (a,b,c))
                rows.append({**query, "target_time":str(target), "axis":axis,
                    "state":snap.loc[a["parking_id"],"state"], "n_candidates":len(cards),
                    "rank_a":json.dumps([x["parking_id"] for x in ranks["A"]]),
                    "rank_b":json.dumps([x["parking_id"] for x in ranks["B"]]),
                    "rank_c":json.dumps([x["parking_id"] for x in ranks["C"]]),
                    "changed":int(ranks["A"] != ranks["B"]), "top1_changed":int(a["parking_id"] != b["parking_id"]),
                    "a_fail":int(fail_a),"b_fail":int(fail_b),"c_fail":int(fail_c),
                    "rescued":int(fail_a and not fail_b), "harmed":int(not fail_a and fail_b),
                    "a_flagged":int(fail_a and a["full_prob"] > .5),
                    "extra_walk":b["walk_min"]-a["walk_min"],"extra_fare":b["fare_payg"]-a["fare_payg"],
                    "a_actual":a["actual"],"b_actual":b["actual"],"c_actual":c["actual"],
                    "a_prob":a["full_prob"],"b_prob":b["full_prob"]})
    return rows, skips


def run():
    TAB.mkdir(parents=True, exist_ok=True)
    d,lots,info = load_snapshot()
    print(json.dumps(info, ensure_ascii=False), flush=True)
    meta = {r["parking_id"]:r for r in lots.to_dict("records")}
    dates = sorted(d.ts_kst.dt.normalize().unique())[3:]
    frames = {h:horizon_frame(d,h,meta) for h in HORIZONS}
    routes = route_snapshot(lots)
    (OUT/"routes.json").write_text(json.dumps([{ "key":list(k),"duration":v[0],"cached_at":v[1]} for k,v in routes.items()], ensure_ascii=False))
    cov, queries, skips, audits, allpred = [],[],[],[],[]
    final_models,final_full,final_platt,final_cqr = {},{},{},{}
    for fold, day in enumerate(dates, 1):
        start = pd.Timestamp(day)
        prior = d[d.ts_kst < start-pd.Timedelta(days=1)]
        ranges = prior.groupby("parking_id").occ.agg(["min","max"])
        dead = set(ranges.index[(ranges["max"]-ranges["min"]) < 1])
        for h,t in frames.items():
            train,cal,test = split_frame(t,start)
            bundle = fit_models(train,cal)
            test = test[~test.parking_id.isin(dead)]
            pred = predict_frame(bundle,test)
            pred["fold"],pred["horizon"] = fold,h
            allpred.append(pred)
            cov.extend(coverage_rows(pred,fold,h))
            q,s = rank_queries(pred,lots,dead,routes,fold,h,start)
            queries.extend(q);skips.extend(s)
            audits.append(dict(fold=fold,horizon=h,train_n=len(train),cal_n=len(cal),test_n=len(test),
                train_target_max=str(train.target_time.max()),cal_start=str(cal.ts_kst.min()),
                cal_target_max=str(cal.target_time.max()),test_start=str(test.ts_kst.min()),
                test_end=str(test.target_time.max()),live_lots=test.parking_id.nunique(),dead_lots=len(dead),
                partial_day=info["end"][:10] == str(start.date()),model_id=f"fold{fold}_h{h}"))
            print(f"fold={fold} {start.date()} h={h}: train={len(train)} cal={len(cal)} test={len(test)} queries={len(q)}",flush=True)
            if fold == len(dates):
                models,clf,platt,adj = bundle
                final_models.update({(h,a):m for a,m in models.items()})
                final_full[h],final_platt[h] = clf,platt
                final_cqr.update({f"{h}|{s}":v for s,v in adj.items()})
    pd.DataFrame(cov).to_csv(TAB/"u11_coverage.csv",index=False)
    pd.DataFrame(queries).to_csv(TAB/"u11_rank_queries.csv",index=False)
    pd.DataFrame(skips).to_csv(TAB/"u11_rank_exclusions.csv",index=False)
    pd.DataFrame(audits).to_csv(TAB/"u11_splits.csv",index=False)
    pd.concat(allpred,ignore_index=True).to_parquet(OUT/"predictions.parquet",index=False)
    (TAB/"u11_manifest.json").write_text(json.dumps(info,ensure_ascii=False,indent=2)+"\n")
    # 최신 fold의 검증된 동일 모델을 배포한다. 보정 뒤 재학습하지 않는다.
    from src.report.u11_report import generate
    gate = generate()
    bundle = dict(models=final_models,full_models=final_full,full_calibrators=final_platt,
                  cqr=final_cqr,feat_cols=FEATURES,meta=meta,dead=list(dead),
                  hist={pid:g.tail(3000).reset_index(drop=True) for pid,g in d.groupby("parking_id")},
                  interval_gate=gate,model_version=info["snapshot_hash"],evaluation="u11")
    model_path = ROOT/"data/processed/predictor.pkl"
    if model_path.exists() and not (OUT/"predictor_before_u11.pkl").exists():
        import shutil
        shutil.copy2(model_path,OUT/"predictor_before_u11.pkl")
    with open(model_path,"wb") as f:
        pickle.dump(bundle,f)


def rerank():
    """고정 예측/관측 스냅샷으로 경로 캐시 보완 후 순위만 재평가한다."""
    predictions=pd.read_parquet(OUT/"predictions.parquet")
    lots=pd.read_parquet(OUT/"lots.parquet")
    obs=pd.read_parquet(OUT/"observations.parquet")
    obs["ts_kst"]=pd.to_datetime(obs.ts_kst,format="mixed").dt.tz_localize(None).dt.ceil("5min")
    obs=obs[(obs.cell_cnt>0)&(obs.park_count>=0)].copy()
    obs["occ"]=(100*obs.park_count/obs.cell_cnt).clip(0,120)
    routes=route_snapshot(lots)
    (OUT/"routes.json").write_text(json.dumps([{"key":list(k),"duration":v[0],"cached_at":v[1]} for k,v in routes.items()],ensure_ascii=False))
    rows=[];skips=[]
    for (fold,h),pred in predictions.groupby(["fold","horizon"]):
        start=pred.ts_kst.min().normalize()
        prior=obs[obs.ts_kst<start-pd.Timedelta(days=1)]
        ranges=prior.groupby("parking_id").occ.agg(["min","max"])
        dead=set(ranges.index[(ranges['max']-ranges['min'])<1])
        q,s=rank_queries(pred,lots,dead,routes,fold,h,start)
        rows.extend(q);skips.extend(s)
    pd.DataFrame(rows).to_csv(TAB/"u11_rank_queries.csv",index=False)
    pd.DataFrame(skips).to_csv(TAB/"u11_rank_exclusions.csv",index=False)
    from src.report.u11_report import generate
    generate()


if __name__ == "__main__":
    run()
