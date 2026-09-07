#!/usr/bin/env python3
"""Δ 분위 회귀 + 직접 만차 이진분류 + validation 기반 라우터."""
import json, pickle, sys, warnings
from datetime import timedelta, timezone
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.features.temporal import oprtime_features, OPR_COLS
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "data/processed/predictor.pkl"
ROUTER_PATH = ROOT / "data/processed/router.json"


def _logodds(p, eps=1e-6):
    """★ Platt scaling 은 확률이 아니라 **log-odds** 에 fit 한다.
    확률값에 직접 fit 하면 극단값이 [0,1] 양 끝에 뭉쳐 로지스틱이 꼬리를 못 편다."""
    q = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return np.log(q / (1 - q))
KST = timezone(timedelta(hours=9))
FULL_THRESHOLD, QUANTILES, HORIZ_GRID = 90.0, (.1, .5, .9), (15, 30, 60, 120)

def _lazy():
    from src.analysis.a16_stratified_weekend import load, series, feats, PID, INTX
    return load, series, feats, PID, INTX

class Predictor:
    """`full_prob`는 분위 보간이 아닌 직접 binary 모델의 연속 확률이다."""
    def __init__(self, path=MODEL_PATH):
        self.ok = False
        try:
            with open(path, "rb") as f: b = pickle.load(f)
            self.models, self.full_models = b["models"], b["full_models"]
            self.full_calibrators = b.get("full_calibrators", {})
            self.feat_cols, self.dead, self.hist = b["feat_cols"], set(b["dead"]), b["hist"]
            self.meta, self.router = b.get("meta", {}), b.get("router", {})
            self.cqr = b.get("cqr", {})
            self.ok = True
        except Exception as e:
            print(f"[predictor] 모델 없음 ({str(e)[:60]}) — predict 는 None 을 돌려준다")

    def is_live(self, parking_id): return parking_id not in self.dead

    def _row(self, parking_id, target_time, h):
        hist = self.hist.get(parking_id)
        if hist is None or not len(hist): return None, None
        base = pd.Timestamp(target_time).tz_localize(None) - pd.Timedelta(minutes=h)
        i = hist.ts_kst.searchsorted(base, side="right") - 1
        if i < 0 or pd.isna(hist.iloc[i].get("occ_now")): return None, None
        return hist.iloc[i], float(hist.iloc[i]["occ_now"])

    def predict(self, parking_id, target_time, horizon_min):
        out = {"p10": None, "p50": None, "p90": None, "full_prob": None,
               "is_live": self.is_live(parking_id), "source": None}
        if not self.ok or not out["is_live"]:
            out["source"] = "dead_feed" if not out["is_live"] else "no_model"; return out
        h = min(HORIZ_GRID, key=lambda x: abs(x-horizon_min)); r, occ = self._row(parking_id, target_time, h)
        if r is None: out["source"]="no_history"; return out
        row = r[[c for c in self.feat_cols if not c.startswith("opr_")]].to_dict()
        opr = oprtime_features(self.meta.get(parking_id, {}), pd.Timestamp(target_time).to_pydatetime())
        row.update({"opr_"+k:v for k,v in opr.items()}); X = pd.DataFrame([row])[self.feat_cols]
        for c in ("parking_id_cat", "pid_we"):
            if c in X: X[c] = X[c].astype("category")
        q = {a: float(np.clip(occ+self.models[(h,a)].predict(X)[0], 0, 120)) for a in QUANTILES}
        p10,p50,p90 = sorted((q[.1],q[.5],q[.9]))
        _tt = pd.Timestamp(target_time)
        state = ("operating" if opr["is_operating"] else "outside") + \
                ("|we" if _tt.weekday() >= 5 else "|wd")
        # ★ CQR 폭 — val 에서 구한 값. 이걸 빼면 80% 구간이 아니다.
        adj = float(self.cqr.get(f"{h}|{state}", 0.0))
        p10, p90 = max(0.0, p10-adj), min(120.0, p90+adj)
        chosen = self.router.get(str(h), {}).get(state, "ml")
        if chosen == "persistence": p10=p50=p90=occ
        raw_prob = self.full_models[h].predict_proba(X)[:,1]
        calibrator = self.full_calibrators.get(h)
        prob = float(calibrator.predict_proba(_logodds(raw_prob).reshape(-1,1))[0,1]
                     if calibrator else raw_prob[0])
        # API에는 확률 정밀도를 남긴다. UI가 표시 자릿수를 정한다.
        out.update(p10=round(p10,1), p50=round(p50,1), p90=round(p90,1), full_prob=round(prob,6),
                   source=f"{chosen}_h{h}+binary_full_prob")
        return out

