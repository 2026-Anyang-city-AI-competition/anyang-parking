# 안양 주차 추천 서비스 개발 TODO

작성일: 2026-09-15  
목적: 현장 조사 결과를 서비스용 데이터로 바꾸고, 추천·요금·할인·검색·회원 기능까지 남은 개발을 한 문서에서 관리한다.

성능 실험의 목표·순서·합격 기준은 [`performance_plan.md`](performance_plan.md)가 정본이다. 현재 수치는 [`../reports/performance_status.md`](../reports/performance_status.md)를 따른다.

## 0. 현재 구현 상태

- [x] `POST /api/v1/recommend`: 좌표·주차시간을 받아 도보순/요금순 추천
- [x] `GET /api/v1/health`: 모델·관측 최신성·예측 가능 주차장 수 확인
- [x] 추천 요청 전 `parking.db` 최신 시각 확인 및 5분 게이트 폴링
- [x] 누진요금, 유료시간 걸침, 15분 미만 무료, 일요일 무료, 일 최대 상한, 100원 미만 절사
- [x] 후불요금과 선불 일일권 병기
- [x] 단일 감면 코드 계산
- [x] 도착 시점 `full_prob` 기반 추천 강등
- [x] `POST /api/v1/fare/quote` 독립 요금 견적
- [x] 조사 결과를 읽는 구조화된 출입·과금 데이터셋 — 89곳 267행, 검증 통과
- [x] 실제 입출차 가능시간을 반영한 추천 제외
- [x] 데이터 경직·이상 시간대의 예측 차단 엔진 — 실제 진단 행 적재는 별도
- [x] 장소명·주소 검색 API
- [ ] 복수 할인 자격과 마이페이지
- [ ] 카카오 OAuth
- [ ] 웹 UI의 실제 API 연결

`parking.db`가 서비스의 주 데이터다. `gits.db`는 예비·대조·추가 실험용이며 추천 요청 경로에서 사용하지 않는다.

### API 전체 목록

| 우선순위 | API | 상태 | 역할 |
|---|---|---|---|
| P0 | `GET /api/v1/health` | 구현됨 | 모델·DB·예측 준비 상태 |
| P0 | `POST /api/v1/recommend` | 1차 구현 | 추천. 출입 필터·복수 할인·상세 요금 보강 필요 |
| P0 | `GET /api/v1/places/search` | 구현됨 | 목적지 이름·주소 검색 |
| P0 | `POST /api/v1/fare/quote` | 구현됨 | 시간·일일권·할인별 독립 요금 견적 |
| P1 | `GET /api/v1/benefits` | 미구현 | 지원 감면 항목과 설명 제공 |
| P1 | `GET /api/v1/auth/kakao/login` | 미구현 | 카카오 로그인 시작 |
| P1 | `GET /api/v1/auth/kakao/callback` | 미구현 | OAuth 콜백 |
| P1 | `POST /api/v1/auth/logout` | 미구현 | 로그아웃 |
| P1 | `GET /api/v1/me` | 미구현 | 내 설정 조회 |
| P1 | `PUT /api/v1/me/preferences` | 미구현 | 할인·개인화 설정 저장 |
| P1 | `DELETE /api/v1/me` | 미구현 | 계정과 설정 삭제 |
| P2 | `GET /api/v1/parkings/{id}` | 미구현 | 주차장 상세정보 |

---

## 1. 조사 결과 데이터셋

### 1.1 원칙

아래 세 시간은 절대 같은 값으로 취급하지 않는다.

1. **출입 가능시간**: 일반 시간제 차량이 실제로 들어가고 나올 수 있는 시간
2. **요금 징수시간**: 주차요금이 계산되는 시간. 이 시간이 끝나도 무료 개방일 수 있다.
3. **예측 가능시간**: 실시간 데이터가 정상적으로 움직이고 검증되어 AI 예측을 제공할 수 있는 시간

`reports/태영.txt`는 조사 메모로 보존하고, 서비스는 메모 문장을 직접 파싱하지 않는다. 검증한 결과를 아래 CSV로 옮긴 뒤 스키마 검증을 통과한 값만 사용한다.

### 1.2 정본 파일

신규 생성 예정:

- `data/raw/parking_access_rules.csv`: 조사로 확인한 출입·과금 규칙. **주차장 × 요일그룹 267행. 서비스 정본.**
- `data/processed/prediction_availability.csv`: DB 진단으로 산출한 예측 가능시간
- `data/processed/parking_access_rules.csv`: **분석용 별도 파일.** 주차장 1행 89행이며
  `feed_status`·`enterable_status`를 담는다. A20/A22가 읽는다. 서비스 경로는 이 파일을 읽지 않는다.

