"""서비스와 오프라인 실험에서 공유하는 두 축 정렬."""

def rank_cards(cards, axis, cutoff=0.5, mode="B"):
    if axis not in ("walk", "fare") or mode not in ("A", "B", "C"):
        raise ValueError("unknown ranking axis/mode")

    def key(card):
        demoted = False
        if not card.get("estimated", False):
            if mode == "B" and cutoff is not None:
                p = card.get("full_prob")
                demoted = p is not None and p > cutoff
            elif mode == "C":
                occ = card.get("occ_now")
                demoted = occ is not None and occ >= 90
        first, second = (("walk_min", "fare_payg") if axis == "walk"
                         else ("fare_payg", "walk_min"))
        return (demoted, card[first] if card.get(first) is not None else float("inf"),
                card[second] if card.get(second) is not None else float("inf"))
    return sorted([c for c in cards if c.get("is_live", True)], key=key)
