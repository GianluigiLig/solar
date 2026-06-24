from __future__ import annotations

from typing import Any, Iterable

import pandas as pd

from data_platform.utils.diagnostics import EmptyDataFrameError, MissingColumnsError
from solar.domain.schemas import SilverRawColumns as RAW


def make_device_time_grid(timestamps: Iterable[Any], device_names: str | Iterable[str], value_cols: Iterable[str] | None = None) -> pd.DataFrame:
    if isinstance(device_names, str):
        normalized_device_names = [device_names]
    else:
        normalized_device_names = list(device_names)
    normalized_timestamps = pd.to_datetime(list(timestamps), utc=True)
    rows = [(timestamp, device_name) for device_name in normalized_device_names for timestamp in normalized_timestamps]
    df = pd.DataFrame(rows, columns=[RAW.DATETIME, RAW.DEVICE_NAME])
    for col in value_cols or []:
        df[col] = pd.NA
    return df


def pivot_device_measurements(df: pd.DataFrame, value_columns: list[str]) -> pd.DataFrame:
    """Pivot long silver data to the wide shape consumed by gold.

    This intentionally mirrors the historical gold pipeline behavior:
    ``pivot_table(..., aggfunc="first")`` is used so duplicated
    ``datetime/device`` records keep the first value produced by silver.
    """
    if df.empty:
        raise EmptyDataFrameError("DataFrame is empty")
    required_columns = [RAW.DATETIME, RAW.DEVICE_NAME] + value_columns
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise MissingColumnsError(f"Missing columns: {missing}")
    df_work = df[required_columns].copy()
    df_work[RAW.DATETIME] = pd.to_datetime(df_work[RAW.DATETIME], utc=True, errors="coerce")
    df_work = df_work.dropna(subset=[RAW.DATETIME, RAW.DEVICE_NAME])

    df_pivot = df_work.pivot_table(
        index=RAW.DATETIME,
        columns=RAW.DEVICE_NAME,
        values=value_columns,
        aggfunc="first",
    )
    if isinstance(df_pivot.columns, pd.MultiIndex):
        df_pivot.columns = [f"{metric}_{device}" for metric, device in df_pivot.columns]
    else:
        value_col = value_columns[0]
        df_pivot.columns = [f"{value_col}_{device}" for device in df_pivot.columns]
    return df_pivot.reset_index().sort_values(RAW.DATETIME)
