from pathlib import Path

import openmeteo_requests
import pandas as pd
import requests_cache
from retry_requests import retry


ROOT = Path(__file__).resolve().parents[2]

OUT_CSV = (
    ROOT
    / "data/raw/weather.csv"
)

# 안양시 중심부
LATITUDE = 37.3943
LONGITUDE = 126.9568

START_DATE = "2026-08-31"
END_DATE = "2026-09-05"


def main():

    print("=" * 70)
    print("A14-0 Download Weather - Open-Meteo Historical Forecast")

    # -----------------------------------------------------
    # API client
    # -----------------------------------------------------
    cache_session = requests_cache.CachedSession(
        ".cache",
        expire_after=-1,
    )

    retry_session = retry(
        cache_session,
        retries=5,
        backoff_factor=0.2,
    )

    openmeteo = openmeteo_requests.Client(
        session=retry_session
    )

    # -----------------------------------------------------
    # request
    # -----------------------------------------------------
    url = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast"
    )

    params = {
    "latitude": 37.3943,
    "longitude": 126.9568,

    "start_date": "2026-08-31",
    "end_date": "2026-09-05",

    "hourly": [
        "temperature_2m",
        "relative_humidity_2m",
        "precipitation",
        "wind_speed_10m",
    ],

    "timezone": "Asia/Seoul",
}

    responses = openmeteo.weather_api(
        url,
        params=params,
    )

    response = responses[0]

    timezone = (
        response.Timezone()
        .decode()
    )

    print()
    print("[응답 정보]")
    print(
        f"Coordinates : "
        f"{response.Latitude():.4f}, "
        f"{response.Longitude():.4f}"
    )

    print(
        f"Elevation   : "
        f"{response.Elevation()} m"
    )

    print(
        f"Timezone    : "
        f"{timezone}"
    )

    print(
        f"UTC offset  : "
        f"{response.UtcOffsetSeconds()} sec"
    )

    # -----------------------------------------------------
    # hourly data
    # -----------------------------------------------------
    hourly = response.Hourly()

    temperature = (
        hourly
        .Variables(0)
        .ValuesAsNumpy()
    )

    humidity = (
        hourly
        .Variables(1)
        .ValuesAsNumpy()
    )

    precipitation = (
        hourly
        .Variables(2)
        .ValuesAsNumpy()
    )

    wind_speed = (
        hourly
        .Variables(3)
        .ValuesAsNumpy()
    )

    ts = pd.date_range(
        start=pd.to_datetime(
            hourly.Time(),
            unit="s",
            utc=True,
        ),
        end=pd.to_datetime(
            hourly.TimeEnd(),
            unit="s",
            utc=True,
        ),
        freq=pd.Timedelta(
            seconds=hourly.Interval()
        ),
        inclusive="left",
    ).tz_convert(timezone)

    # -----------------------------------------------------
    # dataframe
    # -----------------------------------------------------
    df = pd.DataFrame(
        {
            "ts_kst": ts,
            "temperature": temperature,
            "humidity": humidity,
            "precipitation": precipitation,
            "wind_speed": wind_speed,
        }
    )

    # 강수 여부
    df["is_rain"] = (
        df["precipitation"] > 0
    ).astype(int)

    # -----------------------------------------------------
    # diagnostics
    # -----------------------------------------------------
    print()
    print("[기간]")
    print(
        f"{df['ts_kst'].min()} "
        f"~ {df['ts_kst'].max()}"
    )

    print()
    print(
        f"총 rows : {len(df):,}"
    )

    print()
    print("[결측치]")
    print(
        df[
            [
                "temperature",
                "humidity",
                "precipitation",
                "wind_speed",
            ]
        ]
        .isna()
        .sum()
        .to_string()
    )

    print()
    print("[기상 요약]")
    print(
        df[
            [
                "temperature",
                "humidity",
                "precipitation",
                "wind_speed",
            ]
        ]
        .describe()
        .to_string()
    )

    print()
    print(
        f"강수 시간 수 : "
        f"{df['is_rain'].sum()}"
    )

    # -----------------------------------------------------
    # save
    # -----------------------------------------------------
    OUT_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    df.to_csv(
        OUT_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print(
        f"저장 완료: {OUT_CSV}"
    )


if __name__ == "__main__":
    main()