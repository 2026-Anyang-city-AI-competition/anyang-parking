# A20 — 같은 test 행에서 AI와 현재값 기준선 비교

## 실행

수정 브랜치 `codex/parking-review-fixes`에서 실행한다.

```powershell
git pull --ff-only
python src/analysis/a20_matched_model_comparison.py
```

입력은 `src/config.py`의 `PARKING_DB`와 분석용 `PARKING_ACCESS_RULES_CSV`다.
기본 파일은 `data/raw/parking.db`, `data/processed/parking_access_rules.csv`이다.
요일별 한 행의 신규 서비스용 `data/raw/parking_access_rules.csv`와 구분한다.
DB 관측과 lots는 같은 읽기 트랜잭션으로 고정하고, CSV ID/이름과 predict_ok 값을 검사한다.
CSV나 DB를 편집하지 않는다. A19의 과거 결과 파일도 입력으로 요구하지 않는다.

이미 프로젝트 의존성이 설치돼 있으면 별도 설치가 필요 없다.
LightGBM 관련 모듈이 없다는 오류가 나면 다음으로 설치한다.

```powershell
python -m pip install lightgbm scikit-learn pandas numpy
```

## 2주치 데이터를 어떻게 사용하는가

DB의 전체 관측을 읽고, 현행 분석 CSV에서 predict_ok=1인 주차장을 대상으로 한다.
출입 가능·불가·미확인 시간 모두를 학습 대상으로 두고 평가를 층화한다.
기본값은 최소 학습 기간 7일, 별도 validation 1일, 마지막 진행 중 날짜를 제외한 최근 test 3일이다.

사용자가 확인한 8/31 04:56:37~9/15 01:22:18 DB라면 다음과 같다.

| Fold | 학습할 라벨 날짜 | Validation | Test |
|---|---|---|---|
| 1 | 8/31~9/10 | 9/11 | 9/12 토요일 |
| 2 | 8/31~9/11 | 9/12 | 9/13 일요일 |
| 3 | 8/31~9/12 | 9/13 | 9/14 월요일 |

각 fold/horizon에서 새 모델을 학습한다. 다음 날 fold에서는 이미 지난 날짜의 관측을 사용할 수 있다.
라벨 시각 `target_time`이 다음 구간의 시작에 닿거나 넘어가면 앞 구간에서 제외한다.
Test도 관측 시각과 정답 시각이 모두 해당 test 날짜 안에 있어야 한다.
9/15는 수집 중인 날짜라 test에서 제외한다. DB를 갱신하면 test 날짜도 이동하며 실제 날짜를 콘솔·manifest에 남긴다.

Validation은 고정 모델의 점 예측 오차를 진단하는 데만 사용한다.
이번 실험에서는 validation/test를 보고 파라미터나 모델을 고르거나 확률·구간을 보정하지 않는다.
기간이 부족하면 실패한다. 날짜 순서를 무시한 랜덤 분할로 바꾸지 않는다.

## 두 방법을 같은 조건으로 비교

- A18/A19와 같은 `observation_grid`를 사용한다. 정원 초과·음수·결측을 제외하고 관측 이후 5분 격자에 최신 유효 값을 둔다.
- 보간·앞뒤 채움을 하지 않는다.
- A19와 같이 현재부터 도착까지 모든 5분 관측이 있는 행을 후보로 삼는다.
- AI의 최대 lag인 과거 60분의 관측도 모두 있는 행에서 두 방법을 함께 평가한다.
- AI와 Persistence는 같은 주차장·관측 시각·정답 시각·horizon의 행을 사용한다.
- AI가 NaN/무한값을 반환하면 행을 몰래 빼지 않고 실행을 실패 처리한다.

이 때문에 A20의 평가 표본 수와 기준선 점수는 A19 전체 기간 표와 다를 수 있다.
`a20_splits.csv`의 `a19_eligible_n`, `test_n`, `excluded_history_or_features_n`, `matched_ratio`로
같은 test 날짜에서 이력 부족으로 제외된 양을 확인한다.
미래 관측의 연속성을 보고 고른 후향 평가 표본이므로 이 비율을 실시간 서비스 제공률로 해석하지 않는다.

## 모델과 판독

A20은 기존 U11의 피처 정의와 Δ 중앙값 학습 방식을 재사용해 새 점 예측 모델을 학습한다.
고정 LightGBM quantile(alpha=0.5), 120 trees, learning_rate=0.08, num_leaves=31, seed=42를 사용한다.
예측은 `현재 점유율 + 예측 Δ`를 0~100%로 제한한다. 분석의 정원 초과 제외 정책에 맞춘 범위다.
Persistence는 같은 행의 현재 점유율이다. 기존 U11의 세 분위수 정렬·확률·구간 보정 모델을 재평가한 점수로 부르지 않는다.

우선 볼 행은 `access_group=accessible`이며 평일·주말을 각각 확인한다.

| 컬럼 | 의미 |
|---|---|
| n / n_lots / n_days | 평가 관측 쌍 / 주차장 / 정답 날짜 수 |
| persistence_mae / ml_mae | 동일 행에서 계산한 현재값 / AI 평균 절대오차(%p) |
| persistence_rmse / ml_rmse | 큰 오차에 더 민감한 RMSE(%p) |
| gain_pp | Persistence MAE − AI MAE. 양수면 AI의 오차 감소 |
| gain_pct | 기준선 대비 MAE 감소율. 기준선 MAE가 0이면 비워 둠 |
| status | better / tie / worse / unavailable. 관측된 MAE의 단순 비교 |

빈 평일·주말·출입 상태 조합도 n=0, status=unavailable로 남긴다.
합친 평균과 함께 날짜별·주차장별 결과를 본다. better를 통계적 유의성이나 배포 승인으로 해석하지 않는다.
겹치는 5분 관측 쌍은 서로 독립 표본이 아니며 기본 test 기간도 3일이다.

## 산출물

- `reports/tables/a20_comparison.csv`: horizon × 출입 상태 × 평일/주말 및 전체 요약
- `reports/tables/a20_by_day.csv`: 날짜별 같은 요약
- `reports/tables/a20_splits.csv`: 분할 경계·학습/검증/test 수·제외율·validation 점수
- `reports/tables/a20_per_lot.csv`: 주차장별·horizon별·출입 상태별 비교
- `reports/tables/a20_manifest.json`: 입력 해시·관측 범위·test 날짜·파라미터·버전·소스 해시
- `data/processed/a20/predictions.csv.gz`: 두 방법의 동일 행 예측값과 정답. data/ 공유 원칙에 따라 git에 넣지 않는다.

manifest의 complete는 실행 완료를 뜻한다. 빈 평가 집단 수는 별도 필드에 기록한다.

## 적용 범위

현재 predict_ok 목록을 모든 fold에서 고정한 후향 분석이다. 당시 시점의 서비스 대상 선정까지 재현한 실험은 아니다.
관측이 전혀 없는 대상은 manifest의 absent_ids에 남긴다.
출입 상태는 A19의 분석용 규칙으로 분류한다. 요금 운영시간 피처와 실제 출입 판정은 구분한다.
공휴일 예외, 출차 가능시간, 센서 경직·예측 가능 시간대는 별도 검증 대상이다.
이 결과만으로 만차 확률, 구간 커버리지 또는 추천의 헛걸음 감소를 검증했다고 주장하지 않는다.
기존 `predictor.pkl` 및 U11 산출물은 갱신하지 않는다.