def _add_opr(sub, h, meta):
    rows = [oprtime_features(meta.get(pid, {}), (pd.Timestamp(t)+pd.Timedelta(minutes=h)).to_pydatetime())
            for pid,t in zip(sub.parking_id.values,sub.ts_kst.values)]
    return pd.concat([sub, pd.DataFrame(rows,index=sub.index).rename(columns=lambda c:"opr_"+c)],axis=1)

def train(save=True):
    from lightgbm import LGBMClassifier, LGBMRegressor
    from sklearn.metrics import brier_score_loss, mean_absolute_error
    from sklearn.linear_model import LogisticRegression
    load,series,feats,PID,INTX = _lazy(); o,L,dead=load(); d=feats(series(o),L)
    ut=d.ts_kst.dropna().sort_values().unique(); cut=pd.Timestamp(ut[int(len(ut)*.8)]); vcut=pd.Timestamp(ut[int(len(ut)*.64)])
    meta={r["parking_id"]:r for r in L.to_dict("records")}; F=INTX+["opr_"+c for c in OPR_COLS]
    models={}; full_models={}; full_calibrators={}; router={}; rep=[]; cov=[]; cqr={}
    for h in HORIZ_GRID:
        t=d.copy(); t["nx"]=t.groupby("parking_id").occ.shift(-(h//5)); t["y"]=t.nx-t.occ
        t=t.dropna(subset=["nx"]+[c for c in INTX if c not in ("parking_id_cat","pid_we")]); t=_add_opr(t,h,meta)
        fit=t[(t.ts_kst<cut)&~t.parking_id.isin(dead)]; val=fit[fit.ts_kst>=vcut]
        base=fit[fit.ts_kst<vcut]                      # ★ val 을 뺀 학습 구간
        for a in QUANTILES:
            m=LGBMRegressor(objective="quantile",alpha=a,n_estimators=120,learning_rate=.08,num_leaves=31,min_child_samples=40,random_state=42,n_jobs=-1,verbose=-1)
            m.fit(fit[F],fit.y); models[(h,a)]=m
        # ★ 라우터 선택에 models[(h,.5)] 를 쓰면 안 된다 — val 을 포함해 학습했으므로
        #   in-sample 예측이고, ML 이 이기는 게 당연해진다(2.221 vs test 9.378).
        #   선택 전용 모델을 base 로만 학습해 out-of-sample 로 고른다.
        sel=LGBMRegressor(objective="quantile",alpha=.5,n_estimators=400,learning_rate=.05,
                          num_leaves=63,min_child_samples=40,random_state=42,n_jobs=-1,
                          verbose=-1).fit(base[F],base.y)
        pm=np.clip(val.occ_now.values+sel.predict(val[F]),0,120); choices={}
        # ★ U8-3 · CQR — val(모델이 안 본 구간)에서 구간을 넓힐 폭을 구한다.
        #   상수를 손으로 맞추는 게 아니라 「목표 커버리지를 만족하는 분위수」를 읽는 것이다.
        slo=LGBMRegressor(objective="quantile",alpha=.1,n_estimators=400,learning_rate=.05,
              num_leaves=63,min_child_samples=40,random_state=42,n_jobs=-1,verbose=-1).fit(base[F],base.y)
        shi=LGBMRegressor(objective="quantile",alpha=.9,n_estimators=400,learning_rate=.05,
              num_leaves=63,min_child_samples=40,random_state=42,n_jobs=-1,verbose=-1).fit(base[F],base.y)
        vlo=np.clip(val.occ_now.values+slo.predict(val[F]),0,120)
        vhi=np.clip(val.occ_now.values+shi.predict(val[F]),0,120)
        vlo,vhi=np.minimum(vlo,vhi),np.maximum(vlo,vhi)
        vy_=val.nx.values
        vop=val.opr_is_operating.eq(1).values
        _we=val.is_weekend.eq(1).values
        for st,mk in (("operating|wd",vop&~_we),("operating|we",vop&_we),
                      ("outside|wd",(~vop)&~_we),("outside|we",(~vop)&_we)):
            if mk.sum()<50: cqr[(h,st)]=0.0; continue
            sc=np.maximum(vlo[mk]-vy_[mk], vy_[mk]-vhi[mk])      # CQR 비적합 점수
            n=len(sc); lvl=min(1.0,np.ceil((n+1)*0.80)/n)
            cqr[(h,st)]=float(max(0.0,np.quantile(sc,lvl)))
        # ★ base 는 주말 0% · val 50.5% · test 100% 다. 운영여부만으로 고르면
        #   평일에서 고른 선택이 주말 test 에 그대로 적용돼 뒤집힌다(실측: 라우팅 3.338 vs persistence 3.213).
        #   배포 조건과 맞추려면 주말 여부까지 층화해 고른다.
        _vop=val.opr_is_operating.eq(1).values; _vwe=val.is_weekend.eq(1).values
        for state,mask in (("operating|wd",_vop&~_vwe),("operating|we",_vop&_vwe),
                           ("outside|wd",(~_vop)&~_vwe),("outside|we",(~_vop)&_vwe)):
            ml=mean_absolute_error(val.nx.values[mask],pm[mask]) if mask.any() else np.inf
            pe=mean_absolute_error(val.nx.values[mask],val.occ_now.values[mask]) if mask.any() else np.inf
            # ★ 「평균이 더 낮다」만으로 고르면 마진 2% 짜리 노이즈를 고른다.
            #   실측: 그렇게 고른 라우터가 test 에서 persistence 에 +3.2% 졌다.
            #   대응표본 차이의 95% 신뢰구간이 0보다 확실히 작을 때만 ML 을 쓴다.
            #   동률이면 단순한 쪽(persistence)을 남긴다.
            sig=False
            if mask.sum()>=50:
                dif=(np.abs(pm[mask]-val.nx.values[mask])
                     -np.abs(val.occ_now.values[mask]-val.nx.values[mask]))
                se=dif.std(ddof=1)/np.sqrt(len(dif))
                sig=(dif.mean()+1.96*se)<0            # 상한이 0 미만 = 유의하게 우세
            choices[state]="ml" if sig else "persistence"
            rep.append({"horizon":h,"segment":state,"n":int(mask.sum()),"ml_mae":ml,"persistence_mae":pe,"selected":choices[state]})
        router[str(h)]=choices
        # 연속적인 Platt calibration. isotonic처럼 소수의 계단값으로 붕괴하지 않는다.
        clf=LGBMClassifier(objective="binary",n_estimators=120,learning_rate=.08,num_leaves=31,min_child_samples=45,random_state=42,n_jobs=-1,verbose=-1)
        clf.fit(base[F],(base.nx>=FULL_THRESHOLD).astype(int)); full_models[h]=clf
        vp=clf.predict_proba(val[F])[:,1]; vy=(val.nx>=FULL_THRESHOLD).astype(int).values
        calibrator=LogisticRegression(C=1.0, max_iter=500).fit(_logodds(vp).reshape(-1,1),vy); full_calibrators[h]=calibrator
        # ★ U8-3 · 80% 구간 커버리지를 **test**(out-of-sample) 에서 잰다.
        te_=t[(t.ts_kst>=cut)&~t.parking_id.isin(dead)]
        if len(te_):
            lo=np.clip(te_.occ_now.values+models[(h,.1)].predict(te_[F]),0,120)
            hi=np.clip(te_.occ_now.values+models[(h,.9)].predict(te_[F]),0,120)
            lo,hi=np.minimum(lo,hi),np.maximum(lo,hi)
            op=te_.opr_is_operating.eq(1).values
            _twe=te_.is_weekend.eq(1).values
            adj=np.array([cqr.get((h,("operating" if a_ else "outside")+("|we" if b_ else "|wd")),0.0)
                          for a_,b_ in zip(op,_twe)])
            lo=np.clip(lo-adj,0,120); hi=np.clip(hi+adj,0,120)     # ★ CQR 적용
            inside=(te_.nx.values>=lo)&(te_.nx.values<=hi); we=te_.is_weekend.eq(1).values
            for sn,mk in (("전체",np.ones(len(te_),bool)),("운영중",op),("운영외",~op),
                          ("평일",~we),("주말",we),("운영중·평일",op&~we),("운영중·주말",op&we),
                          ("운영외·평일",(~op)&~we),("운영외·주말",(~op)&we)):
                if mk.sum()>30:
                    cov.append({"horizon":h,"segment":sn,"n":int(mk.sum()),
                                "coverage":float(inside[mk].mean()),
                                "width":float((hi-lo)[mk].mean())})
        test=t[(t.ts_kst>=cut)&~t.parking_id.isin(dead)]; y=(test.nx>=FULL_THRESHOLD).astype(int).values; p=calibrator.predict_proba(_logodds(clf.predict_proba(test[F])[:,1]).reshape(-1,1))[:,1]
        rep[-1].update(test_n=len(test),brier_binary=brier_score_loss(y,p),brier_persistence=brier_score_loss(y,(test.occ_now.values>=FULL_THRESHOLD).astype(float)),prob=p,y=y)
    hist={pid:g.sort_values("ts_kst").tail(600).reset_index(drop=True) for pid,g in d.groupby("parking_id")}
    if save:
        MODEL_PATH.parent.mkdir(parents=True,exist_ok=True)
        with open(MODEL_PATH,"wb") as f: pickle.dump({"models":models,"full_models":full_models,"full_calibrators":full_calibrators,"feat_cols":F,"meta":meta,"dead":list(dead),"hist":hist,"router":router,"cqr":{f"{k[0]}|{k[1]}":v for k,v in cqr.items()}},f)
        ROUTER_PATH.write_text(json.dumps(router,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return rep,cut,dead,cov

def _seg(x):
    a,_,b = x.partition("|")
    return ("운영 중" if a=="operating" else "운영 외") + (" · 주말" if b=="we" else " · 평일")


def _write_reports(rep, cov=()):
    rows=[r for r in rep if "ml_mae" in r]; out=["# U3 검증 기반 라우터","","validation은 시간순 train 영역의 뒤 20%이며 test는 선택에 쓰지 않았다.",
        "선택용 모델은 **val을 제외한 base로만** 학습한다(U8-2). val을 포함해 학습하면",
        "in-sample 예측으로 고르게 되어 ML이 항상 이긴다.",
        "base는 주말 0% · val 50.5% · test 100% 라 **주말/평일까지 층화**해 고른다.","","| horizon | 구간 | n | ML MAE | persistence MAE | 선택 |","|---:|---|---:|---:|---:|---|"]
    for r in rows: out.append(f"| {r['horizon']} | {_seg(r['segment'])} | {r['n']:,} | {r['ml_mae']:.3f} | {r['persistence_mae']:.3f} | {r['selected']} |")
    (ROOT/"reports/tables/u3_router.md").write_text("\n".join(out)+"\n",encoding="utf-8")
    if cov:
        cv=["# U8-3 · 분위 구간 커버리지 (test · out-of-sample)","",
            "`coverage = mean(p10 <= 실제값 <= p90)` · 목표 **0.80**","",
            "| horizon | 구간 | n | 커버리지 | 평균 폭(%p) |","|---:|---|---:|---:|---:|"]
        for r in cov:
            cv.append(f"| {r['horizon']} | {r['segment']} | {r['n']:,} | "
                      f"{r['coverage']:.3f} | {r['width']:.1f} |")
        (ROOT/"reports/tables/u8_coverage.md").write_text("\n".join(cv)+"\n",encoding="utf-8")

if __name__ == "__main__":
    rep,cut,dead,cov=train(); _write_reports(rep,cov)
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    byh={h:next(r for r in reversed(rep) if r["horizon"]==h and "prob" in r) for h in HORIZ_GRID}; fig,axes=plt.subplots(1,4,figsize=(16,3.8))
    for ax,h in zip(axes,HORIZ_GRID):
        r=byh[h]; p,y=r["prob"],r["y"]; ix=np.digitize(p,np.linspace(0,1,11),right=True)-1; xs=[];ys=[]
        for b in range(10):
            m=ix==b
            if m.any():
                x,yy=p[m].mean(),y[m].mean();xs.append(x);ys.append(yy);ax.annotate(f"n={m.sum()}",(x,yy),fontsize=7)
        ax.plot([0,1],[0,1],"--",color="gray");ax.plot(xs,ys,"o-",color="tab:blue");ax.set(title=f"h={h} Brier {r['brier_binary']:.3f}\nPersistence {r['brier_persistence']:.3f}",xlabel="예측 만차확률",ylabel="실제 비율");ax.set(xlim=(0,1),ylim=(0,1))
    fig.tight_layout();fig.savefig(ROOT/"reports/figures/calibration.png",dpi=150)
    print(f"학습 완료 · test cut {cut} · 죽은 피드 {len(dead)}곳 제외")
    for h,r in byh.items():print(f"h={h}: Brier binary={r['brier_binary']:.4f}, persistence={r['brier_persistence']:.4f}")
    print("→ reports/figures/calibration.png · reports/tables/u3_router.md")
