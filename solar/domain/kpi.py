from __future__ import annotations

from typing import Iterable, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from data_platform.utils.diagnostics import DataValidationError, MissingColumnsError, get_logger
from solar.config.model import SolarConfig, get_inverter_nominal_power_w_map
from solar.domain.schemas import AvailabilityColumns as AVL
from solar.domain.schemas import CanonicalColumns as CAN
from solar.domain.time import calculate_year_since_pac, get_grouping_columns_and_intervals

logger = get_logger(__name__)


def calculate_pr(net_energy, theoretical_energy, panels_degradation_factor: float = 0.0, years_from_PAC: int | float = 1):
    denominator = theoretical_energy * ((1 - panels_degradation_factor) ** max(float(years_from_PAC) - 1, 0))
    return np.where(denominator > 0, net_energy / denominator, np.nan)


def calculate_valid_interval_pct(valid_intervals, expected_intervals: float):
    if expected_intervals <= 0:
        raise DataValidationError("expected_intervals must be > 0")
    return np.round(valid_intervals / expected_intervals, 4)


def calculate_irr_mean(df: pd.DataFrame, prefix: str, outlier_detection: bool = False, outlier_threshold: float = 0.05) -> pd.DataFrame:
    cols = [c for c in df.columns if c.startswith(prefix)]
    if not cols:
        raise DataValidationError(f"No columns found with prefix '{prefix}'")
    out = df.copy()
    values = out[cols].apply(pd.to_numeric, errors="coerce")
    if outlier_detection and len(cols) > 1:
        row_mean = values.mean(axis=1, skipna=True)
        for col in cols:
            values.loc[(values[col] - row_mean).abs() > (row_mean.abs() * outlier_threshold), col] = np.nan
    out[f"{prefix}_mean"] = values.where(values > 0).mean(axis=1, skipna=True).fillna(0.0)
    return out


def compute_availability_kpis(
    df: pd.DataFrame,
    time_interval_h: float,
    group_cols: Sequence[str],
    cs_intervals: Optional[Iterable[Tuple[Union[str, pd.Timestamp], Union[str, pd.Timestamp]]]] = None,
    nominal_power_w_map: Optional[dict[str, float]] = None,
) -> pd.DataFrame:
    required = [CAN.DATETIME_LOCAL, CAN.CHECK_IRRADIANCE, *group_cols]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise MissingColumnsError(f"Missing columns in dataframe: {missing}")

    work = df.copy()
    work[CAN.DATETIME_LOCAL] = pd.to_datetime(work[CAN.DATETIME_LOCAL], errors="coerce")
    if work[CAN.DATETIME_LOCAL].isna().any():
        raise DataValidationError(f"Some values in {CAN.DATETIME_LOCAL} cannot be converted to datetime")

    pac_prefix = f"{CAN.INVERTER_ACTIVE_POWER}_"
    inv_keys = [c[len(pac_prefix):] for c in work.columns if c.startswith(pac_prefix) and c != CAN.INVERTER_ACTIVE_POWER_SUM]
    if nominal_power_w_map:
        for inv in nominal_power_w_map:
            col = f"{pac_prefix}{inv}"
            if col not in work.columns:
                work[col] = np.nan
                inv_keys.append(inv)
    if not inv_keys:
        return work[group_cols].drop_duplicates().reset_index(drop=True)

    work[AVL.CS_GLOBAL] = False
    for start, end in cs_intervals or []:
        s, e = pd.to_datetime(start), pd.to_datetime(end)
        work.loc[(work[CAN.DATETIME_LOCAL] >= s) & (work[CAN.DATETIME_LOCAL] < e), AVL.CS_GLOBAL] = True

    t_mask = work[CAN.CHECK_IRRADIANCE].astype(bool)
    cs_mask = t_mask & work[AVL.CS_GLOBAL].astype(bool)

    def grouped_steps(mask: pd.Series) -> pd.Series:
        return mask.astype(int).groupby([work[c] for c in group_cols]).sum()

    t_hours = grouped_steps(t_mask) * time_interval_h
    cs_hours = grouped_steps(cs_mask) * time_interval_h
    out = pd.DataFrame({AVL.T_HOURS: t_hours, AVL.CS_HOURS: cs_hours})
    denom_std = out[AVL.T_HOURS] - out[AVL.CS_HOURS]
    denom_no_cs = out[AVL.T_HOURS]

    for inv in inv_keys:
        col = f"{pac_prefix}{inv}"
        pac = work[col].fillna(0)
        os_std = grouped_steps(t_mask & ~cs_mask & (pac == 0)).reindex(out.index, fill_value=0) * time_interval_h
        os_no_cs = grouped_steps(t_mask & (pac == 0)).reindex(out.index, fill_value=0) * time_interval_h
        out[f"{AVL.DOWNTIME_HOURS_PREFIX}{inv}"] = os_std.values
        out[f"{AVL.AVAILABILITY_PREFIX}{inv}"] = np.round(np.where(denom_std > 0, (out[AVL.T_HOURS] - out[AVL.CS_HOURS] - os_std) / denom_std, np.nan), 4)
        out[f"{AVL.AVAILABILITY_NO_CS_PREFIX}{inv}"] = np.round(np.where(denom_no_cs > 0, (out[AVL.T_HOURS] - os_no_cs) / denom_no_cs, np.nan), 4)

    if nominal_power_w_map:
        present = [inv for inv in inv_keys if inv in nominal_power_w_map]
        total_pnom = sum(float(nominal_power_w_map[i]) for i in present)
        if total_pnom > 0:
            out[AVL.PLANT_AVAILABILITY] = np.round(sum(float(nominal_power_w_map[i]) * out[f"{AVL.AVAILABILITY_PREFIX}{i}"] for i in present) / total_pnom, 4)
            out[AVL.PLANT_AVAILABILITY_NO_CS] = np.round(sum(float(nominal_power_w_map[i]) * out[f"{AVL.AVAILABILITY_NO_CS_PREFIX}{i}"] for i in present) / total_pnom, 4)

    out = out.reset_index().drop(columns=[AVL.T_HOURS, AVL.CS_HOURS], errors="ignore")
    return out


