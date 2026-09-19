"""경로·상수 단일 소스. 코드에 경로를 하드코딩하지 말고 여기서 읽는다."""
from pathlib import Path

RANDOM_STATE = 42

ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"
PROCESSED = DATA / "processed"

REPORTS = ROOT / "reports"
FIGURES = REPORTS / "figures"
TABLES = REPORTS / "tables"
LOGS = ROOT / "logs"

# 원천
PARKING_DB = RAW / "parking.db"                 # 폴링 라벨 (비공식 내부 API)
ACCESS_RULES_CSV = RAW / "parking_access_rules.csv"  # 조사 확정 출입·과금 규칙
PREDICTION_AVAILABILITY_CSV = PROCESSED / "prediction_availability.csv"
# 주차장 × 지평선 정확도 게이트. 위 파일이 "값이 움직이는가"라면 이쪽은 "맞히는가"다.
PREDICTION_ACCURACY_CSV = PROCESSED / "prediction_accuracy.csv"
# 만차확률을 숫자로 보여줘도 되는 지평선. 순위 사용 여부와는 별개다.
PROBABILITY_CALIBRATION_CSV = PROCESSED / "probability_calibration.csv"
STD_PARKING_CSV = RAW / "std_parking.csv"       # 경기데이터드림 제공표준 107곳
SHOPS_CSV = RAW / "gg_shops.csv"
APT_CSV = RAW / "gg_apt.csv"
AWS_CSV = RAW / "gg_aws.csv"
ENFORCE_CSV = RAW / "gg_enforce.csv"
BUSSTOP_CSV = RAW / "gg_busstop.csv"

# 중간·최종 산출물
LOTS_PARQUET = INTERIM / "lots.parquet"
OBS_PARQUET = INTERIM / "obs.parquet"
GEOCODE_CACHE = INTERIM / "geocode_cache.csv"
FEATURES_PARQUET = PROCESSED / "features.parquet"
# 기존 A18/A19 분석용 가로형 CSV. 서비스의 raw/ACCESS_RULES_CSV와 스키마가 다르다.
PARKING_ACCESS_RULES_CSV = PROCESSED / "parking_access_rules.csv"

# 수집
LABEL_API = "https://parking.auc.or.kr/api/parking/searchParkingList"
POLL_INTERVAL_SEC = 300         # 5분 미만으로 내리지 말 것
STALE_HOURS = 24                # PARK_COUNT 이 이 시간 이상 불변이면 계측 불가 lot

for _d in (RAW, INTERIM, PROCESSED, FIGURES, TABLES, LOGS):
    _d.mkdir(parents=True, exist_ok=True)
