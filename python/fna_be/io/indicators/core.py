"""
fna_indicators.py - ACER-native FNA system-needs indicators (v3).

Turns the GAMS residual/dispatch CSV outputs into the three ACER system-need
families, with the temporal granularity ACER asks for:

    RES integration  -> curtailment by season / day-type / hour, plus a
                        residual-load duration view.
    Ramping          -> residual-load change per market time unit, summarised
                        statistically (mean, P95, P99, max) overall and by season.
    Short-term       -> residual-load forecast-error percentiles, reported as
                        the upward (P99.9) and downward (P0.1) need bands.

All weighting uses the representative-day weights, so seasonal/annual figures
reflect the full year rather than the 240 modelled hours. Nothing here is
ACER-official; it mirrors the methodology's structure for a prototype.
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def build_fna_indicators(
    residual: pd.DataFrame,
    rep_hours: pd.DataFrame,
    rep_days: pd.DataFrame,
    up_percentile: float = 99.9,
    dn_percentile: float = 0.1,
    market_time_unit_minutes: float = 60.0,
    flex_availability: pd.DataFrame | None = None,
    flex: pd.DataFrame | None = None,
    storage: pd.DataFrame | None = None,
    prequalification: pd.DataFrame | None = None,
    target_year: int | None = None,
) -> dict[str, pd.DataFrame]:
    """Return a dict of ACER-shaped indicator tables keyed by sheet-friendly name."""

    df = _merge_calendar(residual, rep_hours, rep_days)
    out = {
        "res_integration": _res_integration(df),
        "ramping": _ramping(df),
        "ramping_capacity": _ramping_capacity_requirement(df, market_time_unit_minutes),
        "short_term": _short_term(df, up_percentile, dn_percentile),
        "short_term_by_season": _short_term_by_season(df, up_percentile, dn_percentile),
        "residual_duration": _residual_duration(df),
    }
    if flex_availability is not None and not flex_availability.empty:
        out["unavailability"] = _unavailability_needs(
            flex_availability, flex, storage, prequalification, rep_hours, target_year,
        )
    return out


def _merge_calendar(residual: pd.DataFrame, rep_hours: pd.DataFrame, rep_days: pd.DataFrame) -> pd.DataFrame:
    """Attach season, day-type, hour and weight to each residual-load row."""

    res = residual.copy()
    res.columns = [str(c).strip().lower() for c in res.columns]
    res = res.rename(columns={"period": "time_id"})
    for col in ["residual_load_mw", "curtailment_mw", "ramp_up_mw", "ramp_down_mw", "demand_mw", "res_available_mw"]:
        if col in res.columns:
            res[col] = pd.to_numeric(res[col], errors="coerce")

    hours = rep_hours.copy()
    hours.columns = [str(c).strip().lower() for c in hours.columns]
    keep = [c for c in ["time_id", "rep_day_id", "hour", "season", "day_type", "weight_days"] if c in hours.columns]
    merged = res.merge(hours[keep], on="time_id", how="left")
    merged["weight_days"] = pd.to_numeric(merged.get("weight_days", 1.0), errors="coerce").fillna(1.0)
    merged["hour"] = pd.to_numeric(merged.get("hour", 0), errors="coerce").fillna(0).astype(int)
    return merged


def _row(section: str, scope: str, metric: str, value: float, unit: str, notes: str) -> dict[str, Any]:
    """Build one tidy long-format output row (shared by all fna_* indicator modules)."""
    return {"section": section, "scope": scope, "key": f"{section}.{scope}.{metric}", "metric": metric,
            "value": value, "unit": unit, "notes": notes}


def _weighted_sum(df: pd.DataFrame, value_col: str) -> float:
    """Annual MWh: hourly value * day weight (each rep day stands for N days)."""
    if value_col not in df.columns:
        return 0.0
    return float((df[value_col].fillna(0.0) * df["weight_days"]).sum())


def _res_integration(df: pd.DataFrame) -> pd.DataFrame:
    """Curtailment characterised at seasonal, daily and hourly level (ACER Art. 8)."""
    rows: list[dict[str, Any]] = []
    total = _weighted_sum(df, "curtailment_mw")
    rows.append({"level": "annual", "key": "total", "curtailment_mwh": total})

    if "season" in df.columns:
        for season, grp in df.groupby("season"):
            rows.append({"level": "seasonal", "key": season, "curtailment_mwh": _weighted_sum(grp, "curtailment_mw")})
    if "day_type" in df.columns:
        for day_type, grp in df.groupby("day_type"):
            rows.append({"level": "day_type", "key": day_type, "curtailment_mwh": _weighted_sum(grp, "curtailment_mw")})
    for hour, grp in df.groupby("hour"):
        rows.append({"level": "hourly", "key": f"H{int(hour):02d}", "curtailment_mwh": _weighted_sum(grp, "curtailment_mw")})

    out = pd.DataFrame(rows)
    out["share_of_total_pct"] = np.where(total > 0, out["curtailment_mwh"] / total * 100.0, 0.0)
    return out


def _ramping(df: pd.DataFrame) -> pd.DataFrame:
    """Residual-load ramp statistics per market time unit (ACER Art. 9)."""
    ramp = df.get("residual_load_mw")
    if ramp is None:
        return pd.DataFrame()

    # Per-day chronological diff so ramps don't jump across representative days.
    df = df.sort_values(["rep_day_id", "hour"]) if "rep_day_id" in df.columns else df.sort_values("hour")
    df["residual_ramp_mw"] = df.groupby("rep_day_id")["residual_load_mw"].diff() if "rep_day_id" in df.columns else df["residual_load_mw"].diff()
    valid = df.dropna(subset=["residual_ramp_mw"])

    def _stats(series: pd.Series, label: str) -> dict[str, Any]:
        up = series[series > 0]
        dn = series[series < 0]
        return {
            "scope": label,
            "max_ramp_up_mw_per_h": float(up.max()) if not up.empty else 0.0,
            "p95_ramp_up_mw_per_h": float(np.percentile(up, 95)) if not up.empty else 0.0,
            "max_ramp_down_mw_per_h": float(-dn.min()) if not dn.empty else 0.0,
            "p95_ramp_down_mw_per_h": float(-np.percentile(dn, 5)) if not dn.empty else 0.0,
            "mean_abs_ramp_mw_per_h": float(series.abs().mean()) if not series.empty else 0.0,
        }

    rows = [_stats(valid["residual_ramp_mw"], "annual")]
    if "season" in valid.columns:
        for season, grp in valid.groupby("season"):
            rows.append(_stats(grp["residual_ramp_mw"], season))
    return pd.DataFrame(rows)


def _ramping_capacity_requirement(df: pd.DataFrame, mtu_minutes: float) -> pd.DataFrame:
    """Translate residual-load ramp statistics into a required-flexible-capacity
    figure per market time unit (ACER Art. 9, capacity-requirement framing).

    `_ramping` reports realised MW/h ramps. ACER's "ramping need" is better read
    as: how much flexible capacity must be able to move within one MTU. This
    rescales the MW/h percentiles to MW per MTU (`mtu_minutes` from
    `01_Control.market_time_unit_minutes`, default 60 = hourly).
    """
    if mtu_minutes <= 0:
        mtu_minutes = 60.0
    ramp_table = _ramping(df)
    if ramp_table.empty:
        return pd.DataFrame()

    scale = mtu_minutes / 60.0
    out = ramp_table[["scope"]].copy()
    out["market_time_unit_minutes"] = mtu_minutes
    for col in ["max_ramp_up_mw_per_h", "p95_ramp_up_mw_per_h", "max_ramp_down_mw_per_h", "p95_ramp_down_mw_per_h"]:
        out[col.replace("_per_h", "_per_mtu")] = ramp_table[col] * scale
    return out


def _short_term(df: pd.DataFrame, up_percentile: float, dn_percentile: float) -> pd.DataFrame:
    """Short-term need from residual-load forecast-error percentiles (ACER Art. 10).

    The model itself uses the percentile need as a reserve requirement; here we
    report the realised residual-load change distribution as the empirical
    counterpart, so the prototype can be compared against the parametric input.
    """
    if "rep_day_id" in df.columns:
        df = df.sort_values(["rep_day_id", "hour"])
        err = df.groupby("rep_day_id")["residual_load_mw"].diff().dropna()
    else:
        err = df["residual_load_mw"].diff().dropna()

    if err.empty:
        return pd.DataFrame()

    rows = [
        {"metric": "upward_need_mw", "percentile": up_percentile, "value_mw": float(np.percentile(err, up_percentile))},
        {"metric": "downward_need_mw", "percentile": dn_percentile, "value_mw": float(-np.percentile(err, dn_percentile))},
        {"metric": "median_ramp_mw", "percentile": 50.0, "value_mw": float(np.percentile(err, 50))},
        {"metric": "stdev_ramp_mw", "percentile": math.nan, "value_mw": float(err.std())},
    ]
    return pd.DataFrame(rows)


def _short_term_by_season(df: pd.DataFrame, up_percentile: float, dn_percentile: float) -> pd.DataFrame:
    """Short-term need percentile bands per season (ACER Art. 10 granularity).

    Same empirical residual-load-change distribution as `_short_term`, but
    computed separately for each season so the prototype can show whether the
    short-term need varies across the year (e.g. higher in winter evenings).
    """
    if "season" not in df.columns or "residual_load_mw" not in df.columns:
        return pd.DataFrame()

    if "rep_day_id" in df.columns:
        df = df.sort_values(["rep_day_id", "hour"])
        df = df.copy()
        df["residual_ramp_mw"] = df.groupby("rep_day_id")["residual_load_mw"].diff()
    else:
        df = df.copy()
        df["residual_ramp_mw"] = df["residual_load_mw"].diff()

    rows: list[dict[str, Any]] = []
    for season, grp in df.groupby("season"):
        err = grp["residual_ramp_mw"].dropna()
        if err.empty:
            continue
        rows.append({
            "season": season,
            "upward_need_mw": float(np.percentile(err, up_percentile)),
            "downward_need_mw": float(-np.percentile(err, dn_percentile)),
            "median_ramp_mw": float(np.percentile(err, 50)),
            "stdev_ramp_mw": float(err.std()),
        })
    return pd.DataFrame(rows)


def _residual_duration(df: pd.DataFrame) -> pd.DataFrame:
    """Weighted residual-load duration curve (day-equivalent hours above each level)."""
    if "residual_load_mw" not in df.columns:
        return pd.DataFrame()
    work = df[["residual_load_mw", "weight_days"]].dropna().copy()
    work = work.sort_values("residual_load_mw", ascending=False)
    work["cumulative_hours"] = work["weight_days"].cumsum()
    return work[["residual_load_mw", "cumulative_hours"]].reset_index(drop=True)


def _pick_capacity_col(flex_df: pd.DataFrame, base: str, target_year: int | None) -> str | None:
    """Return the best-matching `{base}_{year}` column, falling back to the
    first `{base}_*` column if the target year isn't present."""
    if target_year is not None:
        candidate = f"{base}_{target_year}"
        if candidate in flex_df.columns:
            return candidate
    matches = [c for c in flex_df.columns if c.startswith(f"{base}_")]
    return matches[0] if matches else (base if base in flex_df.columns else None)


