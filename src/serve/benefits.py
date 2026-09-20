#!/usr/bin/env python3
"""감면 자격 — 목록 제공과 복수 자격 평가.

★★ **감면율을 곱하지 않는다.** 조례(별표 2)는 중복 적용 가능 여부를 말하지 않는다.
   0.5 × 0.5 = 0.25 같은 계산은 근거가 없고, 틀리면 과소청구다.
   대신 **자격마다 따로 계산해 가장 싼 하나를 적용**하고, 나머지는 사유와 함께 돌려준다.

★  예외는 조례가 **직접 정의한 결합 코드**뿐이다. `환승주차_경차`(60%)가 그것이다.
   사용자가 환승+경차를 함께 고르면 이 코드를 후보에 넣는다. 우리가 만든 조합이 아니라
   표에 이미 있는 항목이라 근거가 있다.

★  증빙은 현장 제시 사항이다. 서비스는 증빙 서류를 받지도 저장하지도 않는다.
"""
from src.serve import fare_tables as T

# 조례가 직접 정의한 결합. 우리가 곱해서 만든 값이 아니다.
ORDINANCE_COMBINATIONS = {
    frozenset({"환승주차", "경형자동차"}): "환승주차_경차",
}

# 사용자에게 보여줄 설명과 현장 증빙. 감면율·면제분은 `fare_tables.DISCOUNTS` 가 정본이다.
BENEFIT_INFO = {
    "긴급자동차": ("긴급자동차", "소방·구급 등 긴급자동차", "차량 표지"),
    "경형자동차": ("경형자동차", "배기량 1,000cc 미만 경차", "차량등록증 또는 번호판"),
    "저공해차": ("저공해자동차", "저공해차 인증 차량(경유차 제외)", "저공해차 표지"),
    "전기차충전": ("전기차 충전", "충전 중인 전기차", "충전 이용 내역"),
    "경로우대": ("경로우대", "만 65세 이상", "신분증"),
    "장애인_경": ("장애의 정도가 심하지 않은 장애인", "본인 자가운전 차량", "장애인식별표지 또는 복지카드"),
    "장애인_중": ("장애의 정도가 심한 장애인", "본인 자가운전 또는 본인 동승 차량", "장애인식별표지 또는 복지카드"),
    "국가유공자": ("국가유공자", "국가유공자 및 유족", "국가유공자증"),
    "환승주차": ("환승주차", "대중교통 환승 이용", "환승 확인증"),
    "환승주차_경차": ("환승주차 + 경차", "환승 이용 중인 경차", "환승 확인증 + 차량등록증"),
    "다자녀": ("다자녀", "두 자녀 이상 가정", "다자녀 우대카드"),
    "임산부": ("임산부", "임신 중이거나 출산 후 6개월 이내", "임산부 확인 서류"),
    "전통시장": ("전통시장 이용", "지정 전통시장 이용 고객", "시장 상점 영수증"),
}

STACKING_NOTE = ("조례가 감면 중복 적용 가능 여부를 정하고 있지 않아, 고른 자격을 각각 계산한 뒤 "
                 "가장 저렴한 하나만 적용합니다.")
EVIDENCE_NOTE = "감면은 현장에서 증빙을 제시해야 적용됩니다."


class UnknownBenefit(Exception):
    def __init__(self, codes):
        super().__init__(codes)
        self.codes = sorted(codes)


def catalog():
    """`GET /api/v1/benefits` 응답 본문. 감면율은 요금표에서 그대로 읽는다."""
    items = []
    for code, rule in T.DISCOUNTS.items():
        label, description, evidence = BENEFIT_INFO.get(code, (code, "", ""))
        items.append({
            "code": code,
            "label": label,
            "description": description,
            "discount_percent": round((1 - rule["rate"]) * 100),
            "free_minutes": rule["free_min"],
            "note": rule["note"],
            "evidence": evidence,
            "evidence_required": True,
        })
    items.sort(key=lambda item: item["code"])
    return {"benefits": items, "stacking": STACKING_NOTE, "evidence_note": EVIDENCE_NOTE,
            "combinations": [{"codes": sorted(pair), "combined_code": combined,
                              "note": "조례가 직접 정한 결합입니다."}
                             for pair, combined in ORDINANCE_COMBINATIONS.items()]}


def validate(codes):
    unknown = {code for code in codes if code not in T.DISCOUNTS}
    if unknown:
        raise UnknownBenefit(unknown)
    return list(dict.fromkeys(codes))          # 순서를 지키며 중복 제거


def candidates(codes):
    """평가할 후보 목록. 조례가 정한 결합이 성립하면 그것도 후보에 넣는다."""
    chosen = set(codes)
    extra = [combined for pair, combined in ORDINANCE_COMBINATIONS.items()
             if pair <= chosen and combined not in chosen]
    return list(codes) + extra


def evaluate(codes, price):
    """자격별로 따로 계산하고 가장 싼 하나를 고른다.

    `price(code) -> calc_fare 결과 dict`. 계산이 안 되는 자격은 버리지 않고
    사유와 함께 남긴다 — 왜 적용되지 않았는지 사용자가 알아야 한다.
    """
    codes = validate(codes)
    if not codes:
        return {"selected": None, "options": [], "stacking": STACKING_NOTE,
                "evidence_note": EVIDENCE_NOTE, "combined_from": None}

    options = []
    for code in candidates(codes):
        label, _, evidence = BENEFIT_INFO.get(code, (code, "", ""))
        combined = sorted(next((pair for pair, value in ORDINANCE_COMBINATIONS.items()
                                if value == code), ())) or None
        result = price(code)
        options.append({
            "code": code, "label": label, "evidence": evidence,
            "total": result["total"], "total_prepaid": result["total_prepaid"],
            "free_minutes": result["free_minutes"],
            "combined_from": combined,
            "applied": False,
            "rejected_reason": (None if result["total"] is not None
                                else result["reason"] or "요금을 계산할 수 없습니다"),
        })

    priced = [option for option in options if option["total"] is not None]
    if not priced:
        return {"selected": None, "options": options, "stacking": STACKING_NOTE,
                "evidence_note": EVIDENCE_NOTE, "combined_from": None}

    # 가장 싼 하나. 같은 금액이면 사용자가 고른 순서를 지킨다.
    best = min(priced, key=lambda option: (option["total"], options.index(option)))
    best["applied"] = True
    for option in options:
        if option is best or option["rejected_reason"]:
            continue
        option["rejected_reason"] = (
            f"{best['label']} 감면이 더 저렴해 그쪽을 적용했습니다. " + STACKING_NOTE)
    return {"selected": best["code"], "options": options, "stacking": STACKING_NOTE,
            "evidence_note": EVIDENCE_NOTE, "combined_from": best["combined_from"]}
