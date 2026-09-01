#!/usr/bin/env python3
"""
공단 sttus 배치 두 벌을 대조한다. 새 배치(전체)로 옛 배치(부분)를 대체해도
안전한지 판정하는 것이 목적.

  python3 scripts/compare_kotsa.py                                  # 기본 경로
  python3 scripts/compare_kotsa.py --old data/raw/kotsa --new data/raw/kotsa_v2

old 는 다음 순서로 찾는다:
  1) {OLD}_gg.jsonl / {OLD}_anyang.jsonl      (신형 스트리밍 산출물)
  2) {OLD}_gg.json  / {OLD}_anyang.json       (구형 JSON 배열)
  3) .ckpt_{OLD}_sttus.json 또는 .ckpt_sttus.json 의 gg 배열  (미완 배치의 부분 결과)
"""
import argparse, json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def p(prefix, suffix):
    q = Path(prefix)
    if not q.is_absolute(): q = ROOT / q
    return q.parent / f"{q.name}_{suffix}"

def ckpt_of(prefix):
    q = Path(prefix)
    if not q.is_absolute(): q = ROOT / q
    for cand in (q.parent / f".ckpt_{q.name}_sttus.json",
                 q.parent / ".ckpt_sttus.json"):
        if cand.exists(): return cand
    return None

def load_side(prefix, label):
    """(gg, anyang, 출처설명) 반환. 못 찾으면 (None, None, 사유)."""
    f = p(prefix, "gg.jsonl")
    if f.exists():
        gg = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
        af = p(prefix, "anyang.jsonl")
        ay = ([json.loads(l) for l in open(af, encoding="utf-8") if l.strip()]
              if af.exists() else [o for o in gg if o.get("_anyang")])
        return gg, ay, f"{f.name} (완료본)"
    f = p(prefix, "gg.json")
    if f.exists():
        gg = json.load(open(f, encoding="utf-8"))
        af = p(prefix, "anyang.json")
        ay = (json.load(open(af, encoding="utf-8")) if af.exists()
              else [o for o in gg if o.get("_anyang")])
        return gg, ay, f"{f.name} (구형 JSON 배열)"
    ck = ckpt_of(prefix)
    if ck:
        d = json.load(open(ck, encoding="utf-8"))
        gg = d.get("gg") or []
        if gg:
            return gg, [o for o in gg if o.get("_anyang")], \
                   f"{ck.name} (미완 배치, p{d.get('last_page')}까지)"
        return None, None, f"{ck.name} 에 gg 배열이 없다(스트리밍 배치는 ckpt에 데이터를 담지 않음)"
    return None, None, f"{prefix}_* 산출물 없음"

def ids_of(rows):
    return {o.get("prk_center_id") for o in rows if o.get("prk_center_id")}

def cmprt_stats(rows):
    tot, miss = 0, 0
    for o in rows:
        v = o.get("prk_cmprt_co")
        try:
            tot += int(v)
        except (TypeError, ValueError):
            miss += 1
    return tot, miss

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default="data/raw/kotsa")
    ap.add_argument("--new", default="data/raw/kotsa_v2")
    a = ap.parse_args()

    old_gg, old_ay, old_src = load_side(a.old, "old")
    new_gg, new_ay, new_src = load_side(a.new, "new")

    print(f"old : {old_src}")
    print(f"new : {new_src}\n")
    if new_gg is None:
        sys.exit("new 산출물이 없다. sttus 배치를 먼저 완료할 것.")

    print(f"{'':<12}{'경기 행':>10}{'경기 ID':>10}{'안양 행':>10}{'안양 ID':>10}")
    if old_gg is not None:
        print(f"{'old':<12}{len(old_gg):>10,}{len(ids_of(old_gg)):>10,}"
              f"{len(old_ay):>10,}{len(ids_of(old_ay)):>10,}")
    print(f"{'new':<12}{len(new_gg):>10,}{len(ids_of(new_gg)):>10,}"
          f"{len(new_ay):>10,}{len(ids_of(new_ay)):>10,}")

    print("\n── new 가 old 를 덮는가 ──")
    if old_gg is None:
        print("  old 없음 → 대조 생략. new 만으로 진행 가능한지는 아래 지표로 판단.")
    else:
        miss_gg = ids_of(old_gg) - ids_of(new_gg)
        miss_ay = ids_of(old_ay) - ids_of(new_ay)
        print(f"  new 에 없는 old 경기 ID : {len(miss_gg):,}")
        print(f"  new 에 없는 old 안양 ID : {len(miss_ay):,}")
        for i in list(miss_ay)[:10]: print(f"    - {i}")
        print("  ✅ 0 이면 옛 배치를 버려도 안전"
              if not miss_gg and not miss_ay else
              "  ⚠️ 0 이 아니다. 옛 배치를 버리지 말 것 — 실패 페이지나 필터 차이를 확인하라.")

    print("\n── 안양 상세 (new) ──")
    ids = [o.get("prk_center_id") for o in new_ay if o.get("prk_center_id")]
    dup = len(ids) - len(set(ids))
    print(f"  행 {len(new_ay):,} / 고유 ID {len(set(ids)):,} / 중복 {dup:,}")
    if dup:
        for cid, n in Counter(ids).most_common(5):
            if n > 1: print(f"    {cid}  {n}행")
        print("    → prk_center_id 를 PK 로 쓰면 위 행이 소실된다.")
    tot, miss = cmprt_stats(new_ay)
    print(f"  prk_cmprt_co 합계 {tot:,}면 / 결측 {miss:,}건")
    sig = Counter((o.get("prk_plce_adres_sigungu") or "(빈값)") for o in new_ay)
    print(f"  시군구: {sig.most_common(5)}")

    meta = p(a.new, "pages.json")
    if meta.exists():
        d = json.load(open(meta, encoding="utf-8"))
        f = d.get("sttus_failed") or []
        print(f"\n실패 페이지: {len(f)}개 {f[:20]}"
              + ("  ⚠️ 결과는 하한이다." if f else "  ✅"))

if __name__ == "__main__":
    main()
