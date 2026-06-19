"""
quality.py - ACER input-quality reports (data granularity + flexibility barriers).

Two related transparency outputs, merged here because both summarise the
*quality/readiness* of the inputs rather than the model results:

1. ``build_granularity_report`` -> sheet ``19_DataQuality_Report``: for every
   input sheet, its temporal resolution (hourly / representative-day / static),
   spatial resolution (national / zonal) and the distribution of `data_quality`
   values. Transparency aid for ACER Article 4 (data, granularity and quality).

2. ``summarise_barriers`` -> sheet ``48_FNA_Barriers_Summary``: per-category
   summary of the qualitative barrier register (`17_Barriers_Digitalisation`),
   with a 0-100 digitalisation-readiness score. Covers ACER's barriers /
   digitalisation requirement.
"""
from __future__ import annotations

from typing import Any

import numpy as np
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


def summarise_barriers(barriers: pd.DataFrame) -> pd.DataFrame:
    """Return one row per barrier category with severity / readiness stats."""
    df = barriers.copy()
    df.columns = [_clean_col(c) for c in df.columns]
    if df.empty or "category" not in df.columns:
        return pd.DataFrame()

    df["severity_1to5"] = pd.to_numeric(df.get("severity_1to5", 0), errors="coerce").fillna(0.0).clip(0, 5)
    df["status"] = df.get("status", "").astype(str).str.strip().str.lower()
    df["is_open"] = ~df["status"].isin(["resolved", "closed", "n/a"])
    df["digitalisation_dependency"] = df.get("digitalisation_dependency", "").astype(str).str.strip()
    df["has_digital_dependency"] = df["digitalisation_dependency"].str.len() > 0
    df["effective_severity"] = np.where(df["is_open"], df["severity_1to5"], 0.0)

    rows: list[dict[str, Any]] = []
    for category, grp in df.groupby("category"):
        n_total = len(grp)
        n_open = int(grp["is_open"].sum())
        n_high = int(((grp["severity_1to5"] >= 4) & grp["is_open"]).sum())
        mean_eff_severity = float(grp["effective_severity"].mean()) if n_total else 0.0
        digital_open = grp[grp["is_open"] & grp["has_digital_dependency"]]
        readiness = 100.0 * (1.0 - mean_eff_severity / 5.0)
        rows.append({
            "category": category,
            "n_barriers": n_total,
            "n_open": n_open,
            "n_high_severity_open": n_high,
            "n_open_with_digital_dependency": int(len(digital_open)),
            "digitalisation_readiness_score": round(readiness, 1),
            "notes": "; ".join(grp.loc[grp["is_open"] & (grp["severity_1to5"] >= 4), "description"].astype(str).head(3)),
        })

    out = pd.DataFrame(rows)
    overall = {
        "category": "TOTAL",
        "n_barriers": int(out["n_barriers"].sum()),
        "n_open": int(out["n_open"].sum()),
        "n_high_severity_open": int(out["n_high_severity_open"].sum()),
        "n_open_with_digital_dependency": int(out["n_open_with_digital_dependency"].sum()),
        "digitalisation_readiness_score": round(float(out["digitalisation_readiness_score"].mean()), 1) if not out.empty else 100.0,
        "notes": "",
    }
    return pd.concat([out, pd.DataFrame([overall])], ignore_index=True)
