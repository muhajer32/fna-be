"""
data_quality_report.py - ACER data, granularity and quality manifest (v3).

Produces a single machine-readable table (sheet `19_DataQuality_Report`)
listing, for every input sheet:

    - temporal resolution (hourly / representative-day / static)
    - spatial resolution (national / zonal, based on region/zone columns)
    - the distribution of `data_quality` values (assumption / calibrated /
      empirical / placeholder / ...)

This is a transparency aid for ACER Article 4 (data, granularity and quality
requirements): it makes explicit, for the whole workbook, which inputs are
still assumptions vs. calibrated vs. empirical, without requiring anyone to
open every sheet.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def _clean_col(c: Any) -> str:
    return str(c).strip().lower().replace(" ", "_")


def _temporal_resolution(df: pd.DataFrame) -> str:
    cols = set(df.columns)
    if "time_id" in cols:
        return "hourly (representative day)"
    if {"target_year"}.intersection(cols) or "parameter" in cols:
        return "annual / static"
    return "static"


def _spatial_resolution(df: pd.DataFrame) -> str:
    cols = set(df.columns)
    if {"zone_id", "voltage_level"} & cols:
        return "zonal (DSO/TSO)"
    if "region" in cols or "region_proxy" in cols:
        return "national with region tags"
    return "national (single node)"


def _quality_distribution(df: pd.DataFrame) -> dict[str, float]:
    if "data_quality" not in df.columns or df.empty:
        return {}
    counts = df["data_quality"].astype(str).str.strip().str.lower().value_counts(normalize=True) * 100.0
    return {f"pct_{k}": round(float(v), 1) for k, v in counts.items()}


def build_granularity_report(frames: dict[str, pd.DataFrame], sheet_names: dict[str, str]) -> pd.DataFrame:
    """Return one row per input sheet with granularity and data-quality stats.

    `frames` is the `inputs["frames"]` dict from `read_inputs`; `sheet_names`
    maps the same keys to the workbook sheet names (`SHEETS` / `OPTIONAL_SHEETS`
    in `io_excel.py`).
    """
    rows: list[dict[str, Any]] = []
    quality_keys: set[str] = set()
    rows_data: list[dict[str, Any]] = []

    for key, sheet_name in sheet_names.items():
        df = frames.get(key)
        if df is None or df.empty:
            rows_data.append({
                "sheet": sheet_name,
                "n_rows": 0,
                "temporal_resolution": "n/a (sheet empty or absent)",
                "spatial_resolution": "n/a",
            })
            continue

        df = df.copy()
        df.columns = [_clean_col(c) for c in df.columns]
        quality = _quality_distribution(df)
        quality_keys |= set(quality.keys())
        row = {
            "sheet": sheet_name,
            "n_rows": int(len(df)),
            "temporal_resolution": _temporal_resolution(df),
            "spatial_resolution": _spatial_resolution(df),
        }
        row.update(quality)
        rows_data.append(row)

    out = pd.DataFrame(rows_data)
    for k in sorted(quality_keys):
        if k not in out.columns:
            out[k] = 0.0
        out[k] = out[k].fillna(0.0)
    return out
