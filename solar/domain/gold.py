from __future__ import annotations

from typing import Any

import pandas as pd

from data_platform.pipelines.adapters import DatasetPayload, PipelineContext
from data_platform.utils.quality import build_pre_gold_nan_quality_report
from data_platform.utils.diagnostics import DataValidationError, MissingColumnsError, get_logger
from solar.config.model import SolarConfig
from solar.domain.kpi import calculate_irr_mean
from solar.domain.schemas import CanonicalColumns as COL
from solar.domain.schemas import SilverRawColumns as RAW
from solar.domain.time import calculate_yom
from solar.domain.transforms import pivot_device_measurements

logger = get_logger(__name__)


def normalize_datetime_index(df: pd.DataFrame, datetime_col: str) -> pd.DataFrame:
    if datetime_col not in df.columns:
        raise MissingColumnsError(f"Missing datetime column: {datetime_col}")
    work = df.copy()
    work[datetime_col] = pd.to_datetime(work[datetime_col], utc=True, errors="coerce")
    work = work.dropna(subset=[datetime_col])
    work = work.drop_duplicates(subset=[datetime_col], keep="last")
    return work.sort_values(by=[datetime_col]).reset_index(drop=True)


def build_solar_gold_dataset(df: pd.DataFrame, config: SolarConfig | dict) -> pd.DataFrame:
    """Domain-specific enrichment from merged silver to solar gold."""
    if isinstance(config, SolarConfig):
        nominal_power_kw = config.require_plant_nominal_power_kw()
        irradiance_threshold = config.require_irradiance_threshold_w_m2()
        time_interval = config.require_time_interval_hours()
        has_meter = config.has_meter
        om_date = config.require_om_date()
        pyr_quality = config.data_quality.get("pyranometers", {})
    else:
        nominal_power_kw = config["metadata"]["plant_nominal_power_kw"]
        irradiance_threshold = config["pr_calculation_parameters"]["irradiance_threshold_w_m2"]
        time_interval = config["pr_calculation_parameters"]["time_interval_hours"]
        has_meter = config["pr_calculation_parameters"].get("has_meter", False)
        om_date = config["metadata"].get("OM_date")
        pyr_quality = config.get("data_quality", {}).get("pyranometers", {})

    if RAW.DATETIME not in df.columns:
        raise MissingColumnsError(f"Missing required column: {RAW.DATETIME}")

    work = normalize_datetime_index(df, RAW.DATETIME)
    dt_utc = pd.to_datetime(work[RAW.DATETIME], utc=True, errors="coerce")
    valid = dt_utc.notna()
    if not valid.all():
        logger.warning("Dropping %d invalid datetime rows", int((~valid).sum()))
        work = work.loc[valid].copy()
        dt_utc = dt_utc.loc[valid]

    work[COL.DATETIME_UTC] = dt_utc
    work[COL.DATETIME_LOCAL] = dt_utc.dt.tz_convert("Europe/Rome")
    work[COL.YOM] = calculate_yom(work[COL.DATETIME_LOCAL], OM_date=om_date)
    work[COL.YEAR] = work[COL.DATETIME_LOCAL].dt.year
    work[COL.MONTH] = work[COL.DATETIME_LOCAL].dt.month
    work[COL.DAY] = work[COL.DATETIME_LOCAL].dt.day

    rename_irradiance_map = {c: c.replace(RAW.IRRADIANCE, COL.IRRADIANCE) for c in work.columns if c.startswith(f"{RAW.IRRADIANCE}_")}
    work.rename(columns=rename_irradiance_map, inplace=True)
    work = calculate_irr_mean(
        df=work,
        prefix=COL.IRRADIANCE,
        outlier_detection=bool(pyr_quality.get("outlier_detection", False)),
        outlier_threshold=float(pyr_quality.get("outlier_threshold", 0.05)),
    )
    work[COL.CHECK_IRRADIANCE] = work[f"{COL.IRRADIANCE}_mean"] > irradiance_threshold
    work[COL.IRRADIATION] = work[f"{COL.IRRADIANCE}_mean"] * time_interval

    power_prefix = f"{RAW.INVERTER_ACTIVE_POWER}_"
    power_raw_cols = [c for c in work.columns if c.startswith(power_prefix)]
    if not power_raw_cols:
        raise DataValidationError("No inverter power columns found in input data")
    rename_power_map = {c: c.replace(power_prefix, f"{COL.INVERTER_ACTIVE_POWER}_") for c in power_raw_cols}
    work.rename(columns=rename_power_map, inplace=True)
    total_power_cols = list(rename_power_map.values())
    work[COL.INVERTER_ACTIVE_POWER_SUM] = work[total_power_cols].sum(axis=1)

    total_energy_cols: list[str] = []
    for power_col in total_power_cols:
        energy_col = power_col.replace(f"{COL.INVERTER_ACTIVE_POWER}_", f"{COL.INVERTER_ENERGY}_")
        work[energy_col] = work[power_col] * time_interval
        total_energy_cols.append(energy_col)
    work[COL.INVERTER_ENERGY_SUM] = work[total_energy_cols].sum(axis=1)

    if has_meter:
        meter_cols = [c for c in work.columns if c.startswith(f"{RAW.METER_ENERGY}_")]
        if not meter_cols:
            raise DataValidationError("has_meter=True in config but no meter energy columns found in data")
        work[COL.NET_ENERGY] = work[meter_cols].sum(axis=1)
    else:
        work[COL.NET_ENERGY] = work[COL.INVERTER_ENERGY_SUM]

    work[COL.THEORETICAL_ENERGY] = nominal_power_kw * (work[COL.IRRADIATION] / 1000)

    irr_cols = [c for c in work.columns if c.startswith(COL.IRRADIANCE)]
    cols = (
        [COL.DATETIME_LOCAL, COL.DATETIME_UTC, COL.YOM, COL.YEAR, COL.MONTH, COL.DAY]
        + irr_cols
        + [COL.CHECK_IRRADIANCE, COL.IRRADIATION]
        + total_power_cols
        + [COL.INVERTER_ACTIVE_POWER_SUM]
        + total_energy_cols
        + [COL.INVERTER_ENERGY_SUM, COL.NET_ENERGY, COL.THEORETICAL_ENERGY]
    )
    return work[[c for c in cols if c in work.columns]]


