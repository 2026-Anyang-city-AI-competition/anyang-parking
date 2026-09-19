"""CSV만 읽어 U11 판정·표·문장을 생성하고 집계 불변식을 검사한다."""
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TAB = ROOT / "reports/tables"
STATE_NAMES = {"operating|wd":"운영중·평일", "operating|we":"운영중·주말",
               "outside|wd":"운영외·평일", "outside|we":"운영외·주말"}


def markdown_table(df):
    def fmt(v):
        if pd.isna(v):
            return "—"
        if isinstance(v, float):
            return f"{v:.6f}"
        return str(v)
    return "\n".join(["| " + " | ".join(df.columns) + " |",
                       "| " + " | ".join(["---"]*len(df.columns)) + " |"] +
                      ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False,name=None)])


def validate_coverage(cov):
    assert not cov.duplicated(["fold","horizon","segment"]).any()
    assert (cov.n >= cov.n_interval).all() and (cov.n_interval >= cov.inside).all()
    expected = np.where((cov.n == 0) | (cov.n_interval != cov.n), "unavailable",
                        np.where(cov.coverage.between(.77,.83), "pass", "fail"))
    assert (cov.status == expected).all(), "합격 상태와 원시 커버리지 불일치"
    for _, g in cov.groupby(["fold","horizon"]):
        s = g.set_index("segment")
        for parent, children in {"전체":["운영중","운영외"],
                "운영중":["운영중·평일","운영중·주말"],
                "운영외":["운영외·평일","운영외·주말"]}.items():
            for col in ("n","n_interval","inside"):
                assert s.loc[parent,col] == s.loc[children,col].sum(), f"{parent} {col} 합산 오류"
    known = cov.n_interval > 0
    assert np.allclose(cov.loc[known,"coverage"], cov.loc[known,"inside"]/cov.loc[known,"n_interval"])
    assert cov.loc[known,"avg_width"].between(0,120).all()


def rank_summary(q, keys):
    out = q.groupby(keys, dropna=False).agg(n=("changed","size"), changed=("changed","sum"),
        top1_changed=("top1_changed","sum"), a_fail=("a_fail","sum"),b_fail=("b_fail","sum"),
        c_fail=("c_fail","sum"),rescued=("rescued","sum"),harmed=("harmed","sum"),
        a_flagged=("a_flagged","sum"), extra_walk=("extra_walk","mean"),extra_fare=("extra_fare","mean")).reset_index()
    for c in ("changed","top1_changed","a_fail","b_fail","c_fail","harmed"):
        out[c+"_rate"] = out[c]/out.n
    out["rescue_rate"] = out.rescued/out.a_fail.replace(0,np.nan)
    out["ai_gain_pp"] = 100*(out.c_fail-out.b_fail)/out.n
    return out


