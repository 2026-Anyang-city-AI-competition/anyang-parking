# anyang-parking

안양시 미계측 주차구간 점유율 추정 — 2026 안양시 공공데이터·AI 경진대회 출품작.

계측되는 공영주차장 **89곳**의 실시간 점유율(PARK_COUNT / CELL_CNT)로 학습해,
계측되지 않는 구간(무료 공영 노외 **~18~25곳**)의 점유율을 전이학습으로 추정하고
현장 실측으로 검증한다. 대상은 **공영 한정**, 지역은 **안양 한정**(결정 확정 9/1).

## 현재 상태 (9/1 게이트 판정)

| | 합격선 | 현재 | |
|---|---|---|---|
| 행정용 — level 순위 | Spearman ρ ≥ 0.6 | **0.665** | 🟢 통과 |
| 시민용 — 시간대별 | LOO MAE ≤ 15%p | **20.6%p** | 🔴 미달 |
| 오라클(구조 천장) | — | 4.3%p | 구조는 건강 |

병목은 shape(17.4%p) > level(13.2%p). **상권·세대수 피처가 유일한 레버**다.
`is_operating` 을 넣어도 20.7 → 20.6%p 로 0.1%p 밖에 안 줄었다.
자세한 내용은 `reports/tables/a05_shape_check.md`.

## 일정
| 날짜 | 항목 |
|---|---|
| 상시 | 5분 폴링 (`scripts/run_poll.sh`) |
| ~~9/1~~ | ✅ 사전 검증 게이트 판정 완료 (`a04`·`a05`) |
| **9/2~5** | **경기데이터드림 상권·공동주택 확보** ← 최우선 |
| 9/2~5 | 표준데이터 좌표 60곳 지오코딩 |
| **9/6** | 게이트 재실행 (주말 + 상권 피처) ← 분기점 |
| 9/6~7 | 타깃 무료 주차장 **5곳** 현장 실측 (2인 전원) |
| **9/17** | 기능 동결 |
| **9/21** | 서류 제출 |
| 10/30 | 발표평가 |

## 설치
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 실행
```bash
bash scripts/run_poll.sh        # 폴링 시작 (중복 실행 방지, nohup)
bash scripts/pull_vm.sh         # VM에서 DB 회수 → 병합 → 결측 점검 (수동)
bash scripts/auto_pull.sh       # 위를 잠금·로깅 붙여 실행 (cron 이 3시간마다 호출)
python3 scripts/check_gaps.py   # 폴링 결측 구간 확인 (venv 필요)
python3 src/analysis/a05_shape_check.py   # 게이트 재실행
```

## 추천 API

모델은 서버 프로세스 시작 때 한 번만 로드한다. 추천 요청이 오면 `parking.db`의
최신 관측을 확인하고 5분 이상 지났을 때만 원천을 한 번 폴링한 뒤 최근 피처를
갱신한다. 동시 요청은 한 번으로 합치며, 폴링 실패 시 기존 DB로 응답한다.
메모리 중복을 막기 위해 데모 환경에서는 worker를 1개로 둔다.

```bash
pip install -r requirements.txt
bash scripts/run_api.sh
```

- API 문서: `http://127.0.0.1:8000/docs`
- 상태 확인: `GET /api/v1/health`
- 추천: `POST /api/v1/recommend`

```json
{
  "destination": {"lat": 37.394259, "lng": 126.956861},
  "origin": {"lat": 37.4018, "lng": 126.9226},
  "parking_minutes": 120,
  "depart_in_minutes": 0,
  "minimum_candidates": 5,
  "full_probability_cutoff": 0.5,
  "include_alternatives": true
}
```

응답의 `service.data_status`는 `fresh`/`stale`/`unavailable`이며 카드마다
`observation_at`, `observation_age_min`, `observation_status`가 포함된다.
`poll.status`는 `polled`/`skipped`/`failed` 중 하나다.
예측에 필요한 최근 이력이 부족하면 수치를 꾸며내지 않고
`prediction_source: "no_fresh_history"`와 null 예측값을 반환한다.

## 입출차 조사 데이터

`reports/태영.txt` 같은 조사 메모는 서비스가 직접 읽지 않는다. 조사 완료분을
`data/raw/parking_access_rules.csv`에 구조화해서 적고 아래 명령으로 검증한다.

```bash
python3 scripts/validate_access_rules.py
```