class SolarGoldProcessor:
    """Processor injected into the generic ``data_platform.GoldPipeline``."""

    def __init__(self, adapter: Any, *, generate_nan_quality_report: bool = True) -> None:
        self.adapter = adapter
        self.generate_nan_quality_report = generate_nan_quality_report

    def get_inputs(self, context: PipelineContext) -> dict[str, str]:
        raw = context.raw_config
        out = {
            "inverters": self.adapter.silver_key(config=raw, instrument="inverters", end_datetime=context.end_datetime),
            "pyranometers": self.adapter.silver_key(config=raw, instrument="pyranometers", end_datetime=context.end_datetime),
        }
        if raw.get("pr_calculation_parameters", {}).get("has_meter", False):
            out["meters"] = self.adapter.silver_key(config=raw, instrument="meters", end_datetime=context.end_datetime)
        return out

    def output_key(self, context: PipelineContext) -> str:
        return self.adapter.enriched_silver_data_key(config=context.raw_config, end_datetime=context.end_datetime)

    def build(self, datasets: dict[str, pd.DataFrame], context: PipelineContext) -> DatasetPayload:
        inverters = datasets["inverters"]
        pyranometers = datasets["pyranometers"]
        meters = datasets.get("meters")

        # MeteoControl exposes P_AC in W, while the canonical gold expects kW.
        if context.source == "meteocontrol" and RAW.INVERTER_ACTIVE_POWER in inverters.columns:
            inverters = inverters.copy()
            inverters[RAW.INVERTER_ACTIVE_POWER] = pd.to_numeric(inverters[RAW.INVERTER_ACTIVE_POWER], errors="coerce") / 1000.0

        df_inverters = pivot_device_measurements(inverters, [RAW.INVERTER_ACTIVE_POWER])
        df_pyranometers = pivot_device_measurements(pyranometers, [RAW.IRRADIANCE])
        dfs = [df_pyranometers, df_inverters]
        if meters is not None:
            dfs.append(pivot_device_measurements(meters, [RAW.METER_ENERGY]))

        df_merged = pd.concat([df.set_index(RAW.DATETIME) for df in dfs], axis=1, join="outer")
        df_merged = df_merged[~df_merged.index.duplicated(keep="last")].sort_index().reset_index()

        # The current project intentionally fills NaN after optionally generating
        # a pre-gold quality report, preserving the previous behavior.
        if self.generate_nan_quality_report:
            report = build_pre_gold_nan_quality_report(
                df_merged,
                plant_name=context.asset,
                source=context.source,
                has_meter=context.raw_config.get("pr_calculation_parameters", {}).get("has_meter", False),
                time_interval_hours=float(context.raw_config["pr_calculation_parameters"]["time_interval_hours"]),
            )
            logger.info("Pre-gold quality summary: rows=%s empty_columns=%s", report.get("rows"), report.get("empty_columns"))

        df_merged = df_merged.fillna(0.0)
        gold = build_solar_gold_dataset(df_merged, context.config)
        return DatasetPayload(name="solar_gold", dataframe=gold, key=self.output_key(context))
