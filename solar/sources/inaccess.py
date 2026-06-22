from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from data_platform.datalake.lake import BasePathsConfig, DEFAULT_LAKE_ROOT
from data_platform.pipelines.adapters import BaseSourceAdapter, BronzePayload, BronzeRequest, DataSource
from data_platform.utils.diagnostics import ConfigurationError, DataValidationError, SourceResponseError, get_logger
from solar.config.model import get_inverter_nominal_power_w_map
from solar.domain.schemas import SilverRawColumns as RAW
from solar.domain.transforms import make_device_time_grid

logger = get_logger(__name__)


class InaccessClient(DataSource):
    """Bronze-oriented Inaccess API client."""

    def __init__(self, connection_config: dict[str, Any]) -> None:
        super().__init__(connection_config)
        api_key = connection_config.get("api_key")
        if not api_key:
            raise ConfigurationError("api_key must be provided in connection_config")
        self.base_url = connection_config.get("base_url", "https://portal.solarpark-online.com/ifms")
        self.timeout = int(connection_config.get("timeout", 40))
        self.verify = bool(connection_config.get("verify", True))
        self.headers = {"Api-Key": api_key}

    def get_source_name(self) -> str:
        return "inaccess"

    def fetch_data(self, start_timestamp: datetime, end_timestamp: datetime, parameters: dict[str, Any] | None = None) -> Any:
        if start_timestamp > end_timestamp:
            raise DataValidationError("start_timestamp must be <= end_timestamp")
        parameters = parameters or {}
        source_id = parameters.get("source_id")
        if not source_id:
            raise DataValidationError("parameters['source_id'] is required for InaccessClient.fetch_data")
        params = {
            "start_date": start_timestamp.strftime("%Y%m%dT%H%M%SZ"),
            "end_date": end_timestamp.strftime("%Y%m%dT%H%M%SZ"),
        }
        endpoint = parameters.get("endpoint", "/sources/{source_id}/events").format(source_id=source_id)
        payload = self.api_get_request(endpoint, params=params).json()
        if not isinstance(payload, (dict, list)):
            raise SourceResponseError(f"Unexpected JSON type from API: {type(payload)}")
        return payload


@dataclass(frozen=True)
class InaccessPathsConfig(BasePathsConfig):
    source_name: str = "inaccess"
    bronze_filename_template: str = "{device_name}_{measurement_name}.json.gz"
    silver_filename_template_string_currents: str = "{device_name}_{measurement_name}.parquet"

    def bronze_data_key(self, *, plant: str, dt: datetime, device_name: str, measurement_name: str) -> str:
        filename = self.bronze_filename_template.format(device_name=device_name, measurement_name=measurement_name)
        return self.bronze_prefix(plant=plant, dt=dt) + filename

    def silver_data_key_string_currents(self, *, plant: str, dt: datetime, device_name: str, measurement_name: str) -> str:
        filename = self.silver_filename_template_string_currents.format(device_name=device_name, measurement_name=measurement_name)
        return self.silver_prefix(plant=plant, dt=dt) + filename


def _process_device_data(
    df: pd.DataFrame,
    *,
    base_timestamps: pd.DatetimeIndex,
    device_name: str,
    value_column: str,
    instrument: str,
    quality_cfg: dict,
) -> pd.DataFrame:
    q_cfg = quality_cfg.get(instrument, {})
    if q_cfg.get("enabled", False):
        q_field = q_cfg.get("field", "quality")
        min_quality = float(q_cfg.get("min_quality", 0.0))
        if q_field in df.columns:
            q_series = pd.to_numeric(df[q_field], errors="coerce")
            df = df[q_series >= min_quality].copy()

    if "date" not in df.columns or "val" not in df.columns:
        logger.warning("Missing date/val for instrument=%s device=%s; returning empty grid", instrument, device_name)
        return make_device_time_grid(base_timestamps, device_name, [value_column])

    df = df.copy()
    df[RAW.DATETIME] = pd.to_datetime(df["date"], format="%Y%m%dT%H%M%SZ", utc=True, errors="coerce")
    values = pd.to_numeric(df["val"], errors="coerce")
    grouped = (
        pd.DataFrame({RAW.DATETIME: df[RAW.DATETIME], "val": values})
        .dropna(subset=[RAW.DATETIME])
        .groupby(RAW.DATETIME, sort=False, as_index=True)["val"]
        .first()
    )
    completed = make_device_time_grid(base_timestamps, device_name)
    completed[value_column] = pd.array(grouped.reindex(base_timestamps), dtype="Float64")
    return completed


class InaccessAdapter(BaseSourceAdapter):
    """Source adapter for Inaccess using the single YAML connection shape."""

    source_name = "inaccess"

    def __init__(self, *, lake_root: str = DEFAULT_LAKE_ROOT) -> None:
        self.paths = InaccessPathsConfig(root=lake_root)

    def iter_bronze_requests(self, config: dict, time_range: tuple):
        for instrument in config.get("connections", {}):
            for device in config["connections"].get(instrument, []):
                for conn in device.get("id_connections", []):
                    yield BronzeRequest(
                        instrument=instrument,
                        fetch_parameters={"source_id": conn["id"]},
                        metadata={
                            "device_name": device["name"],
                            "measurement_name": conn["measurement_name"],
                            "value_column": conn["value_column_name"],
                        },
                    )

    def bronze_key(self, *, config: dict, request: BronzeRequest, end_datetime) -> str:
        return self.paths.bronze_data_key(
            plant=config["metadata"]["plant_name"],
            dt=end_datetime,
            device_name=request.metadata["device_name"],
            measurement_name=request.metadata["measurement_name"],
        )

    def available_silver_instruments(self, config: dict) -> list[str]:
        instruments = list(config.get("connections", {}).keys())
        if not config.get("pr_calculation_parameters", {}).get("has_meter", False):
            instruments = [i for i in instruments if i != "meters"]
        return instruments

    def iter_silver_requests_for_instrument(self, config: dict, instrument: str, time_range: tuple):
        for request in self.iter_bronze_requests(config, time_range):
            if request.instrument == instrument:
                yield request

    def transform_bronze_payloads_to_silver(self, *, config: dict, instrument: str, bronze_payloads: list[BronzePayload], time_range: tuple) -> pd.DataFrame:
        start_datetime, end_datetime, *_ = time_range
        freq = pd.to_timedelta(config["pr_calculation_parameters"]["time_interval_hours"], unit="h")
        timestamps = pd.date_range(start=start_datetime, end=end_datetime, freq=freq, inclusive="left")
        base_timestamps = pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True), name=RAW.DATETIME)
        quality_cfg = config.get("data_quality", {})

        dfs: list[pd.DataFrame] = []
        for item in bronze_payloads:
            device_name = item.request.metadata["device_name"]
            value_column = item.request.metadata["value_column"]
            payload = item.payload
            if not payload:
                dfs.append(make_device_time_grid(base_timestamps, device_name, [value_column]))
                continue
            dfs.append(_process_device_data(pd.DataFrame(payload), base_timestamps=base_timestamps, device_name=device_name, value_column=value_column, instrument=instrument, quality_cfg=quality_cfg))

        if dfs:
            return pd.concat(dfs, ignore_index=True)
        empty = [make_device_time_grid(base_timestamps, req.metadata["device_name"], [req.metadata["value_column"]]) for req in self.iter_silver_requests_for_instrument(config, instrument, time_range)]
        return pd.concat(empty, ignore_index=True) if empty else pd.DataFrame()

    def get_inverter_nominal_power_w_map(self, config: dict) -> dict[str, float]:
        return get_inverter_nominal_power_w_map(config)