서비스본과 분석본은 **합치지 않는다.** `scripts/crosscheck_access_rules.py`가 대조만 하고
충돌·보강 후보·양쪽 미확인을 보고한다. 현재 충돌 0건, 일치 189행, 양쪽 미확인 26곳이다.

`parking_access_rules.csv`는 **주차장 × 요일그룹 한 행**으로 저장한다.

| 필드 | 예시 | 의미 |
|---|---|---|
| `parking_id` | `39` | `parking.db.lots.parking_id` |
| `name` | `안양7동노외` | 검수용 이름. 조인은 ID로만 한다. |
| `day_group` | `weekday` | `weekday`, `saturday`, `sunday_holiday` |
| `entry_windows` | `00:00-24:00` | 입차 가능 구간. 복수 구간은 `|`로 구분 |
| `exit_windows` | `00:00-24:00` | 출차 가능 구간 |
| `fee_windows` | `09:00-17:00` | 과금 구간. 종일 무료면 빈 값 |
| `fee_mode` | `paid_window_free_outside` | 과금 정책 코드 |
| `overnight_allowed` | `true` | 폐쇄시간을 사이에 두고 차량을 둘 수 있는지 |
| `access_status` | `confirmed_open` | `confirmed_open`, `confirmed_restricted`, `unknown` |
| `general_public` | `true` | 일반 시간제 승용차 이용 가능 여부 |
| `effective_from` | `2026-09-15` | 규칙 적용 시작일 |
| `effective_to` | 빈 값 | 임시 운영 종료일. 미정이면 빈 값 |
| `checked_at` | `2026-09-15` | 확인일 |
| `evidence_method` | `official_web` | `official_web`, `phone`, `field`, `user_experience` |
| `evidence_ref` | URL/사진/기관명 | 재검증 가능한 근거 |
| `note` | `17시 이후 무료 개방` | 예외·주의사항 |

시간 표기 규칙:

- 24시간은 `00:00-24:00`으로 쓴다.
- `00:00-00:00`을 24시간 또는 휴무로 추정하지 않는다.
- 확인할 수 없으면 시간값을 비우고 `access_status=unknown`으로 둔다.
- 자정을 넘는 구간은 `22:00-24:00|00:00-06:00`처럼 명시한다.
- 여러 출처가 충돌하면 확정값을 만들지 않고 `unknown`으로 돌린다.
- 운영시간만 보고 출입 가능시간을 복사하지 않는다.

### 1.3 ID 39 변환 예시

조사 메모:

> ID 39 / 안양7동노외  
> 월~금 09:00~17:00 요금 징수, 그 외 무료 개방, 주말 무료 개방, 입출차 통제 없음

구조화 결과:

```csv
parking_id,name,day_group,entry_windows,exit_windows,fee_windows,fee_mode,overnight_allowed,access_status,general_public,effective_from,effective_to,checked_at,evidence_method,evidence_ref,note
39,안양7동노외,weekday,00:00-24:00,00:00-24:00,09:00-17:00,paid_window_free_outside,true,confirmed_open,true,2026-09-15,,2026-09-15,user_experience,reports/태영.txt#ID-39,17시 이후 무료 개방
39,안양7동노외,saturday,00:00-24:00,00:00-24:00,,free,true,confirmed_open,true,2026-09-15,,2026-09-15,user_experience,reports/태영.txt#ID-39,주말 무료 개방
39,안양7동노외,sunday_holiday,00:00-24:00,00:00-24:00,,free,true,confirmed_open,true,2026-09-15,,2026-09-15,user_experience,reports/태영.txt#ID-39,일요일·공휴일 무료 개방
```

평일 16:00 도착·4시간 주차라면:

- 예상 출차: 20:00
- 입출차: 24시간 가능 → 추천 유지
- 과금: 16:00~17:00의 60분만 계산
- 17:00~20:00: 무료

### 1.4 예측 가능시간 데이터

`prediction_availability.csv`는 조사 결과가 아니라 `parking.db` 연속성·경직·이상 진단 결과로 만든다.

| 필드 | 의미 |
|---|---|
| `parking_id` | 주차장 ID |
| `day_group` | 평일/토요일/일요일·공휴일 |
| `prediction_windows` | 검증된 예측 제공 시간 |
| `status` | `available`, `frozen`, `evaluation_pending`, `anomaly`, `dead_feed` |
| `reason` | 차단 사유 코드 |
| `evidence_report` | 산출 리포트 경로 |
| `evaluated_at` | 평가일 |

