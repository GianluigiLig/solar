from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from data_platform.pipelines.adapters import PipelineContext
from data_platform.utils.diagnostics import ConfigurationError
from data_platform.utils.time_utils import time_range as build_time_range


def get_required(raw: Mapping[str, Any], path: str) -> Any:
    cursor: Any = raw
    for part in path.split("."):
        if not isinstance(cursor, Mapping) or part not in cursor:
            raise ConfigurationError(f"Missing required config value: {path}")
        cursor = cursor[part]
    return cursor


def get_optional(raw: Mapping[str, Any], path: str, default: Any = None) -> Any:
    cursor: Any = raw
    for part in path.split("."):
        if not isinstance(cursor, Mapping) or part not in cursor:
            return default
        cursor = cursor[part]
    return cursor


def get_inverter_nominal_power_w_map(config: Mapping[str, Any]) -> dict[str, float]:
    registry = config.get("devices_registry", {}).get("inverters", {})
    if registry:
        return {name: float(meta["nominal_power_w"]) for name, meta in registry.items() if "nominal_power_w" in meta}

    out: dict[str, float] = {}
    connections = config.get("connections", {})
    # Inaccess shape: connections.inverters: list[{name, id_connections}]
    for inverter in connections.get("inverters", []) if isinstance(connections, Mapping) else []:
        if "nominal_power_w" in inverter:
            out[inverter["name"]] = float(inverter["nominal_power_w"])
    # MeteoControl historical shape: connections.{conn}.inverters
    for connection_data in connections.values() if isinstance(connections, Mapping) else []:
        if isinstance(connection_data, Mapping):
            for inverter in connection_data.get("inverters", []):
                if "nominal_power_w" in inverter:
                    out[inverter["name"]] = float(inverter["nominal_power_w"])
    return out


@dataclass(frozen=True)
class SolarConfig:
    """Typed view over the single YAML file.

    The YAML remains a single application file; this model only creates logical
    views for safer code access.
    """

    source: str
    raw_config: Mapping[str, Any]
    plant_name: str
    timezone: str = "Europe/Rome"
    plant_nominal_power_kw: float | None = None
    pac_date: date | datetime | str | None = None
    om_date: date | datetime | str | None = None
    time_interval_hours: float | None = None
    irradiance_threshold_w_m2: float | None = None
    has_meter: bool = False
    degradation_factor: float = 0.0
    devices_registry: Mapping[str, Any] = field(default_factory=dict)
    data_quality: Mapping[str, Any] = field(default_factory=dict)
    connections: Mapping[str, Any] = field(default_factory=dict)
    inverter_nominal_power_w_map: dict[str, float] = field(default_factory=dict)

    @property
    def raw(self) -> Mapping[str, Any]:
        return self.raw_config

    def require_time_interval_hours(self) -> float:
        if self.time_interval_hours is None:
            raise ConfigurationError("Missing required config value: pr_calculation_parameters.time_interval_hours")
        return float(self.time_interval_hours)

    def require_irradiance_threshold_w_m2(self) -> float:
        if self.irradiance_threshold_w_m2 is None:
            raise ConfigurationError("Missing required config value: pr_calculation_parameters.irradiance_threshold_w_m2")
        return float(self.irradiance_threshold_w_m2)

    def require_plant_nominal_power_kw(self) -> float:
        if self.plant_nominal_power_kw is None:
            raise ConfigurationError("Missing required config value: metadata.plant_nominal_power_kw")
        return float(self.plant_nominal_power_kw)

    def require_pac_date(self):
        if self.pac_date is None:
            raise ConfigurationError("Missing required config value: metadata.PAC_date")
        return self.pac_date

    def require_om_date(self):
        if self.om_date is None:
            raise ConfigurationError("Missing required config value: metadata.OM_date")
        return self.om_date

    @classmethod
    def from_dict(cls, raw_config: Mapping[str, Any], *, source: str) -> "SolarConfig":
        validate_solar_config(raw_config)
        return cls(
            source=source,
            raw_config=raw_config,
            plant_name=str(get_required(raw_config, "metadata.plant_name")),
            timezone=str(get_optional(raw_config, "metadata.timezone", "Europe/Rome")),
            plant_nominal_power_kw=get_optional(raw_config, "metadata.plant_nominal_power_kw"),
            pac_date=get_optional(raw_config, "metadata.PAC_date"),
            om_date=get_optional(raw_config, "metadata.OM_date"),
            time_interval_hours=get_optional(raw_config, "pr_calculation_parameters.time_interval_hours"),
            irradiance_threshold_w_m2=get_optional(raw_config, "pr_calculation_parameters.irradiance_threshold_w_m2"),
            has_meter=bool(get_optional(raw_config, "pr_calculation_parameters.has_meter", False)),
            degradation_factor=float(get_optional(raw_config, "pr_calculation_parameters.degradation_factor", 0.0)),
            devices_registry=raw_config.get("devices_registry", {}),
            data_quality=raw_config.get("data_quality", {}),
            connections=raw_config.get("connections", {}),
            inverter_nominal_power_w_map=get_inverter_nominal_power_w_map(raw_config),
        )

    @classmethod
    def from_yaml(cls, config_path: str | Path, *, source: str) -> "SolarConfig":
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return cls.from_dict(raw, source=source)


def validate_solar_config(raw_config: Mapping[str, Any]) -> None:
    """Minimal validation aligned with the current single-YAML Inaccess shape."""
    for path in [
        "metadata.plant_name",
        "metadata.plant_nominal_power_kw",
        "metadata.PAC_date",
        "metadata.OM_date",
        "pr_calculation_parameters.time_interval_hours",
        "pr_calculation_parameters.irradiance_threshold_w_m2",
        "connections",
    ]:
        get_required(raw_config, path)


def load_solar_config(config_path: str | Path, *, source: str) -> SolarConfig:
    return SolarConfig.from_yaml(config_path, source=source)


def build_solar_context(
    config: SolarConfig,
    *,
    source: str,
    execution_date: str | date | datetime | None = None,
) -> PipelineContext:
    return PipelineContext(
        asset=config.plant_name,
        source=source,
        time_range=build_time_range(execution_date, tz_local=config.timezone),
        config=config,
        raw_config=dict(config.raw_config),
    )
