"""서비스와 오프라인 실험에서 공유하는 두 축 정렬."""


def demotion_of(card, cutoff=0.5, mode="B"):
    """(강등 여부, 이유 dict). 강등 규칙의 단일 출처다.

    ★ 폴백 ETA(`estimated`)에는 강등을 적용하지 않는다(도착시간 정책 ⑨).
      도착 시각 자체가 추정이면 그 시각의 만차확률로 순위를 내릴 근거가 없다."""
    if card.get("estimated", False):
        return False, None
    if mode == "B" and cutoff is not None:
        p = card.get("full_prob")
        if p is not None and p > cutoff:
            return True, {"code": "full_probability_above_cutoff",
                          "full_prob": p, "cutoff": cutoff,
                          "message": "도착 시점에 만차일 가능성이 높아 순위를 내렸어요."}
    elif mode == "C":
        occ = card.get("occ_now")
        if occ is not None and occ >= 90:
            return True, {"code": "current_occupancy_above_90", "occ_now": occ,
                          "message": "지금 이미 거의 차 있어 순위를 내렸어요."}
    return False, None


def rank_cards(cards, axis, cutoff=0.5, mode="B"):
    if axis not in ("walk", "fare") or mode not in ("A", "B", "C"):
        raise ValueError("unknown ranking axis/mode")

    def key(card):
        demoted, _ = demotion_of(card, cutoff, mode)
        first, second = (("walk_min", "fare_payg") if axis == "walk"
                         else ("fare_payg", "walk_min"))
        return (demoted, card[first] if card.get(first) is not None else float("inf"),
                card[second] if card.get(second) is not None else float("inf"))
    return sorted([c for c in cards if c.get("is_live", True)], key=key)


def rank_with_demotion(cards, axis, cutoff=0.5, mode="B"):
    """강등 전/후 순위를 함께 담은 **카드 사본** 목록.

    한 카드가 두 축에 동시에 들어가므로 축별 순위를 원본에 쓰면 서로 덮어쓴다.
    그래서 얕은 사본에만 순위를 붙인다. 강등이 순위를 실제로 바꿨는지는
    `rank`와 `rank_without_demotion`을 비교하면 된다."""
    baseline = rank_cards(cards, axis, cutoff=None, mode="A")
    before = {id(card): index + 1 for index, card in enumerate(baseline)}
    ordered = []
    for index, card in enumerate(rank_cards(cards, axis, cutoff, mode), 1):
        demoted, reason = demotion_of(card, cutoff, mode)
        ordered.append({**card, "rank": index,
                        "rank_without_demotion": before.get(id(card)),
                        "demoted": demoted, "demotion_reason": reason})
    return ordered
