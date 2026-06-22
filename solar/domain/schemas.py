from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SilverRawColumns:
    DATETIME: str = "datetime_utc"
    DEVICE_ID: str = "device_id"
    DEVICE_NAME: str = "device_name"
    INVERTER_ACTIVE_POWER: str = "P_AC"  # kW after source normalization
    IRRADIANCE: str = "SRAD"             # W/m²
    METER_ENERGY: str = "E_INT_MEASURED" # kWh per interval


@dataclass(frozen=True)
class CanonicalColumns:
    DATETIME_UTC: str = "datetime_utc"
    DATETIME_LOCAL: str = "datetime_local"
    YOM: str = "yom"
    YEAR: str = "year"
    MONTH: str = "month"
    DAY: str = "day"
    INVERTER_ENERGY: str = "inverter_energy"
    INVERTER_ENERGY_SUM: str = "inverter_energy_sum"
    INVERTER_ACTIVE_POWER: str = "inverter_active_power"
    INVERTER_ACTIVE_POWER_SUM: str = "inverter_active_power_sum"
    THEORETICAL_ENERGY: str = "theoretical_energy"
    NET_ENERGY: str = "net_energy"
    IRRADIATION: str = "irradiation"
    IRRADIANCE: str = "irradiance"
    CHECK_IRRADIANCE: str = "check_irradiance_threshold"
    PR: str = "PR"
    VALID_INTERVAL_PCT: str = "valid_interval_pct"


@dataclass(frozen=True)
class AvailabilityColumns:
    CS_GLOBAL: str = "CS_GLOBAL"
    T_HOURS: str = "T_hours"
    CS_HOURS: str = "CS_hours"
    DENOM_HOURS: str = "DENOM_hours"
    DOWNTIME_HOURS_PREFIX: str = "downtime_hours_"
    AVAILABILITY_PREFIX: str = "availability_"
    PLANT_AVAILABILITY: str = "availability_plant"
    AVAILABILITY_NO_CS_PREFIX: str = "availability_no_cs_"
    PLANT_AVAILABILITY_NO_CS: str = "availability_plant_no_cs"