def probability_report():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    p = pd.read_parquet(ROOT/"data/processed/u11/predictions.parquet")
    assert p.full_prob.between(0,1).all()
    fold_metrics=[]
    for (fold,h),g in p.groupby(["fold","horizon"]):
        y=g.nx.ge(90).astype(int).to_numpy();prob=g.full_prob.to_numpy()
        ix=np.minimum((prob*10).astype(int),9)
        points=[(prob[ix==b].mean(),y[ix==b].mean()) for b in range(10) if (ix==b).any()]
        slope=float(np.polyfit(*np.array(points).T,1)[0]) if len(points)>1 else np.nan
        fold_metrics.append(dict(fold=fold,horizon=h,n=len(g),brier=float(np.mean((prob-y)**2)),
            brier_persistence=float(np.mean((g.occ_now.ge(90).astype(int).values-y)**2)),
            reliability_slope=slope,slope_pass=bool(.9<=slope<=1.1)))
    pd.DataFrame(fold_metrics).to_csv(TAB/"u11_probability_by_fold.csv",index=False)
    rows=[]; bins=[]
    # ★ 지평선 수에 맞춰 칸을 만든다. 4로 고정하면 zip 이 나머지를 조용히 버린다
    #   (240·360 을 추가했을 때 실제로 표에서 사라졌다).
    groups=list(p.groupby("horizon"))
    fig,axes=plt.subplots(1,len(groups),figsize=(4*len(groups),4),squeeze=False)
    for ax,(h,g) in zip(axes[0],groups):
        y=g.nx.ge(90).astype(int).to_numpy(); prob=g.full_prob.to_numpy()
        ix=np.minimum((prob*10).astype(int),9)
        xs=[];ys=[]
        for b in range(10):
            m=ix==b; n=int(m.sum())
            x=float(prob[m].mean()) if n else np.nan; yy=float(y[m].mean()) if n else np.nan
            bins.append(dict(horizon=h,bin=b,n=n,predicted=x,observed=yy))
            ax.text(.02,.96-b*.052,f"{b/10:.1f}-{(b+1)/10:.1f}: n={n}",transform=ax.transAxes,fontsize=6)
            if n:xs.append(x);ys.append(yy)
        slope=float(np.polyfit(xs,ys,1)[0]) if len(xs)>1 else np.nan
        brier=float(np.mean((prob-y)**2)); baseline=float(np.mean((g.occ_now.ge(90).astype(int).values-y)**2))
        rows.append(dict(horizon=h,n=len(g),brier=brier,brier_persistence=baseline,
                         reliability_slope=slope,unique_prob=len(np.unique(prob.round(6)))))
        ax.plot([0,1],[0,1],"--",color="gray");ax.plot(xs,ys,"o-")
        ax.set(title=f"h={h}; n={len(g)}\nBrier={brier:.4f}; slope={slope:.3f}",xlim=(0,1),ylim=(0,1))
    fig.tight_layout(); fig.savefig(ROOT/"reports/figures/calibration.png",dpi=150);plt.close(fig)
    pd.DataFrame(rows).to_csv(TAB/"u11_probability.csv",index=False)
    pd.DataFrame(bins).to_csv(TAB/"u11_calibration_bins.csv",index=False)
    return pd.DataFrame(rows)


