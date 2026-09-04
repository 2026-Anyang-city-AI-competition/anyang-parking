# 카카오 API 실호출 테스트 (2026-09-04)

세 API 모두 **성공**. 제휴 없이 카카오디벨로퍼스 REST 키로 동작한다.
필드명은 전부 실응답에서 확인했다.

## 3-1. 다중 목적지 길찾기 ✅

```
POST https://apis-navi.kakaomobility.com/v1/destinations/directions
Authorization: KakaoAK {REST_KEY} · Content-Type: application/json
body {"origin":{"x":126.9226,"y":37.4018},                 ★ x=경도, y=위도
      "destinations":[{"x":lon,"y":lat,"key":"3"}, …],     최대 30개
      "radius":5000}
```

**응답**

```
{"trans_id": "...",
 "routes":[{"result_code":0, "result_msg":"길찾기 성공", "key":"3",
            "summary":{"distance":1574, "duration":460}}]}   distance=m, duration=초
```

- 10곳·25곳 요청 모두 `result_code 0` 으로 전건 성공
- **쿼터 관련 응답 헤더가 없다** — 잔여 호출수를 알 수 없으므로 카운팅은 우리가 해야 한다
- `key` 로 요청·응답을 매칭한다. 순서에 의존하지 말 것

## 3-2. 미래 운행 정보 ✅

```
GET https://apis-navi.kakaomobility.com/v1/future/directions
    origin="126.9226,37.4018"   destination="lon,lat"      ★ 경도,위도
    departure_time="202609041511"    YYYYMMDDHHMM · 반드시 미래
    summary=true
```

**응답** `routes[0].summary{distance, duration, fare{taxi, toll}, origin, destination, priority, bound}`

- 30분 뒤 출발 기준 4,128m / 795초 / 택시요금 8,100원 정상 반환
- 과거 시각을 주면 실패한다. 항상 `now + N분` 으로 만들 것

## 3-3. 카카오 로컬 PK6 ✅

```
GET https://dapi.kakao.com/v2/local/search/category.json
    category_group_code=PK6 · x=경도 · y=위도 · radius(≤20000) · size(≤15) · page(≤3)
```

**응답** `documents[]{place_name, x, y, distance, category_name, road_address_name, place_url, id, phone}`

안양시청 반경 1km 결과:

| | 값 |
|---|---|
| `total_count` | **100** |
| 실제 수집 가능 | **45** (15×3페이지) ← `pageable_count` 상한 |
| 그중 공영(`category_name` 에 "공영") | 8 |
| 표준데이터 107곳과 60m 이내 매칭 | **12 / 45** |
| **「대체 주차장」 후보 (안 겹치는 것)** | **33곳** |

⚠️ **한 질의당 45건이 상한이다.** `total_count` 가 100인데 45건만 온다.
전수를 얻으려면 반경을 줄여 격자로 훑어야 한다.

`category_name` 이 `교통,수송 > 교통시설 > 주차장 > 공영주차장` 형태라 **공영/민영 구분이 된다.**
아파트·사무실 부설은 애초에 나오지 않아 정본 §5-⑥ 의 기대대로 유형 판정을 우회한다.

## 3-4. ★ 폴백 — 상수를 실측으로 재보정했다

정본이 제시한 `직선거리 × 1.3 ÷ 20km/h` 를 그대로 쓰면 **틀린다.**
안양역 출발 25개 경로를 카카오 실제값과 대조한 결과:

| | 정본 가정 | 실측 | 보정 후 |
|---|---:|---:|---:|
| 우회계수 (도로거리/직선거리) | 1.3 | **1.62** | 1.6 |
| 도심 평균속도 | 20 km/h | **15.6 km/h** | 15.5 |
| 거리 비 (폴백/실제) | 0.804 | — | **0.990** |
| 소요시간 비 | **0.630** ← 37% 과소추정 | — | **1.037** |

보정 후 절대 오차 중앙 **2.4분**, 최대 13.1분, 사분위 0.78~1.21.

> ⚠️ 출발지 1곳·25개 경로 기준이다. 표본이 늘면 재보정할 것.
> 목적지가 멀수록 오차가 커진다(최대 13분은 6km 경로).

**폴백 검증에서 잡은 버그:** `key = key or _key()` 로 쓰면 `key=""` 가 자동 로드로 새어나가
폴백 경로가 실행되지 않는다. `if key is None` 으로 고쳤다.
안 고쳤으면 폴백을 "검증했다"고 잘못 보고할 뻔했다.

## 구현

`src/serve/routing.py` — `multi_eta` · `future_eta` · `alt_parkings` · `fallback_eta`

- 모든 호출이 실패해도 예외를 밖으로 던지지 않는다. 실패한 항목만 폴백으로 채운다
- 목적지 30개 초과 시 자동 분할
- `python3 src/serve/routing.py` 로 자체 점검 (카카오 경로 + 폴백 경로 모두)

## 남은 확인 사항

- 호출 한도: 응답 헤더에 없다. 카카오디벨로퍼스 콘솔에서 확인 필요
- PK6 45건 상한을 격자 탐색으로 우회할지, 아니면 반경 1km 45건으로 충분한지 판단 필요