class SolarKpiProcessor:
    """Solar KPI processor kept outside the generic medallion pipelines."""

    def calculate(
        self,
        df: pd.DataFrame,
        config: SolarConfig | dict,
        *,
        freq: str,
        day: int,
        month: int | None = None,
        year: int | None = None,
        cs_intervals: list | None = None,
    ) -> pd.DataFrame:
        cs_intervals = cs_intervals or []
        if isinstance(config, SolarConfig):
            raw = config.raw_config
            time_interval = config.require_time_interval_hours()
            nominal_power_w_map = dict(config.inverter_nominal_power_w_map)
            degradation = config.degradation_factor
            pac_date = config.require_pac_date()
        else:
            raw = config
            time_interval = raw["pr_calculation_parameters"]["time_interval_hours"]
            nominal_power_w_map = get_inverter_nominal_power_w_map(raw)
            degradation = raw["pr_calculation_parameters"].get("degradation_factor", 0.0)
            pac_date = raw["metadata"]["PAC_date"]

        if year is None or month is None or day is None:
            raise DataValidationError("year, month and day are required for KPI calculation")

        actual_date = pd.to_datetime(f"{year}-{month:02d}-{day:02d}")
        years_from_pac = calculate_year_since_pac(actual_date, pac_date)
        group_cols, expected_intervals = get_grouping_columns_and_intervals(df, freq=freq, time_interval_hours=time_interval)

        total_energy_agg_map = {
            CAN.INVERTER_ENERGY_SUM: (CAN.INVERTER_ENERGY_SUM, "sum"),
            CAN.NET_ENERGY: (CAN.NET_ENERGY, "sum"),
            **{c: (c, "sum") for c in df.columns if c.startswith(f"{CAN.INVERTER_ENERGY}_") and c != CAN.INVERTER_ENERGY_SUM},
        }
        df_total_energy = df.groupby(group_cols, as_index=False).agg(**total_energy_agg_map)

        df_filtered = df[df[CAN.CHECK_IRRADIANCE] == True].copy().reset_index(drop=True)
        if df_filtered.empty:
            df_output = df_total_energy[group_cols].copy()
            df_output[CAN.IRRADIATION] = 0.0
            df_output[CAN.IRRADIANCE] = pd.NA
            df_output[CAN.THEORETICAL_ENERGY] = 0.0
            df_output[CAN.PR] = pd.NA
            df_output[CAN.VALID_INTERVAL_PCT] = 0.0
        else:
            df_output = df_filtered.groupby(group_cols, as_index=False).agg(**{
                CAN.IRRADIATION: (CAN.IRRADIATION, "sum"),
                CAN.IRRADIANCE: (f"{CAN.IRRADIANCE}_mean", "mean"),
                CAN.NET_ENERGY: (CAN.NET_ENERGY, "sum"),
                CAN.CHECK_IRRADIANCE: (CAN.CHECK_IRRADIANCE, "sum"),
                CAN.THEORETICAL_ENERGY: (CAN.THEORETICAL_ENERGY, "sum"),
            })
            df_output[CAN.PR] = calculate_pr(df_output[CAN.NET_ENERGY], df_output[CAN.THEORETICAL_ENERGY], degradation, years_from_pac)
            df_output[CAN.VALID_INTERVAL_PCT] = calculate_valid_interval_pct(df_output[CAN.CHECK_IRRADIANCE], expected_intervals)
            df_output.drop(columns=[CAN.CHECK_IRRADIANCE, CAN.NET_ENERGY], inplace=True)

        availability = compute_availability_kpis(df_filtered, time_interval, group_cols, cs_intervals, nominal_power_w_map) if not df_filtered.empty else pd.DataFrame(columns=group_cols)
        final = df_total_energy.merge(df_output.merge(availability, on=group_cols, how="left"), on=group_cols, how="left")
        final[CAN.YOM] = int(years_from_pac)
        final[CAN.YEAR] = year
        final[CAN.MONTH] = month
        final[CAN.DAY] = day
        final["freq"] = freq
        fixed = [CAN.YOM, CAN.YEAR, CAN.MONTH, CAN.DAY]
        return final[[c for c in fixed if c in final.columns] + [c for c in final.columns if c not in fixed]]