새 환경에서는 `schemas/parking_access_rules.template.csv`를
`data/raw/parking_access_rules.csv`로 복사해 시작한다. 검증기는 DB에 없는 ID,
주차장명 불일치, 요일 누락·중복, 잘못되거나 겹치는 시간, 근거 없는 확정값,
겹치는 적용기간을 실패 처리한다. `00:00-00:00`은 의미를 추정하지 않고 오류로 처리한다.
API 서버는 시작할 때 검증된 확정 규칙만 메모리에 올린다. 빈 CSV에서도 정상 기동하며,
잘못된 파일로 갱신되면 마지막 정상 규칙을 유지하고 `/api/v1/health`의
`access_rules.status`를 `invalid`로 표시한다.
추천 요청마다 CSV 변경을 검사하고 실제 도착·출차시간에 이용 불가한 후보는
`excluded`로 분리한다. 부족하면 최대 3km까지 확대하며 미확인은 카드의 `access`에
경고로 반환한다. `access_safety_margin_minutes`로 출차 안전여유(기본 0분)를 지정할 수 있다.

## 예측 제공 시간 게이트

`schemas/prediction_availability.template.csv`의 형식으로
`data/processed/prediction_availability.csv`에 진단된 허용시간을 적는다.
`prediction_windows`는 **예측 허용시간**이며, `frozen`·`anomaly` 행도 정상 시간창만
넣으면 그 안에서는 예측이 가능하다. `evaluation_pending`·`dead_feed`는 시간창을 비운다.
관측 시각과 도착 시각이 모두 허용시간에 있어야 모델을 호출한다.

API는 파일 변경을 확인하고 `prediction_status/reason`을 카드에 반환한다.
빈 파일·누락 규칙·불량 파일은 예측을 허용하지 않는다. 현재 빈 파일이므로
API 예측은 미검증 상태로 null을 반환하며 위치·요금 추천은 유지한다.
실제 진단 행 적재와 기존 학습·평가 배치의 `validity_mask()` 연결은 별도 작업이다.

## 조사된 유료시간 요금 계산

추천 API는 출입 규칙 CSV의 `fee_windows`로 실제 과금 구간만 계산한다.
카드 `fare`에 누진 내역, `billable_min`, `free_minutes_outside_fee_window`,
날짜별 `fee_schedule`을 반환하며 후불과 선불 일일권은 계속 별도로 표시한다.
조사 규칙이 없으면 `fee_source=legacy_db_unverified`, 혼합되면 `mixed`를 표시한다.
복수 날짜의 유료주차는 적용 규칙을 검증할 때까지 요금을 null과 사유로 반환한다.

## 구조
```
data/raw/        parking.db(폴링 라벨) · kotsa_v2_*.jsonl(공단 시설정보)
data/interim/    lots.parquet · obs.parquet · geocode_cache.csv
data/processed/  features.parquet
src/config.py    경로·상수 단일 소스 (RANDOM_STATE=42)
src/collect/     poll_parking.py · fetch_kotsa.py · probe_params.py · geocode.py
src/features/    spatial.py · temporal.py · build.py
src/analysis/    a04_feature_corr.py · a05_shape_check.py · a01~a03(스텁)
src/models/      train_b.py · evaluate.py     ← 모델 A 는 2인 팀이라 잘라냄
src/utils/       merge_db.py · geo.py · kr_holidays.py
scripts/         run_poll.sh · pull_vm.sh · check_gaps.py · compare_kotsa.py
reports/         figures/ · tables/   ← 발표자료로 직행
```

## 문서
- **[공용-프로젝트-컨텍스트.md](공용-프로젝트-컨텍스트.md) — 정본.** 배점·일정·데이터 현황·기술 설계·작업 순서 전부.
  데스크탑에서 갱신하고 이 리포에 덮어쓴다.
- [CLAUDE.md](CLAUDE.md) — 정본에서 **코딩 규칙만 추린 파생본.** Claude Code가 자동으로 읽는다.
  정본이 갱신되면 다시 추려 쓴다. 두 문서가 충돌하면 **정본이 이긴다.**

> ⚠️ 라벨 수집에 쓰는 `parking.auc.or.kr` API는 비공식 내부 API다.
> 학습·검증용으로만 쓰고 공모전 신청서의 "활용 공공데이터" 항목에 기재하지 않는다.
> 폴링 간격은 5분 이상을 유지한다.