적용 원칙:

- 운영시간 밖에서 경직된 25곳: 경직 시간대만 예측·만차확률·AI 강등 중단
- 운영시간 밖 평가가 어려운 3곳: 검증 전까지 해당 시간대 예측 숨김
- 명확한 이상징후: 이상 구간을 학습·평가·서비스 예측에서 제외
- 데이터가 움직이지 않는 21곳: `dead_feed`; 위치·요금 카드는 제공하되 실시간 추천 순위에서는 제외
- 예측 불가와 출입 불가는 서로 다른 상태다. 예측 불가여도 출입 가능하면 위치·요금 정보는 제공한다.

### 1.5 데이터 적재·검증 TODO

- [x] 위 스키마로 `parking_access_rules.csv` 생성
- [x] 조사 완료된 항목만 `reports/태영.txt`에서 수동 전환 — 89곳 전부 적재, 26곳은 `unknown`으로 남김
- [x] ID가 `parking.db.lots` 89곳에 존재하는지 검사
- [x] 주차장별 `weekday/saturday/sunday_holiday` 중복·누락 검사
- [x] 시간 형식, 겹치는 구간, 역전 구간, 자정 통과 검사
- [x] `confirmed_*`인데 근거가 비어 있는 행을 실패 처리
- [x] 24시간/무료/미확인을 자동 추정하지 않는 검사
- [x] CSV를 서비스 시작 시 읽는 로더 구현
- [x] 조사 변경 이력과 `effective_from/to` 적용

---

## 2. 추천 가능 여부 판정 — P0

### 2.1 판정식

```text
주차장 도착시각 = 사용자 출발시각 + 차량 ETA
예상 출차시각   = 주차장 도착시각 + 사용자가 입력한 주차시간
```

다음 조건을 모두 만족해야 추천 순위에 넣는다.

1. 일반 시간제 차량이 이용 가능한 주차장이다.
2. 도착시각이 입차 가능구간 안이다.
3. 예상 출차시각이 출차 가능구간 안이다.
4. 주차 구간이 폐쇄시간을 건너면 `overnight_allowed=true`로 확인되어 있다.
5. 임시폐쇄·월정기 전용·차종 제한에 걸리지 않는다.

`access_status=confirmed_restricted`만 강제 제외에 사용한다. `unknown`을 24시간 개방으로 간주하지 않으며, 결과에는 미확인 경고를 표시한다.

### 2.2 추천 파이프라인 변경

```text
후보 조회
→ 차량 ETA 계산
→ 도착·예상 출차시각 계산
→ 출입 규칙 검사
→ 이용 불가 후보 제외
→ 가능한 후보만 예측·요금 계산
→ full_prob 강등
→ 도보순/요금순 반환
```

제외 후 후보가 `minimum_candidates`보다 적으면 반경을 단계적으로 넓혀 다시 검사한다. 최종적으로 부족하면 없는 후보를 채워 넣지 않고 부족 사유를 반환한다.

### 2.3 응답 규격

추천 순위에서 제외한 항목은 `excluded` 배열로 분리한다. 기존 `unavailable`은 실시간 미제공 카드와 의미가 겹치므로 역할을 명확히 나눈다.

```json
{
  "excluded": [
    {
      "parking_id": 7,
      "name": "안양2동노외",
      "reason": "closes_before_departure",
      "arrival_at": "20:00",
      "expected_departure_at": "00:00",
      "access_closes_at": "22:00",
      "message": "예상 출차 전에 주차장이 폐쇄돼요."
    }
  ]
}
```

사유 코드:

- `entry_closed_at_arrival`
- `closes_before_departure`
- `closed_interval_crossed`
- `general_public_not_allowed`
- `temporary_closure`
- `vehicle_restriction`
- `access_schedule_unknown`
- `insufficient_exit_margin`

3단계 구현: `src/serve/access_check.py`의 `check_access()`는 `AccessDecision`을 반환한다.
`available=true`는 이용 가능, `false`는 확인된 이용 불가, `null`은 미확인이다.
미확인은 `excluded=false`로 반환하며 24시간 개방으로 확정하지 않는다.
4단계 연결 완료: 추천 요청마다 CSV 변경을 확인하고, 이용 불가 후보는 `excluded`에 분리한다.
제외 후 최소 후보가 부족하면 1→2→3km로 확대하며 기존 차량 ETA는 재사용한다.
미확인 후보는 경고와 함께 유지하고 고정 피드는 최소 추천 후보 수에 포함하지 않는다.

