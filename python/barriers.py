"""
barriers.py - Barriers to flexibility & digitalisation readiness (v3).

Reads the qualitative barrier register (`17_Barriers_Digitalisation`) and
produces a per-category summary (sheet `48_FNA_Barriers_Summary`).

This is intentionally lightweight: ACER's barriers/digitalisation requirement
is largely a structured register, not a model. The summary aggregates
severity and status by category so it can feed `15_Dashboard`, and computes a
simple 0-100 "digitalisation readiness" score per category (100 = no open
barriers with a digitalisation dependency).

Expected columns on `17_Barriers_Digitalisation`:
    barrier_id, category, description, severity_1to5,
    digitalisation_dependency, status, source_id, data_quality, notes
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _clean_col(c: Any) -> str:
    return str(c).strip().lower().replace(" ", "_")


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