def generate():
    cov=pd.read_csv(TAB/"u11_coverage.csv");q=pd.read_csv(TAB/"u11_rank_queries.csv")
    splits=pd.read_csv(TAB/"u11_splits.csv");info=json.loads((TAB/"u11_manifest.json").read_text())
    validate_coverage(cov)
    assert (pd.to_datetime(splits.train_target_max)<pd.to_datetime(splits.cal_start)).all()
    assert (pd.to_datetime(splits.cal_target_max)<pd.to_datetime(splits.test_start)).all()
    assert not q.duplicated(["fold","horizon","dest","ts_kst","axis"]).any()
    assert (q.rescued <= q.a_fail).all() and (q.harmed <= 1-q.a_fail).all()
    assert (q.a_fail-q.rescued+q.harmed == q.b_fail).all()
    probs=probability_report()
    prob_folds=pd.read_csv(TAB/"u11_probability_by_fold.csv")
    counts=cov.status.value_counts();passed=int(counts.get("pass",0));failed=int(counts.get("fail",0));unknown=int(counts.get("unavailable",0))
    nonempty=cov[cov.n>0]
    # 게이트는 horizon×도착 요일×운영 상태별 모든 비어있지 않은 fold가 통과해야 연다.
    gate={}
    for h in sorted(cov.horizon.unique()):
        for state,segment in STATE_NAMES.items():
            g=nonempty[(nonempty.horizon==h)&(nonempty.segment==segment)]
            gate[f"{h}|{state}"]=bool(len(g) and g.status.eq("pass").all())
    (ROOT/"data/processed/interval_gate.json").write_text(json.dumps(gate,indent=2)+"\n")
    detail=rank_summary(q,["fold","horizon","dest","state","axis"])
    detail.to_csv(TAB/"u11_rank_sensitivity.csv",index=False)
    rank_summary(q,["dest","horizon","state","axis"]).to_csv(TAB/"u11_rank_by_segment.csv",index=False)
    summary=rank_summary(q,["axis"])
    summary.to_csv(TAB/"u11_rank_summary.csv",index=False)
    # 서로 겹치는 질의를 독립으로 보지 않고 fold(일자) 전체를 재표집한다.
    rng=np.random.default_rng(42);ci=[]
    for axis,g in q.groupby("axis"):
        days=g.groupby("fold").agg(n=("a_fail","size"),a=("a_fail","sum"),b=("b_fail","sum"),c=("c_fail","sum"),rescued=("rescued","sum"))
        draws=rng.integers(0,len(days),size=(2000,len(days)))
        samples=days.to_numpy()[draws].sum(axis=1)
        gain=100*(samples[:,3]-samples[:,2])/samples[:,0]
        ci.append(dict(axis=axis,independent_days=len(days),gain_pp_low=np.quantile(gain,.025),gain_pp_high=np.quantile(gain,.975)))
    pd.DataFrame(ci).to_csv(TAB/"u11_rank_day_bootstrap.csv",index=False)
    prob_pass=bool(prob_folds.slope_pass.all())
    status="complete" if nonempty.status.eq("pass").all() and prob_pass else "partial"
    lines=[f"# U11 · {status} — 평가 수정·강등 효과·커버리지", "",
        f"DB: {info['start']} ~ {info['end']} · 원시 {info['raw_n']:,}행 · {info['lots']}곳.",
        f"스냅샷 SHA256: `{info['snapshot_hash']}`.","",
        f"커버리지 전체 {len(cov)}칸: 통과 {passed}, 미달 {failed}, 평가불가 {unknown}.",
        f"관측이 있는 칸 {len(nonempty)}개 중 통과 {int(nonempty.status.eq('pass').sum())}개. 빈 요일 칸도 삭제하지 않고 평가불가로 남긴다.",
        f"평가불가 구분: 빈 표본 칸 {int(cov.n.eq(0).sum())}, 관측은 있으나 구간이 불완전한 칸 {int(((cov.n>0)&cov.status.eq('unavailable')).sum())}. 보정 요일 집단 부족 또는 signed CQR 축소 후 역전 구간을 숨긴 결과다.",
        f"UI 구간 노출 허용: {sum(gate.values())}/{len(gate)}개 horizon×운영상태×요일 조합.","",
        "## 평가 방법", "",
        "매 fold에서 과거 학습 → 직전 하루 별도 보정 → 다음 날 test. 라벨 시각으로 경계를 purge한다.",
        "미래 보간 없이 관측을 다음 5분 격자에서 사용한다. 마지막 날짜는 수집된 시각까지의 부분 일자다.",
        "죽은 피드는 각 fold 학습 이전 범위에서 판정한다. 주 지표는 살아있는 곳 운영중/운영외를 함께 보고한다.",
        "CQR 목표 0.80, signed 보정, Platt log-odds, 강등 cutoff 0.5는 사전 고정했다. test 튜닝 없음.",
        "보정 표본 50개 미만인 집단은 구간 평가불가. 학습 모델과 보정 모델은 동일하며 보정 뒤 재학습하지 않는다.","",
        "## 강등 효과", "",
        "A=강등 없음, B=AI 확률>0.5 강등, C=현재 점유율≥90% 강등. 혼잡 정답은 도착 점유율≥90%다.",
        "6곳×2시간 격자×4 horizon, 주차시간 60분. 두 축의 질의 수를 분모로 쓴다.",
        "도보는 TMAP 실측 캐시를 고정했고 차 도착 시각은 공통 t+h로 통제했다. 과거 교통상황 재현 실험은 아니다.",
        "캐시/관측/요금이 하나라도 없는 후보가 있으면 질의를 제외한다. 제외 내역은 별도 CSV다.",
        "운영 상태는 A의 1위 도착 시각 기준이다. 목적지 전체를 운영중이라고 단정하지 않는다.",
        "회피 성공은 A가 혼잡이고 B의 새 1위가 여유인 경우다. 대체 후보도 혼잡이면 성공이 아니다.","",
        "| 축 | 질의 n | 순위 변경 | A 혼잡 | B 혼잡 | C 혼잡 | 회피/A혼잡 | 새 실패 | C 대비 AI 이득 |", 
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in summary.to_dict("records"):
        lines.append(f"| {r['axis']} | {r['n']} | {r['changed']}/{r['n']} ({r['changed_rate']:.2%}) | {r['a_fail']} | {r['b_fail']} | {r['c_fail']} | {r['rescued']}/{r['a_fail']} ({r['rescue_rate']:.2%}) | {r['harmed']} | {r['ai_gain_pp']:.2f}%p |")
    exclusions=pd.read_csv(TAB/"u11_rank_exclusions.csv")
    call_path=TAB/"u11_walk_calls.json"
    calls=json.loads(call_path.read_text()) if call_path.exists() else {}
    lines += ["", f"제외 질의: {len(exclusions)}건(축 분리 전). 경로 누락 {int(exclusions.missing_routes.gt(0).sum())}건; 관측 누락 {int(exclusions.missing_observations.gt(0).sum())}건.",
              f"도보 캐시 보완 실행 기록: {json.dumps(calls,ensure_ascii=False)}. 평가·재정렬 루프 자체의 외부 API 호출은 0회.",
              "", "일자를 묶어서 재표집한 C 대비 B 이득의 95% 구간(일자 수가 적어 탐색적 추정):", "",markdown_table(pd.DataFrame(ci)),
              "", "겹치는 시간·목적지 질의를 독립 표본으로 해석하지 않는다. 아래 수치는 이 기간 재현 실험 결과이며 미래 보장이 아니다.",
              "AI 추가 이득이 음수라면 현재값 강등보다 나쁜 결과다. 강등 off/on의 큰 변화만으로 AI 우월성을 주장하지 않는다.","",
              "## 확률 품질", "", "reliability 기울기는 10개 고정 bin 중 비어있지 않은 평균점의 비가중 직선 기울기다. 각 bin n은 그림과 CSV에 병기한다.", "",
              markdown_table(probs),"",f"위 표는 전체 fold를 합친 결과다. fold별 기울기 통과는 {int(prob_folds.slope_pass.sum())}/{len(prob_folds)}칸이다. pooled 결과로 개별 fold 통과를 주장하지 않는다.",
              "",markdown_table(prob_folds),"", "## 커버리지 전수 결과", "",markdown_table(cov),"",
              "## 재현과 판정", "", "`python -m src.models.u11_evaluate`로 전체 재실행. `python -m src.report.u11_report`로 CSV 재검사·보고서 재생성.",
              "합산 n/포함건수 불일치, 상태 오류, 분할 누수, 중복 질의, 회피 계산 오류는 assertion으로 실패한다.",
              "미달은 partial이며 완료로 바꾸지 않는다. 구간 노출 게이트는 배포 모델에 포함한다.",
              "기존 U10 수치는 정수 마스크·잘못된 분모·피처·분할 문제로 폐기한다. U10의 37/100을 새 결과와 직접 개선율 비교하지 않는다."]
    text="\n".join(lines)+"\n"
    for bad in ("actifs","aktif","보정Pending","습니다.습니다.","커�"):
        assert bad not in text
    (TAB/"u11_report.md").write_text(text,encoding="utf-8")
    result=dict(status=status,coverage_total=len(cov),passed=passed,failed=failed,unavailable=unknown,
                gate_open=sum(gate.values()),gate_total=len(gate),checks="pass",probability_slope_pass=prob_pass)
    (TAB/"u11_status.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return gate


if __name__ == "__main__":
    generate()
    import sys
    if "--require-complete" in sys.argv:
        status=json.loads((TAB/"u11_status.json").read_text())
        sys.exit(0 if status["status"]=="complete" else 2)