### 2.4 경계 정책

- [x] 폐쇄시각과 예상 출차시각이 같으면 제외 — 24시간 자정 경계는 폐쇄로 취급하지 않음
- [x] 안전여유 옵션 구현 — 기본 0분, 호출 시 10~15분 등 설정 가능
- [x] 안전여유를 판정 결과에 표시 — API 입력 `access_safety_margin_minutes`와 카드 `access`에 연결
- [x] 자정 통과·요일 변경·공휴일 변경 처리 — 한국 공휴일 달력과 날짜별 조회 사용
- [x] 입차시간과 출차시간이 다른 시설 처리

### 2.5 필수 테스트

- [x] ID 39형 24시간 출입 시설의 평일 16:00~20:00: 이용 가능 판정
- [x] ID 39 평일 16:00~20:00의 60분 과금 — 1,000원, 일일권 7,000원 검증
- [x] 18:00 폐쇄 시설의 16:00~20:00: 이용 불가 판정
- [x] 도착시각부터 이미 폐쇄: 이용 불가 판정
- [x] 입차는 18시까지, 출차는 24시간 가능: 이용 가능 판정
- [x] 24시간 개방: 이용 가능 판정
- [x] 종일 무료 개방의 요금 0원
- [x] 금요일 밤부터 토요일 새벽까지 요일 경계
- [x] 일요일부터 법정공휴일까지 날짜 경계
- [x] 미확인 시설: 강제 24시간 처리 금지, 경고 반환
- [x] 제외 후 후보 반경 확대 및 후보 부족 응답

---

## 3. 예측 제공 정책 — P0

- [x] `prediction_availability.csv` 검증·로더 구현
- [x] 카드마다 `prediction_status`와 `prediction_reason` 반환
- [x] 예측 불가 시간에는 `p50`, `p10`, `p90`, `full_prob`를 `null`로 반환
- [x] 예측 불가 카드는 `full_prob` 강등에 사용하지 않음
- [x] 경직·이상 시간대의 현재값을 null 처리하고 관측 상태 표시
- [x] 학습·평가가 재사용할 `validity_mask()` 구현
- [x] 25곳·3곳 및 이상 시간대의 실제 진단 행 적재 — `scripts/build_prediction_availability.py`로 267행 산출
- [x] 정상 시간대 데이터만 모델 학습·평가에 사용하도록 마스크 연결 — `u11_evaluate.split_frame`이 `validity_mask` 적용
- [x] 15·30·60·120분 horizon별 최근 이력 충족 검사 — 5분 초과 결측은 `no_fresh_history`, 학습 범위 밖은 `unsupported_horizon`
- [x] 상시 5분 폴러 유지. 요청 시 폴링은 누락 방지용으로만 사용
- [ ] 일일 데이터 연속성·고정값·정원 초과·급변 감시 — 결측(`check_gaps`)·최신성(`watch_freshness`)·고정값(`dead_feed`)만 있고 정원 초과·급변 감시 없음

게이트는 **fail-closed**다. `prediction_availability.csv`가 비어 있으면 아무것도 막지 않는 게
아니라 **모든 예측이 차단**된다. 그래서 진단 적재는 선택이 아니라 서비스 동작 조건이다.
진단은 `scripts/build_prediction_availability.py`가 `parking.db`에서 만든다 — 조사 결과가 아니다.
주차장 × 요일그룹 × 시(hour) 버킷을 표본 부족 → 정원초과·급변 → 경직 순으로 판정하고,
열린 시만 이어 붙여 `prediction_windows`로 쓴다. 근거는
`reports/tables/prediction_availability_diagnosis.csv`에 버킷 단위로 남는다.
현재 결과: 고정 피드 21곳 전면 차단, 68곳 개방, 그중 49곳은 일부 시간대만 차단.
이 21곳은 분석본 `data/processed/parking_access_rules.csv`의 `feed_status=FIXED` 21곳과 정확히 일치한다.

학습·평가는 `split_frame`에서 같은 게이트를 통과한 행만 쓴다. 서비스가 막는 시간대의 성능을
리포트에 합산하지 않기 위해서다. 게이트가 비어 있으면 마스크를 적용하지 않고 경고만 남긴다 —
fail-closed 게이트로 학습 표본을 0으로 만들지 않기 위한 예외다.

사용자 표시 문구:

