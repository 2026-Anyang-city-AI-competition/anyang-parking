# anyang-parking

안양시 미계측 주차구간 점유율 추정 — 2026 안양시 공공데이터·AI 경진대회 출품작.

계측되는 공영주차장 **89곳**의 실시간 점유율(PARK_COUNT / CELL_CNT)로 학습해,
계측되지 않는 구간(주로 무료 공영 노외 **20~33곳 / 1,270면**)의 점유율을
전이학습으로 추정하고 현장 실측으로 검증한다.

## 일정
| 날짜 | 항목 |
|---|---|
| 상시 | 5분 폴링 (`scripts/run_poll.sh`) |
| 9/1 전후 | 사전 검증 게이트 (ICC / 전이 가능성 ρ / VIF) |
| 9/6~7 | 타깃 무료 주차장 5~8곳 현장 실측 |
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
bash scripts/run_poll.sh status # 상태 확인
bash scripts/run_poll.sh stop   # 중지
```

## 구조
```
data/raw/        parking.db(폴링) · std_parking.csv · gg_*.csv
data/interim/    lots.parquet · obs.parquet · geocode_cache.csv
data/processed/  features.parquet
src/collect/     poll_parking.py · fetch_kotsa.py · geocode.py
src/features/    spatial.py · temporal.py · build.py
src/analysis/    a01_sanity.py · a02_target_eda.py · a03_variance.py · a04_feature_corr.py
src/models/      train_a.py · train_b.py · evaluate.py
reports/         figures/ · tables/   ← 발표자료로 직행
```

작업 규칙은 [CLAUDE.md](CLAUDE.md) 참조.

> ⚠️ 라벨 수집에 쓰는 `parking.auc.or.kr` API는 비공식 내부 API다.
> 학습·검증용으로만 쓰고 공모전 신청서의 "활용 공공데이터" 항목에 기재하지 않는다.
> 폴링 간격은 5분 이상을 유지한다.
# anyang-parking
