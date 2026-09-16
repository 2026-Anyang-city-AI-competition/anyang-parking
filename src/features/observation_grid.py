"""A18/A19용 관측 정렬. 관측을 실제로 받은 이후의 5분 격자에 배치한다."""
import pandas as pd

FREQUENCY = "5min"


def invalid_observations(raw):
    return (
        raw["cell_cnt"].isna() | (raw["cell_cnt"] <= 0)
        | raw["park_count"].isna() | (raw["park_count"] < 0)
        | (raw["park_count"] > raw["cell_cnt"])
    )


def observation_grid(raw, index=None):
    """이상값 제외, 다음 격자에서 이용 가능한 최신 값, 보간/앞뒤 채움 없음.

    index가 있으면 관측이 없는 앞뒤 구간도 포함한다.
    이 모듈의 100% 초과 제외 정책은 기존 U11의 clip(0, 120)과 다르다.
    모델과 기준선의 성능 비교에는 동일한 관측/평가 행을 별도로 적용해야 한다.
    """
    valid = raw.loc[~invalid_observations(raw)].sort_values("ts_kst")
    values = 100 * valid["park_count"] / valid["cell_cnt"]
    available_at = pd.DatetimeIndex(valid["ts_kst"]).ceil(FREQUENCY)
    series = pd.Series(values.to_numpy(), index=available_at, name="occ")
    series = series.groupby(level=0).last()
    if index is None:
        times = pd.DatetimeIndex(raw["ts_kst"])
        index = (pd.date_range(times.min().ceil(FREQUENCY),
                               times.max().ceil(FREQUENCY), freq=FREQUENCY)
                 if len(times) else times)
    result = series.reindex(index).to_frame()
    result.index.name = "ts_kst"
    return result