> 현재 시간대에는 혼잡도 예측을 제공하지 않아요. 위치와 요금을 기준으로 안내해요.

---

## 4. 요금 API와 계산기 보강 — P0

현재 추천 API 안에서 요금을 계산하지만, UI에서 시간·할인만 바꿔 빠르게 다시 계산할 수 있도록 독립 견적 API를 추가한다.

### 4.1 `POST /api/v1/fare/quote`

요청 예시:

```json
{
  "parking_id": 39,
  "arrival_at": "2026-09-15T16:00:00+09:00",
  "parking_minutes": 240,
  "benefit_codes": []
}
```

응답 예시:

```json
{
  "parking_id": 39,
  "arrival_at": "2026-09-15T16:00:00+09:00",
  "expected_departure_at": "2026-09-15T20:00:00+09:00",
  "access": {"available": true, "status": "confirmed_open"},
  "fare": {
    "billable_minutes": 60,
    "free_minutes_outside_fee_window": 180,
    "payg": 1000,
    "daily_pass": 7000,
    "recommended_option": "payg",
    "saving": 6000,
    "applied_benefit": null,
    "breakdown": []
  }
}
```

7단계 구현 완료: `src/serve/fare_quote.py`의 `quote_fare()`가 추천을 다시 돌리지 않고
`parking_id`·도착시각·주차시간·감면 코드만으로 견적을 만든다. 추천과 같은 `calc_fare`와
`check_access`를 쓰므로 두 경로의 금액이 어긋나지 않는다. 출입 판정은 요금 계산을 막지 않는다 —
이용 불가여도 참고 요금을 돌려주고 `access.available=false`로 알린다.
`benefit_codes`는 지금 0~1개만 받는다. 중복 감면은 조례 확인 전까지 곱하지 않고
`multiple_benefits_unsupported`(422)로 거절한다 — 10단계에서 항목별 계산으로 바꾼다.
일일권은 `recommended_option`·`saving`으로 병기만 하고 `daily_pass_purchasable`은 null이다.

### 4.2 계산 보강

6단계 연결 완료: 추천 카드의 `fare`에 계산 내역·유료분·무료분·날짜별
`fee_schedule`과 `fee_source`를 반환한다. 조사 요금 미확정·누락 시 기존 DB 시간으로
계산하되 `legacy_db_unverified`, 일부 날짜만 확정이면 `mixed`로 표시한다.
복수 날짜에 유료분이 존재하면 누진·감면·일일권 규칙 검증 전까지 총액을 null로 반환한다.
이 제한은 아래 날짜별 계산 보강 작업을 대신 완료한 것으로 취급하지 않는다.

- [x] `fee_windows`를 조사 데이터에서 읽어 실제 유료분만 계산
- [x] 평일·토요일·일요일·법정공휴일 그룹 조회
- [x] 실제 법정공휴일 달력 연결 — 조사 규칙 경로
- [x] 주차장별 토요일·공휴일 규칙 우선 적용
- [ ] 24시간을 넘는 주차의 날짜별 누진·일 상한 재계산
- [ ] 입차 당일 기준 일일권의 이용 범위와 구매 가능 조건 확정
- [x] 일일권 판매 여부·매진 정보가 없으면 가격만 안내하고 구매 가능으로 단정하지 않음 — `daily_pass_purchasable`은 항상 null
- [x] 후불과 일일권을 자동 합치지 않고 두 상품을 병기 — `apply_daily_pass_cap` 기본 False
- [ ] 누진구간·무료구간·감면·100원 절사 순서가 보이는 `breakdown` 반환 — 누진 구간만 행으로 있고 무료·감면·절사는 별도 필드
- [x] 계산 불가 사유를 `null + reason`으로 반환하고 임의 추정 금지

---

## 5. 할인 자격과 마이페이지 — P1

### 5.1 추천 API 입력 변경

기존 `discount: string | null`은 하위 호환으로 유지한 뒤 `benefit_codes: string[]`로 전환한다.

- [ ] 복수 자격별 요금을 각각 계산
- [x] 조례상 중복 가능 여부를 확인하기 전에는 감면율을 임의로 곱하지 않음 — 복수 코드는 `multiple_benefits_unsupported`로 거절
- [ ] 중복 불가라면 적용 가능한 단일 혜택 중 최저요금 선택
- [ ] 선택된 혜택과 탈락한 혜택의 사유 반환
- [ ] 현장 증빙 필요 문구 표시 — `fare/quote`의 `applied_benefit.evidence_note`까지만, UI 미연결
- [ ] `GET /api/v1/benefits`로 지원 코드·감면 내용·증빙 안내 제공