# ---------------------------------------------------------------------------
# Contractual Stop intervals
# ---------------------------------------------------------------------------

def build_cs_intervals(
    df_tickets: pd.DataFrame,
    plant_name: str,
    period_start: pd.Timestamp | None = None,
    period_end: pd.Timestamp | None = None,
    cs_cause_prefix: str = "FM_Ente Fornitore",
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Build contractual-stop intervals from ticket data.

    This function replaces the old ``shared.cs_intervals`` module and is kept
    inside ``solar.domain.kpi`` to reduce small modules while keeping the KPI
    batch behavior unchanged.
    """
    required = {
        "impianto",
        "data_inizio_disservizio",
        "data_fine_disservizio",
        "causa_problematica_finale",
    }
    missing = required - set(df_tickets.columns)
    if missing:
        raise MissingColumnsError(f"Missing columns in df_tickets: {missing}")

    mask_plant = df_tickets["impianto"].astype(str).str.lower().str.contains(
        plant_name.lower(), na=False
    )
    mask_dates = (
        df_tickets["data_inizio_disservizio"].notna()
        & df_tickets["data_fine_disservizio"].notna()
        & df_tickets["causa_problematica_finale"].astype(str).str.contains(
            cs_cause_prefix, case=False, na=False
        )
    )
    df_cs = df_tickets.loc[
        mask_plant & mask_dates,
        ["data_inizio_disservizio", "data_fine_disservizio"],
    ].copy()
    if df_cs.empty:
        return []

    df_cs["data_inizio_disservizio"] = pd.to_datetime(df_cs["data_inizio_disservizio"], errors="coerce")
    df_cs["data_fine_disservizio"] = pd.to_datetime(df_cs["data_fine_disservizio"], errors="coerce")
    df_cs = df_cs.dropna(subset=["data_inizio_disservizio", "data_fine_disservizio"])

    if period_start is not None:
        period_start = pd.Timestamp(period_start)
        df_cs = df_cs[df_cs["data_fine_disservizio"] > period_start]
    if period_end is not None:
        period_end = pd.Timestamp(period_end)
        df_cs = df_cs[df_cs["data_inizio_disservizio"] < period_end]

    return list(zip(df_cs["data_inizio_disservizio"], df_cs["data_fine_disservizio"]))
