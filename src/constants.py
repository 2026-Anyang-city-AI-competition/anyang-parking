# src/constants.py  ← 둘 다 여기서 import. 각자 재정의 금지

# 6일(162,514행) 전수 검사에서 변동폭 정확히 0.00 인 22곳
# 재검증 불필요. T1에서 GITS 대체 성공하면 이 목록을 줄인다.
DEAD_FEED_IDS = {3, 6, 15, 19, 35, 41, 45, 46, 47, 55, 57, 61,
                 101, 102, 103, 104, 105, 106, 107, 109, 110, 111}

def is_live(parking_id: int) -> bool:
    return parking_id not in DEAD_FEED_IDS

# 운영시간: wdays_start == wdays_end 는 미운영. "00:00~23:59" 는 24시간.
# is_operating / is_weekend / is_sunday 는 전부 target_time 기준으로 계산한다.