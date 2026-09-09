# U10 Fixtures Schema

> U11 주의: 기존 JSON은 U10 당시 산출물이다. 확률 클래스 참조 오류 수정 이전이므로 AI 성과 근거로 사용하지 않는다.
> 새 생성기는 서비스의 구간 게이트를 따른다. `interval_status != "pass"`이면
> 모든 배열에서 `pred_p10`/`pred_p90`은 null이다. 최신 게이트와 판정은 `reports/tables/u11_status.json`에 있다.
> `avail_now`는 **주차된 대수**, `avail_pred`는 **예측 점유율(%)**이다. 둘 다 빈자리 수가 아니다.
> UI는 null을 0으로 바꾸지 않고 정보 없음으로 표시한다.
> 미래 시나리오 재현은 별도 작업이다. 생성기는 현재 실행 시각 기준이며 파일명 요일을 실제 예측 시각으로 해석하지 않는다.

This directory contains the JSON fixtures for the three U10 demo scenarios.

## Files

- `anyang_city_hall_weekday.json`: 평일(월) 14:00 도착, 120분 주차
- `seoksu_station_saturday.json`: 토요일 11:00 도착, 60분 주차  
- `beomgye_station_sunday.json`: 일요일 15:00 도착, 180분 주차

## Schema

Each file contains a JSON object with the following keys:

- `cards`: Array of recommended parking lot cards, sorted by the primary ranking (walking time then fare).
  Each card has the following fields:
  - `name` (string): Parking lot name
  - `parking_id` (integer): Unique identifier
  - `drive_min` (integer): Driving time from start to lot (minutes)
  - `walk_min` (integer): Walking time from lot to destination (minutes)
  - `total_min` (integer): drive_min + walk_min
  - `fare_payg` (integer): Pay-as-you-go fare (KRW)
  - `fare_daily_pass` (integer): Daily pass fare (KRW)
  - `daily_pass_better` (boolean): True if daily pass is cheaper than pay-as-you-go
  - `avail_now` (integer or null): 현재 주차된 대수
  - `avail_pred` (float or null): 도착 시점 예측 점유율(%)
  - `full_prob` (float or null): Probability of full occupancy (0.0~1.0) at arrival time (null if real-time feed unavailable)
  - `walk_far_warning` (boolean): True if walk_min > 15 (≈1km)
  - `estimated` (boolean): True if any ETA value was estimated due to API failure
  - `cell_cnt` (integer): Total capacity of the lot
  - `straight_m` (integer): Straight-line distance from lot to destination (meters)
  - `grade` (integer or null): Lot grade (1~3) from standard data
  - `is_live` (boolean): True if real-time feed is currently providing data
  - `operating_hours` (string): Operating hours in "HH:MM~HH:MM" format
  - `unavailable_note` (string or null): Note if real-time feed is unavailable (e.g., "실시간 정보를 제공하지 않는 주차장입니다")
  - `arrive_at` (string): Estimated arrival time at lot in "HH:MM" format
  - `fare_reason` (string or null): Reason for fare calculation (if any)
  - `pred_p10`, `pred_p90`: Prediction interval bounds (p10, p90) are **omitted or set to null** until coverage calibration passes (see U10-4). They are not included in the card schema for U10 fixtures.

- `radius_used` (integer): Search radius used to find candidate lots (meters)

- `unavailable`: 실시간 미제공 완전 카드. `avail_now`, `avail_pred`, `full_prob`는 null이며 순위에서 제외한다.
- `dead_feeds`: 하위 호환용 원시 메타데이터. UI 카드는 `unavailable`을 사용한다. 고정 피드는 항상 빈 주차장을 뜻하지 않는다.

- `unlabeled`: Array of candidate lots that could not be matched to known lots (should be empty in normal operation).

- `alternatives`: Array of alternative parking lots beyond the top recommendations.
  Each alternative has the following fields:
  - `name` (string): Parking lot name
  - `lat` (float): Latitude
  - `lng` (float): Longitude
  - `is_public` (boolean): Whether it's a public parking lot
  - `distance_m` (integer): Straight-line distance from destination to lot (meters)

## Notes

- The `avail_pred` and `full_prob` fields are null when the real-time feed is unavailable or the lot is in `dead_feeds`.
- The `estimated` flag is true if any of the driving ETAs were estimated due to Kakao/TMAP API failure.
- All times are in minutes, distances in meters, fares in KRW (Korean Won).
- The fixtures were generated with the model and data as of the commit that generated them.
- **Unavailable feeds** (22 dead feeds with zero occupancy variance) are listed in `dead_feeds` with full metadata but null occupancy/prediction fields.
- **Alternatives** are provided for UI to show additional options beyond the top recommendations.