### 5.2 로그인 없는 1차 마이페이지

- [ ] 할인 품목 선택 UI
- [ ] 선택값을 브라우저 로컬 저장소에 보관
- [ ] 장애 세부등급·증명서·주민번호 등 증빙정보 수집 금지
- [ ] 추천/요금 요청에 선택한 혜택 코드만 전송
- [ ] 설정 전체 삭제 기능

이 단계는 계정 없이도 할인 맞춤 계산을 완성할 수 있으므로 OAuth보다 먼저 한다.

### 5.3 카카오 OAuth — P1 후반

다른 기기에서도 설정을 동기화해야 할 때 추가한다.

- [ ] `GET /api/v1/auth/kakao/login`
- [ ] `GET /api/v1/auth/kakao/callback`
- [ ] `POST /api/v1/auth/logout`
- [ ] `GET /api/v1/me`
- [ ] `PUT /api/v1/me/preferences`
- [ ] `DELETE /api/v1/me`
- [ ] OAuth `state` 검증, 안전한 세션 쿠키, 토큰 암호화/폐기 정책
- [ ] 카카오 계정 식별자와 최소 설정만 저장
- [ ] 할인 자격 증명자료는 저장하지 않음

---

## 6. 장소 검색 API — P0

### `GET /api/v1/places/search?q=안양시청`

8단계 구현 완료: `src/serve/places.py`. 카카오 좌표는 `x=경도, y=위도`라 `_place()` 한 곳에서만
뒤집고 바깥으로는 `lat`/`lng` 이름으로만 내보낸다. **결과 없음과 검색 불가를 섞지 않는다** —
빈 배열은 "검색은 됐고 결과가 없다", 503 `search_unavailable`은 "검색 자체를 못 했다"이다.
카카오가 죽으면 24시간 이내 캐시를 `stale=true`로 내보내고, 그마저 없으면 503을 준다.

- [x] 카카오 로컬 키워드/주소 검색 연결 — 키워드 우선, 주소 검색으로 보완
- [x] 안양시 중심 또는 경계로 결과 우선순위 제한 — 경계 밖은 버리지 않고 뒤로 민다
- [x] 이름·도로명주소·위도·경도 반환
- [x] 좌표 순서 `lng,lat` 혼동 방지 테스트 — x/y 뒤집기는 `_place()` 한 곳에서만
- [x] 짧은 TTL 캐시와 요청 제한 — 300초 TTL, 프로세스당 초당 5회
- [x] API 키를 브라우저에 노출하지 않음 — 서버에서만 호출, 응답에 원천 본문 미포함
- [x] 카카오 실패 시 최근 캐시 또는 명확한 검색 불가 응답 — `stale=true` 또는 503 `search_unavailable`

---

## 7. 추천 API 응답 완성 — P0

- [ ] 카드의 `lat/lng`를 웹 지도 핀과 연결
- [x] `access_status`, `expected_departure_at`, `prediction_status` 추가
- [x] `fare` 객체에 후불·일일권·할인·계산 내역 포함
- [ ] `by_walk`, `by_fare`, `excluded`, `live_unavailable` 의미 분리 — 역할은 나뉘었고 `live_unavailable` 이름만 아직 `unavailable`
- [ ] 만차확률 강등 전/후 순위와 강등 이유 반환
- [x] 후보 부족·경로 실패·폴링 실패·예측 불가를 서로 다른 상태로 반환 — `exhausted`/`estimated`/`poll`/`prediction_reason`
- [ ] 프런트엔드용 TypeScript 타입과 실제 응답 fixture 3개 생성 — fixture 3개는 `web/fixtures/`에 있고 TS 타입이 없음
- [ ] 현재 `web/src/data.ts`의 가짜 데이터를 실제 API 호출로 교체
- [ ] predictor.pkl·KAKAO_REST_KEY 확보 후 generate_fixtures.py 재실행 → ui_*.png 3장 다시 촬영(도착 시점 예측 줄이 나와야 제출용으로 완성)

---

## 8. 예측 검증을 꾸준히 돌리는 작업 — P0/P1

### 세부 단계

