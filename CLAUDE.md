# anyang-parking

## 프로젝트
2026 안양시 공공데이터·AI 활용 대학생 경진대회 출품작.
계측되는 안양시 공영주차장 89곳의 실시간 점유율로 학습해, 계측 안 되는 구간
(주로 무료 공영 노외 20~33곳, 1,270면)의 점유율을 전이학습으로 추정하고
현장 실측으로 검증한다.
서류 마감 2026-09-21, 기능 동결 9/17, 발표평가 10/30.

## 데이터
- 라벨: https://parking.auc.or.kr/api/parking/searchParkingList
  PARK_COUNT / CELL_CNT, 89곳, 5분 폴링 → data/raw/parking.db
  비공식 내부 API. 학습·검증용으로만 사용, 공모전 신청서에 기재 금지.
- 계측 불가 lot(24h 이상 PARK_COUNT 불변)은 a01_sanity.py로 식별해 학습 제외.
- 모집단: 경기데이터드림 「주차장 정보 현황(제공표준)」 안양시 107곳
  (공영 100%, 노상 29/노외 78, 유료 71/무료 36). 좌표 60곳 결측 → 지오코딩 필요.
- 전이 타깃: 표준데이터에 있고 포털 API에 없는 ~20~33곳. 대부분 무료 노외.

## 모델링 규칙 (위반 금지)
- 점유율 = level(lot) × shape(lot,t) 분해. shape만 예측, level은 실측 앵커링.
- 전이 모델(B)에 자기회귀 lag 피처 금지. 시민 예측 모델(A)에만 허용.
- 유료 주차장의 운영시간 외(무료) 구간을 반드시 학습에 포함
  → 무료 타깃 예측이 외삽 아닌 내삽이 됨.
- 검증: Leave-One-Location-Out CV + 시간 순차 분할. 랜덤 분할 절대 금지.
- 독립 표본은 89개. 피처 10~15개, LightGBM/XGBoost 상한. 딥러닝 금지.
- 분위 예측(quantile) + isotonic calibration 필수.
- 목표: 타깃 MAE ≤ 15%p, Spearman ρ ≥ 0.6

## 코드 규칙
- 시드 고정(RANDOM_STATE=42). 경로는 config에서 읽고 하드코딩 금지.
- 모든 분석 산출물은 reports/figures/*.png, reports/tables/*.csv 로 저장.
  파일명에 날짜 넣지 말 것(발표자료 링크가 깨짐).
- 한글 폰트: matplotlib에 NanumGothic 설정, 없으면 자동 설치 시도.
