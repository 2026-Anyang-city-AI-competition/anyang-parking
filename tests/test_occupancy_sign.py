#!/usr/bin/env python3
"""
★ 부호 단위 테스트 — 두 출처가 반대다. 뒤집으면 조용히 전부 틀린다.

  도시공사 parking.db : PARK_COUNT    = 주차된 대수 → occ = park_count / cell_cnt
  GITS     gits.db    : avblPklotCnt  = ★ 이름은 "available" 이지만 실제 값은
                        **주차된 대수**다 → occ = avbl / pklot   (도시공사와 동일)

  ★★ 2026-09-04 실측으로 확정. 정본 v9 §5-④ 의 "(총−잔여)/총" 은 틀렸다.
     안양 89곳은 두 출처에 모두 있는데 값이 그대로 일치한다:
       안양6동5노외 GITS 96 = 도시공사 96 · 박달고가밑 34 = 34 · 호원어린이공원 20 = 20
     |GITS−도시공사| 중앙값 0.632  vs  |(1−GITS)−도시공사| 0.000
  ⚠️ 안양 외 시군은 규약 미검증. 야간 데이터로 판별할 것(§ 아래 주석)

  python3 tests/test_occupancy_sign.py
"""
import sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _sec(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()
FAIL = []

def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond: FAIL.append(name)

def occ_gits(pklot, avbl):
    """GITS 점유율. avbl 은 이름과 달리 '주차 대수'다(실측 확정)."""
    if not pklot: return None
    return avbl / pklot

def occ_auc(park_count, cell_cnt):
    """도시공사 점유율. park_count 는 '주차 대수'다."""
    if not cell_cnt: return None
    return park_count / cell_cnt

print("=== 공식 자체 ===")
check("GITS 만차(avbl=총) → 1.0", occ_gits(100, 100) == 1.0)
check("GITS 빈차(avbl=0) → 0.0",  occ_gits(100, 0)   == 0.0)
check("도시공사 만차 → 1.0",      occ_auc(100, 100)  == 1.0)
check("도시공사 빈차 → 0.0",      occ_auc(0, 100)    == 0.0)
check("두 출처가 같은 규약", occ_gits(100, 100) == occ_auc(100, 100))

# 부호가 뒤집히면 이상치 비율이 폭증한다(대부분이 >1 이 됨). 임계값으로 잡는다.
ANOM_MAX = 0.05

for db, sql, fn, label, allow_over in (
    ("data/raw/gits.db",
     "SELECT cell_cnt, avail_cnt FROM gits_obs WHERE CAST(cell_cnt AS INT)>0",
     lambda a, b: occ_gits(int(a), int(b)), "GITS", False),
    ("data/raw/parking.db",
     "SELECT cell_cnt, park_count FROM obs WHERE cell_cnt>0",
     lambda a, b: occ_auc(int(b), int(a)), "도시공사", True)):
    p_ = ROOT / db
    print(f"\n=== 실데이터 {label} ({db}) ===")
    if not p_.exists():
        print(f"  SKIP  {db} 없음"); continue
    rows = sqlite3.connect(p_).execute(sql).fetchall()
    if not rows:
        print("  SKIP  행 없음"); continue
    occ = [fn(a, b) for a, b in rows if a is not None and b is not None]
    n = len(occ)
    neg  = sum(1 for o in occ if o < 0)
    over = sum(1 for o in occ if o > 1)
    anom = (neg + over) / n
    print(f"  n={n:,} · 범위 [{min(occ):.3f}, {max(occ):.3f}] · "
          f"음수 {neg} · 1초과 {over} · 이상치 {anom:.2%}")
    check(f"{label} 이상치 < {ANOM_MAX:.0%} (부호 뒤집힘 탐지)", anom < ANOM_MAX, f"{anom:.2%}")
    if label == "도시공사" and over:
        print(f"  INFO  면수 초과 관측은 실재한다(18/17 등). 정본 §5-① 확인 사항")
    if label == "GITS" and (neg or over):
        print(f"  INFO  GITS 원본에 잔여<0 등 오염 행이 있다 → build.py 에서 제거할 것")
# ── ★ 진짜 부호 검출기 ────────────────────────────────────
# 범위 검사(0~1)로는 못 잡는다. 올바른 식과 뒤집은 식 둘 다 0~1 안에 들어오기 때문이다.
# 안양 89곳은 두 출처에 모두 있다. 같은 lot·같은 시각이면 두 점유율이 '같아야' 한다.
# 부호가 뒤집혔다면 두 값이 서로 여집합(합≈1)이 된다.
print("\n=== ★ 교차 검증 — 안양 89곳은 두 출처에 모두 있다 ===")
gp, pp = ROOT/"data/raw/gits.db", ROOT/"data/raw/parking.db"
if not (gp.exists() and pp.exists()):
    print("  SKIP  두 DB 가 모두 필요하다")
else:
    import re
    def norm(x): return re.sub(r"\s", "", x or "")
    g = sqlite3.connect(gp)
    auc = sqlite3.connect(pp)
    grows = g.execute("""SELECT l.pkplc_nm, o.ts_kst, o.cell_cnt, o.avail_cnt
                         FROM gits_obs o JOIN gits_lots l
                           ON o.lae_id=l.lae_id AND o.pkplc_id=l.pkplc_id
                         WHERE l.lae_nm='안양시' AND CAST(o.cell_cnt AS INT)>0""").fetchall()
    arows = auc.execute("""SELECT l.name, o.ts_kst, o.cell_cnt, o.park_count
                           FROM obs o JOIN lots l USING(parking_id)
                           WHERE o.cell_cnt>0""").fetchall()
    from collections import defaultdict
    A = defaultdict(list)
    for nm, ts, cc, pc in arows: A[norm(nm)].append((ts, pc/cc))
    pairs = []
    for nm, ts, cc, av in grows:
        k = norm(nm)
        if k not in A: continue
        # 시각이 가장 가까운 관측을 고른다(문자열 ISO 비교로 충분)
        best = min(A[k], key=lambda x: abs(_sec(x[0]) - _sec(ts)))
        if abs(_sec(best[0]) - _sec(ts)) <= 300:
            pairs.append((occ_gits(cc, av), best[1]))
    if len(pairs) < 20:
        print(f"  SKIP  겹치는 관측이 {len(pairs)}쌍뿐 (GITS 수집 시간이 더 필요)")
    else:
        import statistics as st
        d_same = st.median(abs(a-b) for a, b in pairs)
        d_flip = st.median(abs((1-a)-b) for a, b in pairs)
        print(f"  {len(pairs)}쌍 · |GITS−도시공사| 중앙값 {d_same:.3f} · "
              f"|(1−GITS)−도시공사| {d_flip:.3f}")
        check("★ 안양: GITS 와 도시공사 점유율이 일치한다", d_same < d_flip,
              f"|일치식| {d_same:.3f} < |뒤집은식| {d_flip:.3f}")
        check("두 출처 점유율 중앙값 차이 < 0.05", d_same < 0.05, f"{d_same:.3f}")
        print("  ⚠️ 안양 외 시군은 규약 미검증. 야간(03~05시) 점유율로 판별한다 —")
        print("     도시공사 실측상 운영시간 외 점유가 0.711 > 운영중 0.457 이므로,")
        print("     avbl 이 주차대수면 야간 값이 '높게', 잔여면 '낮게' 나온다.")

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {FAIL}"); sys.exit(1)
print("✅ 전부 통과")