| 단계 | 범위 | 상태 |
|---|---|---|
| 8-1 | 공통 평가 기반: 지평선 15~1440분 확장, 기준선 4종, 평가행·제공률 진단 | 완료 |
| 8-2 | A23 본실험: 지평선별 M0 vs 최강 기준선, 인증/실패 지평선 판정 | 완료(partial) |
| 8-3 | A23 모델 보강: M1(`lag_24h`/`lag_7d`/time-of-week/공휴일), 360분 이상 M2 Prophet 비교군 | 완료(partial) |
| 8-4 | A24: 글로벌 G0 vs 개별 L0 vs 글로벌+잔차 보정 H0 | 대기 |
| 8-5 | 만차 강등 가치 검증 A/B/C와 `full_prob` 보정·cutoff 비교 | 대기 |
| 8-6 | 매일 감시 자동화: 최신성·결측·고정값·정원 초과·급변 | 대기 |
| 8-7 | 데이터 추가 시 재평가 루틴과 U11 구간 커버리지 재평가 | 대기 |

8-1 완료: `src/analysis/a23_common.py`. A22의 `label_valid` 게이트와 A20의
history/future 연속성 공식을 그대로 재사용하고, 기준선 persistence·`lag_24h`·`lag_7d`·
요일×시각 seasonal naive를 같은 평가행에 붙인다. seasonal naive는 홀드아웃 이전
관측만으로 집계하고 표본 2개 미만은 제공하지 않는다. 결측 기준선을 근처 값으로
채우지 않으므로 인증 비교는 기준선이 모두 정의된 행에서만 한다.
진단 결과는 [`../../reports/tables/a23_coverage.csv`](../../reports/tables/a23_coverage.csv).

현재 데이터(18일·66곳)의 제약 두 가지를 8-2 전에 확정해야 한다.

- `lag_7d` 제공률이 36~58%뿐이라 네 기준선 공통 행은 평가행의 절반 아래다. 그래서
  행 집합을 `core`(persistence·`lag_24h`·seasonal naive)와 `all`(+`lag_7d`) 두 벌로 만들고
  어느 쪽 수치인지 항상 함께 보고한다. `performance_plan.md`도 `lag_7d`를 "가능한 경우"로 둔다.
- 720·1440분은 평가 가능 주차장이 66곳에서 43곳으로 줄고, `all` 집합의 1440분 주말 공통 행은
  0이다. 이 지평선은 현재 데이터로 인증할 수 없으며 통과로 처리하지 않는다.

8-2 완료: `src/analysis/a23_horizon_curve.py`. 지평선마다 M0을 직접 학습하고
같은 평가행에서 기준선과 비교한다. 결과는 [`../../reports/tables/a23_certification.csv`](../../reports/tables/a23_certification.csv),
근거 수치는 [`../../reports/performance_status.md`](../../reports/performance_status.md).
test 구간에 주말이 1회뿐이라 "주말 2회 재현" 조건을 채울 수 없어 **상태는 `partial`**이고
인증 지평선은 없다. 그 조건만 뺀 탐색 결과는 **core 행집합 60분**이다.

fold를 정답 시각 기준으로 나눴다. A20의 관측시각 기준 test 창은 지평선이 하루에
가까워지면 행이 사라져 1440분에서 정의상 0이 된다. 이 변경은 A23에만 적용했고
A20/A22 산출물은 건드리지 않았다.

8-3 완료: `src/analysis/a23_model_upgrade.py`(M1), `src/analysis/a23_prophet_compare.py`(M2).
M0과 M1은 같은 fold·같은 평가행·같은 기준선을 쓴다. M1 추가 피처의 결측으로 행을 빼지 않고
LightGBM이 그대로 학습하게 해 두 모델의 분모를 맞췄다.

- M1이 8-2에서 120분을 막았던 **주차장 합격률을 0.788 → 0.803**으로 올려 탐색 상한이
  **60분 → 120분**으로 늘었다. 인증은 여전히 주말 2회 확보 후다.
- 180·240분은 M1이 오히려 나빠진다(-3.1%, -2.4%). 전 구간 채택할 근거는 없다.
- 720분은 M1이 MAE 8.538 → 7.144(-16.3%), 합격률 0.744 → 0.860으로 가장 크게 좋아지지만
  날짜별 방향이 재현되지 않아 인증 대상이 아니다.
- `tgt_is_holiday`는 데이터 기간(08-31~09-18)에 공휴일이 없어 모든 지평선에서 상수다.
  추석(9/24~26) 데이터가 쌓이기 전에는 기여할 수 없다.
- M2(Prophet)는 360·720·1440분 모두 M1보다 나쁘다(9.255/7.986/12.203). 720분에서만 M0을
  이긴다. 비교군으로만 기록하고 앙상블·서비스에 넣지 않는다. `prophet`은 실험 전용 의존성이다.

