from __future__ import annotations

from data_platform.datalake.lake import DEFAULT_LAKE_ROOT
from data_platform.utils.diagnostics import ConfigurationError

from .inaccess import InaccessAdapter, InaccessClient
from .meteocontrol import MeteoControlAdapter, MeteoControlClient


def get_source_adapter(source: str, *, lake_root: str = DEFAULT_LAKE_ROOT):
    normalized = source.lower().strip()
    if normalized == "inaccess":
        return InaccessAdapter(lake_root=lake_root)
    if normalized == "meteocontrol":
        return MeteoControlAdapter(lake_root=lake_root)
    raise ConfigurationError(f"Unsupported source: {source!r}. Expected 'inaccess' or 'meteocontrol'.")


def get_source_client(source: str, connection_config: dict):
    normalized = source.lower().strip()
    if normalized == "inaccess":
        return InaccessClient(connection_config)
    if normalized == "meteocontrol":
        return MeteoControlClient(connection_config)
    raise ConfigurationError(f"Unsupported source: {source!r}. Expected 'inaccess' or 'meteocontrol'.")


__all__ = ["InaccessAdapter", "InaccessClient", "MeteoControlAdapter", "MeteoControlClient", "get_source_adapter", "get_source_client"]
