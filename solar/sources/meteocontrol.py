from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import pandas as pd
from requests.auth import HTTPBasicAuth

from data_platform.datalake.lake import BasePathsConfig, DEFAULT_LAKE_ROOT
from data_platform.pipelines.adapters import BaseSourceAdapter, BronzePayload, BronzeRequest, DataSource
from data_platform.utils.diagnostics import ConfigurationError, DataValidationError, SourceResponseError
from data_platform.utils.time_utils import split_utc_range
from solar.domain.schemas import SilverRawColumns as RAW
from solar.domain.transforms import make_device_time_grid

Instrument = Literal["inverters", "sensors"]


class MeteoControlClient(DataSource):
    def __init__(self, connection_config: dict[str, Any]) -> None:
        super().__init__(connection_config)
        api_key = connection_config.get("api_key")
        username = connection_config.get("username")
        password = connection_config.get("password")
        if not api_key or not username or not password:
            raise ConfigurationError("api_key, username and password must all be provided")
        self.base_url = connection_config.get("base_url", "https://api.meteocontrol.de/v2/systems")
        self.timeout = int(connection_config.get("timeout", 30))
        self.headers = {"X-API-KEY": api_key}
        self.auth = HTTPBasicAuth(username, password)

    def get_source_name(self) -> str:
        return "meteocontrol"

    def fetch_data(self, start_timestamp: datetime, end_timestamp: datetime, parameters: dict[str, Any] | None = None) -> Any:
        if start_timestamp > end_timestamp:
            raise DataValidationError("start_timestamp must be <= end_timestamp")
        parameters = parameters or {}
        connection_id = parameters.get("connection_id")
        instrument = parameters.get("instrument")
        if not connection_id:
            raise DataValidationError("parameters['connection_id'] is required")
        if instrument not in ("inverters", "sensors"):
            raise DataValidationError("parameters['instrument'] must be one of ('inverters', 'sensors')")
        chunks = split_utc_range(start_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"), end_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"), max_hours=24)
        endpoint = f"/{connection_id}/{instrument}/bulk/measurements"
        merged: dict[str, Any] = {}
        for from_iso, to_iso in chunks:
            data = self.api_get_request(endpoint, params={"from": from_iso, "to": to_iso, "resolution": "interval", "precision": "2"}).json()
            if not isinstance(data, dict):
                raise SourceResponseError(f"Unexpected JSON type from API: {type(data)}")
            merged.update(data)
        return merged


@dataclass(frozen=True)
class MeteoControlPathsConfig(BasePathsConfig):
    source_name: str = "meteocontrol"
    bronze_filename_template: str = "{instrument}-{connection_id}.json.gz"

    def bronze_data_key(self, *, plant: str, dt: datetime, instrument: str, connection_id: str) -> str:
        filename = self.bronze_filename_template.format(instrument=instrument, connection_id=connection_id)
        return self.bronze_prefix(plant=plant, dt=dt) + filename


INVERTER_FLOAT_COLS = [
    "COS_PHI", "E_DAY", "E_INT", "E_INT_N", "E_TOTAL", "F_AC", "I_AC", "I_AC1", "I_AC2", "I_AC3",
    "I_DC", "I_DC1", "P_AC", "P_AC_N", "P_DC", "P_DC1", "Q_AC", "QS_CI", "S_AC", "T_WR1", "T_WR2",
    "T_WR3", "T_WR4", "U_AC", "U_AC_L1L2", "U_AC_L2L3", "U_AC_L3L1", "U_AC1", "U_AC2", "U_AC3", "U_DC", "U_DC1",
]
INVERTER_INT_COLS = ["ERROR1", "ERROR2", "ERROR3", "ERROR4", "ERROR5", "ERROR6", "ERROR7", "QS_RX", "QS_TX", "STATE1", "STATE2"]
INVERTER_VALUE_COLS = INVERTER_FLOAT_COLS + INVERTER_INT_COLS
PYRANOMETER_VALUE_COLS = [RAW.IRRADIANCE]


def _flatten_mc_response(response: dict) -> list[dict]:
    rows: list[dict] = []
    if not isinstance(response, dict):
        return rows
    for dt_str, systems in response.items():
        if not isinstance(systems, dict):
            continue
        for system_id, values in systems.items():
            row = {RAW.DATETIME: dt_str, RAW.DEVICE_ID: str(system_id)}
            if isinstance(values, dict):
                row.update(values)
            rows.append(row)
    return rows


