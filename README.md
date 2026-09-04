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