### 재정립한 P0 목표

- [x] A23: 15/30/60/120/180/240/360/720/1440분 직접 예측
- [ ] MAE 10%p 이하이면서 최강 기준선보다 우수한 최대 지평선 산출 — 탐색값 60분, 인증은 주말 2회 확보 후
- [x] `lag_24h`, `lag_7d`, time-of-week를 현재 글로벌 모델과 비교 — 지평선별로 효과가 갈린다
- [x] 360분 이상에서 Prophet을 비교군으로만 평가 — 세 지평선 모두 M1에 미달
- [ ] A24: 글로벌 vs 개별 vs 글로벌+주차장별 잔차 보정 비교
- [ ] GITS는 QA → 지역별 의미 검증 → 다지역 사전학습 순으로 실험

### 매일

- [ ] 최신 폴링 시각과 5분 이상 결측 확인
- [ ] 살아 있는 피드·고정 피드·예측 준비 주차장 수 확인
- [ ] 정원 초과, 0/100 급변, 비정상 고정값 확인

### 데이터가 충분히 추가될 때

- [ ] 15·30·60·120분 horizon별 rolling-origin 재평가
- [ ] 평일/주말 × 예측 가능시간/불가시간 층화
- [ ] persistence 대비 MAE 확인
- [ ] `full_prob` calibration과 cutoff 0.4/0.5/0.6 비교
- [ ] 강등 on/off 순위 변경률
- [ ] 기존 1위가 도착 시 실제 만차였던 비율
- [ ] AI 강등이 그 헛걸음을 걸러낸 비율
- [ ] U11 구간 커버리지 재평가. 통과한 구간만 UI에 표시
- [ ] 평가불가·미달을 성공으로 합산하지 않고 리포트 제목에 `partial` 반영

---

## 9. 운영·배포 — P1

- [x] GCP에서 `scripts/run_poll.sh` 5분 상시 실행 및 단일 인스턴스 보장
- [ ] API 프로세스 재시작 정책과 1 worker 원칙 적용 — 1 worker만 적용, 재시작 정책 없음
- [ ] `parking.db` 백업·복구와 읽기/쓰기 잠금 점검 — `pull_vm.sh`가 `.backup`으로 회수, 복구 절차 미정
- [ ] CORS 운영 도메인 제한 — `CORS_ORIGINS` 환경변수는 있으나 localhost 정규식이 항상 열려 있음
- [ ] 카카오 키·OAuth secret을 환경변수/Secret Manager로 관리 — 환경변수·`.env`까지 적용, Secret Manager 미적용
- [ ] `/health`를 모니터링에 연결
- [ ] 폴링·경로·검색 API 실패율과 응답시간 기록
- [x] 사용자 응답에 내부 경로·키·원천 응답 본문 노출 금지

---

## 10. 후순위 기능 — P2

- [ ] `GET /api/v1/parkings/{id}` 상세 API
- [ ] 즐겨찾기·최근 목적지
- [ ] 월정기권/일정기권 정보 표시
- [ ] 판매 가능일·매진·재고는 계약/공식 데이터가 확보된 경우에만 제공
- [ ] 사용자 제보와 관리자 검수 흐름
- [ ] 조사 규칙의 만료 알림 및 재검증 큐

---

## 11. 실제 구현 순서

1. ✅ `parking_access_rules.csv` 스키마와 검증기
2. ✅ CSV 로더 구현 — 빈 데이터셋에서도 기동하고, 확인된 행만 읽도록 구성
3. ✅ 도착·출차 시각 기반 출입 판정기와 단위 테스트
4. ✅ 추천 API에서 이용 불가 후보 제외 및 후보 반경 재확장
5. ✅ `prediction_availability.csv` 스키마·로더와 시간대별 예측 차단 엔진
6. ✅ 요금 계산기를 조사된 `fee_windows`에 연결
7. ✅ `POST /api/v1/fare/quote`
8. ✅ `GET /api/v1/places/search`
9. 웹 UI 실제 추천·검색·요금 응답 연결
10. 복수 할인 자격과 로그인 없는 마이페이지
11. 필요 시 카카오 OAuth와 설정 동기화
12. 배포·모니터링·반복 예측 검증

조사 결과 수동 적재는 개발 단계와 병렬로 계속하며, 실제 서비스 검증 전까지 완료한다.
기능 동결 전 필수 범위는 1~9다. OAuth·정기권·사용자 제보는 핵심 추천 흐름이 안정된 뒤 진행한다.