def _unavailability_needs(
    flex_availability: pd.DataFrame,
    flex: pd.DataFrame | None,
    storage: pd.DataFrame | None,
    prequalification: pd.DataFrame | None,
    rep_hours: pd.DataFrame,
    target_year: int | None,
) -> pd.DataFrame:
    """Flexibility-resource unavailability needs (ACER Art. 13).

    For each flexibility resource and hour, the gap between nameplate and
    available capacity (`10_FlexAvailability`) is counted as a "replacement
    flexibility need" only in hours where the resource's dispatch already used
    (close to) all of its available headroom (`storage.csv`), i.e. more
    capacity would have been used had it been available.

    Resources flagged in `10b_Prequalification_Log` as `temporary_limit` or
    `unavailable` add a further static need (50% / 100% of nameplate) on top
    of the time-varying derating gap, independent of dispatch.
    """
    av = flex_availability.copy()
    av.columns = [str(c).strip().lower() for c in av.columns]
    if av.empty or "flex_id" not in av.columns or "time_id" not in av.columns:
        return pd.DataFrame()
    av["flex_id"] = av["flex_id"].astype(str)
    av["time_id"] = av["time_id"].astype(str)
    for c in ["availability_pct_up", "availability_pct_down"]:
        if c in av.columns:
            av[c] = pd.to_numeric(av[c], errors="coerce").fillna(1.0)

    flex_df = flex.copy() if flex is not None else pd.DataFrame()
    if not flex_df.empty:
        flex_df.columns = [str(c).strip().lower() for c in flex_df.columns]
        flex_df["flex_id"] = flex_df["flex_id"].astype(str)
    up_col = _pick_capacity_col(flex_df, "up_capacity_mw", target_year)
    dn_col = _pick_capacity_col(flex_df, "down_capacity_mw", target_year)
    nameplate_up = dict(zip(flex_df["flex_id"], pd.to_numeric(flex_df[up_col], errors="coerce"))) if up_col else {}
    nameplate_dn = dict(zip(flex_df["flex_id"], pd.to_numeric(flex_df[dn_col], errors="coerce"))) if dn_col else {}

    stor = storage.copy() if storage is not None else pd.DataFrame()
    if not stor.empty:
        stor.columns = [str(c).strip().lower() for c in stor.columns]
        stor = stor.rename(columns={"period": "time_id"})
        stor["flex_id"] = stor["flex_id"].astype(str)
        stor["time_id"] = stor["time_id"].astype(str)
        for c in ["flex_up_mw", "flex_down_mw"]:
            if c in stor.columns:
                stor[c] = pd.to_numeric(stor[c], errors="coerce").fillna(0.0)

    prequal = {}
    if prequalification is not None and not prequalification.empty:
        pq = prequalification.copy()
        pq.columns = [str(c).strip().lower() for c in pq.columns]
        if "flex_id" in pq.columns and "prequalification_status" in pq.columns:
            prequal = dict(zip(pq["flex_id"].astype(str), pq["prequalification_status"].astype(str).str.strip().str.lower()))

    hours = rep_hours.copy()
    hours.columns = [str(c).strip().lower() for c in hours.columns]
    hours["time_id"] = hours["time_id"].astype(str)
    keep = [c for c in ["time_id", "season", "weight_days"] if c in hours.columns]
    df = av.merge(hours[keep], on="time_id", how="left")
    df["weight_days"] = pd.to_numeric(df.get("weight_days", 1.0), errors="coerce").fillna(1.0)

    if not stor.empty:
        df = df.merge(stor[["time_id", "flex_id", "flex_up_mw", "flex_down_mw"]], on=["time_id", "flex_id"], how="left")
    df["flex_up_mw"] = df.get("flex_up_mw", 0.0)
    df["flex_down_mw"] = df.get("flex_down_mw", 0.0)
    df["flex_up_mw"] = pd.to_numeric(df["flex_up_mw"], errors="coerce").fillna(0.0)
    df["flex_down_mw"] = pd.to_numeric(df["flex_down_mw"], errors="coerce").fillna(0.0)

    df["nameplate_up_mw"] = df["flex_id"].map(nameplate_up).fillna(0.0)
    df["nameplate_dn_mw"] = df["flex_id"].map(nameplate_dn).fillna(0.0)
    df["available_up_mw"] = df["nameplate_up_mw"] * df.get("availability_pct_up", 1.0)
    df["available_dn_mw"] = df["nameplate_dn_mw"] * df.get("availability_pct_down", 1.0)
    df["derating_up_mw"] = (df["nameplate_up_mw"] - df["available_up_mw"]).clip(lower=0.0)
    df["derating_dn_mw"] = (df["nameplate_dn_mw"] - df["available_dn_mw"]).clip(lower=0.0)

    binding_up = df["flex_up_mw"] >= df["available_up_mw"] * 0.98
    binding_dn = df["flex_down_mw"] >= df["available_dn_mw"] * 0.98
    df["unavail_need_up_mw"] = df["derating_up_mw"].where(binding_up, 0.0)
    df["unavail_need_dn_mw"] = df["derating_dn_mw"].where(binding_dn, 0.0)

    rows: list[dict[str, Any]] = []
    for flex_id, grp in df.groupby("flex_id"):
        status = prequal.get(flex_id, "qualified")
        pq_factor = {"unavailable": 1.0, "temporary_limit": 0.5}.get(status, 0.0)
        pq_up_mw = nameplate_up.get(flex_id, 0.0) * pq_factor
        pq_dn_mw = nameplate_dn.get(flex_id, 0.0) * pq_factor

        rows.append({
            "flex_id": flex_id,
            "prequalification_status": status,
            "derating_need_up_mwh": _weighted_sum(grp, "unavail_need_up_mw"),
            "derating_need_dn_mwh": _weighted_sum(grp, "unavail_need_dn_mw"),
            "derating_need_up_mw_max": float(grp["unavail_need_up_mw"].max()),
            "derating_need_dn_mw_max": float(grp["unavail_need_dn_mw"].max()),
            "prequalification_need_up_mw": pq_up_mw,
            "prequalification_need_dn_mw": pq_dn_mw,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    total = {
        "flex_id": "TOTAL",
        "prequalification_status": "",
        "derating_need_up_mwh": out["derating_need_up_mwh"].sum(),
        "derating_need_dn_mwh": out["derating_need_dn_mwh"].sum(),
        "derating_need_up_mw_max": out["derating_need_up_mw_max"].sum(),
        "derating_need_dn_mw_max": out["derating_need_dn_mw_max"].sum(),
        "prequalification_need_up_mw": out["prequalification_need_up_mw"].sum(),
        "prequalification_need_dn_mw": out["prequalification_need_dn_mw"].sum(),
    }
    return pd.concat([out, pd.DataFrame([total])], ignore_index=True)