class MeteoControlAdapter(BaseSourceAdapter):
    source_name = "meteocontrol"

    def __init__(self, *, lake_root: str = DEFAULT_LAKE_ROOT) -> None:
        self.paths = MeteoControlPathsConfig(root=lake_root)

    def iter_bronze_requests(self, config: dict, time_range: tuple):
        for connection_id in config.get("connections", {}):
            for instrument in config["connections"].get(connection_id, {}):
                yield BronzeRequest(instrument=instrument, fetch_parameters={"instrument": instrument, "connection_id": connection_id}, metadata={"connection_id": connection_id})

    def bronze_key(self, *, config: dict, request: BronzeRequest, end_datetime) -> str:
        return self.paths.bronze_data_key(plant=config["metadata"]["plant_name"], dt=end_datetime, instrument=request.instrument, connection_id=request.metadata["connection_id"])

    def available_silver_instruments(self, config: dict) -> list[str]:
        instruments: list[str] = []
        has_inverters = any("inverters" in c for c in config.get("connections", {}).values())
        has_pyr = any(any(s.get("type") == "pyranometer" for s in c.get("sensors", [])) for c in config.get("connections", {}).values())
        if has_inverters:
            instruments.append("inverters")
        if has_pyr:
            instruments.append("pyranometers")
        return instruments

    def iter_silver_requests_for_instrument(self, config: dict, instrument: str, time_range: tuple):
        raw_instrument = "sensors" if instrument == "pyranometers" else instrument
        for request in self.iter_bronze_requests(config, time_range):
            if request.instrument != raw_instrument:
                continue
            if raw_instrument == "sensors":
                sensors = config["connections"][request.metadata["connection_id"]].get("sensors", [])
                if not any(s.get("type") == "pyranometer" for s in sensors):
                    continue
            yield request

    def _inverter_mapping(self, config: dict) -> dict[str, str]:
        return {str(inv["id"]): inv["name"] for conn in config.get("connections", {}) for inv in config["connections"][conn].get("inverters", [])}

    def _pyranometer_mapping(self, config: dict) -> dict[str, str]:
        return {str(sensor["id"]): sensor["name"] for conn in config.get("connections", {}) for sensor in config["connections"][conn].get("sensors", []) if sensor.get("type") == "pyranometer"}

    def _all_inverter_names(self, config: dict) -> list[str]:
        return [inv["name"] for conn in config.get("connections", {}) for inv in config["connections"][conn].get("inverters", [])]

    def _all_pyranometer_names(self, config: dict) -> list[str]:
        return [sensor["name"] for conn in config.get("connections", {}) for sensor in config["connections"][conn].get("sensors", []) if sensor.get("type") == "pyranometer"]

    def transform_bronze_payloads_to_silver(self, *, config: dict, instrument: str, bronze_payloads: list[BronzePayload], time_range: tuple) -> pd.DataFrame:
        start_datetime, end_datetime, *_ = time_range
        freq = pd.to_timedelta(config["pr_calculation_parameters"]["time_interval_hours"], unit="h").round("5min")
        timestamps = pd.date_range(start=start_datetime, end=end_datetime, freq=freq, inclusive="left")
        if instrument == "inverters":
            mapping, all_names, value_cols = self._inverter_mapping(config), self._all_inverter_names(config), INVERTER_VALUE_COLS
        elif instrument == "pyranometers":
            mapping, all_names, value_cols = self._pyranometer_mapping(config), self._all_pyranometer_names(config), PYRANOMETER_VALUE_COLS
        else:
            raise DataValidationError(f"Unsupported MeteoControl silver instrument: {instrument}")

        dfs: list[pd.DataFrame] = []
        for item in bronze_payloads:
            records = _flatten_mc_response(item.payload)
            if not records:
                continue
            df = pd.DataFrame(records)
            df[RAW.DATETIME] = pd.to_datetime(df[RAW.DATETIME], errors="coerce", utc=True)
            df[RAW.DEVICE_ID] = df[RAW.DEVICE_ID].astype("string")
            df[RAW.DEVICE_NAME] = df[RAW.DEVICE_ID].map(mapping)
            df = df.dropna(subset=[RAW.DEVICE_NAME]).reset_index(drop=True)
            if instrument == "inverters":
                for col in INVERTER_FLOAT_COLS:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Float64")
                for col in INVERTER_INT_COLS:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
                leading = [RAW.DATETIME, RAW.DEVICE_NAME]
                df = df[leading + [c for c in df.columns if c not in leading]]
            else:
                if RAW.IRRADIANCE not in df.columns:
                    df[RAW.IRRADIANCE] = pd.NA
                df[RAW.IRRADIANCE] = pd.to_numeric(df[RAW.IRRADIANCE], errors="coerce").astype("Float64")
                df = df[[RAW.DATETIME, RAW.DEVICE_NAME, RAW.IRRADIANCE]]
            dfs.append(df.sort_values([RAW.DATETIME, RAW.DEVICE_NAME]))
        if not dfs:
            return make_device_time_grid(timestamps, all_names, value_cols)
        return make_device_time_grid(timestamps, all_names, value_cols=[]).merge(pd.concat(dfs, ignore_index=True), on=[RAW.DATETIME, RAW.DEVICE_NAME], how="left").sort_values([RAW.DATETIME, RAW.DEVICE_NAME]).reset_index(drop=True)
