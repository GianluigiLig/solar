from __future__ import annotations

from datetime import date, datetime
from typing import Union

import pandas as pd

from data_platform.utils.diagnostics import DataValidationError, MissingColumnsError
from solar.domain.schemas import CanonicalColumns as CAN


def calculate_yom(dates: Union[pd.Series, pd.Timestamp, datetime, date], OM_date: Union[pd.Timestamp, datetime, date]) -> Union[pd.Series, int]:
    """Calculate Year of Monitoring from OM date."""
    om = pd.Timestamp(OM_date)
    if isinstance(dates, pd.Series):
        values = pd.to_datetime(dates)
        anniversary_not_reached = (values.dt.month < om.month) | ((values.dt.month == om.month) & (values.dt.day < om.day))
        return values.dt.year - om.year - anniversary_not_reached.astype(int) + 1
    value = pd.Timestamp(dates)
    anniversary_not_reached = value.month < om.month or (value.month == om.month and value.day < om.day)
    return value.year - om.year - int(anniversary_not_reached) + 1


def calculate_year_since_pac(dates: Union[pd.Series, pd.Timestamp, datetime, date], pac_date: Union[pd.Timestamp, datetime, date]) -> Union[pd.Series, int]:
    """Calculate operational year from PAC date."""
    pac = pd.Timestamp(pac_date)
    if isinstance(dates, pd.Series):
        values = pd.to_datetime(dates)
        anniversary_not_reached = (values.dt.month < pac.month) | ((values.dt.month == pac.month) & (values.dt.day < pac.day))
        return values.dt.year - pac.year - anniversary_not_reached.astype(int) + 1
    value = pd.Timestamp(dates)
    anniversary_not_reached = value.month < pac.month or (value.month == pac.month and value.day < pac.day)
    return value.year - pac.year - int(anniversary_not_reached) + 1


def get_grouping_columns_and_intervals(df: pd.DataFrame, freq: str, time_interval_hours: float) -> tuple[list[str], float]:
    if time_interval_hours <= 0:
        raise DataValidationError("time_interval_hours must be > 0")
    if freq == "day":
        return [CAN.YEAR, CAN.MONTH, CAN.DAY], float(24 / time_interval_hours)
    if freq == "month":
        if CAN.DAY not in df.columns:
            raise MissingColumnsError("DataFrame must contain day column for freq='month'")
        return [CAN.YEAR, CAN.MONTH], float(int(df[CAN.DAY].max()) * 24 / time_interval_hours)
    if freq == "year":
        if CAN.DATETIME_LOCAL not in df.columns:
            raise MissingColumnsError("DataFrame must contain datetime_local column for freq='year'")
        dates = pd.to_datetime(df[CAN.DATETIME_LOCAL]).dt.date
        days = (dates.max() - dates.min()).days + 1
        return [CAN.YEAR], float(days * 24 / time_interval_hours)
    if freq == "yom":
        if CAN.YOM not in df.columns or CAN.DATETIME_LOCAL not in df.columns:
            raise MissingColumnsError("DataFrame must contain yom and datetime_local for freq='yom'")
        dates = pd.to_datetime(df[CAN.DATETIME_LOCAL]).dt.date
        days = (dates.max() - dates.min()).days + 1
        return [CAN.YOM], float(days * 24 / time_interval_hours)
    raise DataValidationError("freq must be 'day', 'month', 'year' or 'yom'")
