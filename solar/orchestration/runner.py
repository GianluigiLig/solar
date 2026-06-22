from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from data_platform.datalake.lake import DataLake, DEFAULT_LAKE_ROOT
from data_platform.datalake.storage import GCSObjectStorage, LocalObjectStorage, ObjectStorage
from data_platform.pipelines import BronzePipeline, GoldPipeline, SilverPipeline
from data_platform.utils.diagnostics import ConfigurationError, get_logger
from solar.config import build_solar_context, load_solar_config
from solar.domain.gold import SolarGoldProcessor
from solar.domain.kpi import SolarKpiProcessor
from solar.sources import get_source_adapter, get_source_client

logger = get_logger(__name__)


def build_lake(*, bucket: str, storage: ObjectStorage | None = None, local_storage_root: str | None = None, gcs_client=None) -> DataLake:
    if storage is None:
        if local_storage_root:
            storage = LocalObjectStorage(local_storage_root)
        elif gcs_client is not None:
            storage = GCSObjectStorage(client=gcs_client)
        else:
            raise ConfigurationError("Provide storage, local_storage_root or gcs_client")
    return DataLake(storage=storage, bucket=bucket)


def run_solar_pipeline(
    *,
    config_path: str | Path,
    source: str,
    stages: Iterable[str],
    bucket: str,
    lake_root: str = DEFAULT_LAKE_ROOT,
    connection_config: dict | None = None,
    storage: ObjectStorage | None = None,
    local_storage_root: str | None = None,
    gcs_client=None,
    execution_date: str | date | datetime | None = None,
) -> dict[str, dict]:
    """Run the solar medallion flow using one YAML file.

    Bronze/silver/gold are generic pipelines from ``data_platform``. The solar
    package injects the source adapter and the ``SolarGoldProcessor``.
    """
    normalized_stages = [stage.lower() for stage in stages]
    config = load_solar_config(config_path, source=source)
    context = build_solar_context(config, source=source, execution_date=execution_date)
    adapter = get_source_adapter(source, lake_root=lake_root)
    lake = build_lake(bucket=bucket, storage=storage, local_storage_root=local_storage_root, gcs_client=gcs_client)

    results: dict[str, dict] = {}

    if "bronze" in normalized_stages:
        if connection_config is None:
            raise ConfigurationError("connection_config is required for bronze stage")
        client = get_source_client(source, connection_config)
        results["bronze"] = BronzePipeline(lake).run(context=context, adapter=adapter, client=client)

    if "silver" in normalized_stages:
        results["silver"] = SilverPipeline(lake).run(context=context, adapter=adapter)

    if "gold" in normalized_stages:
        results["gold"] = GoldPipeline(lake).run(context=context, processor=SolarGoldProcessor(adapter))

    return results


def calculate_solar_kpis_from_gold(
    *,
    config_path: str | Path,
    source: str,
    gold_df: pd.DataFrame,
    freq: str,
    year: int,
    month: int,
    day: int,
    cs_intervals: list | None = None,
) -> pd.DataFrame:
    """Standalone KPI helper. KPI remains solar-specific, outside generic medallion pipelines."""
    config = load_solar_config(config_path, source=source)
    return SolarKpiProcessor().calculate(gold_df, config, freq=freq, year=year, month=month, day=day, cs_intervals=cs_intervals)
