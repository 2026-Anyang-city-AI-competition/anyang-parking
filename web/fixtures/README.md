# U10 Fixtures Schema

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
  - `avail_now` (integer): Current available spaces (derived from real-time feed)
  - `avail_pred` (integer or null): Predicted available spaces at arrival time (null if real-time feed unavailable)
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

- `dead_feeds`: Array of parking lot objects that have dead feeds (always zero occupancy) but are included for fare/walk cards.
  Each object has the same structure as a card but with `avail_pred` and `full_prob` always null.

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
