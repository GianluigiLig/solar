from __future__ import annotations

"""String-current feature placeholder.

This module intentionally stays separated from the main medallion flow: it is a
solar-specific extension with different inputs/outputs, often including a SQL
sink. The previous project had dedicated pipelines for it; in the new layout the
feature belongs here and can be wired by orchestration only when required.
"""

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class StringCurrentResult:
    dataframe: pd.DataFrame
    metadata: dict[str, Any]


def normalize_string_current_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Small shared normalization hook for future string-current processing."""
    out = df.copy()
    for col in out.columns:
        if col.lower().startswith(("i_", "current")):
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out
