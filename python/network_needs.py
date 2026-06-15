"""
network_needs.py - ACER-native DSO / TSO / Article-14 fine-tuning needs (v3).

Splits the single "Article-14 network layer" scalar into three ACER-shaped
families, using the metadata carried on `13_NetworkNeeds`:

    DSO needs        -> proxy hosting-capacity / feeder-capacity exceedance
                        per local zone (`13b_DSO_Zones`), independent of
                        whether the network layer is switched on in GAMS.
    TSO needs        -> structural (`timeframe == "structural"`) entries of
                        `13_NetworkNeeds` with `zone_id in {"", "TSO"}`,
                        reporting the GAMS-side hosting-cap exceedance /
                        upward shortfall from `network.csv`.
    Fine-tuning needs-> `timeframe == "fine_tuning"` entries of
                        `13_NetworkNeeds`, same source data as TSO needs but
                        kept in a separate sheet because ACER Article 14
                        fine-tuning is a different timeframe than structural
                        TSO/DSO needs.

Nothing here is ACER-official; it mirrors the methodology's structure for a
prototype. All MWh figures use the representative-day weights so seasonal /
annual totals reflect the full year.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_TSO_ZONE_LABELS = {"", "tso", "belgium", "national"}


def _clean_col(c: Any) -> str:
    return str(c).strip().lower().replace(" ", "_")


def _label(x: Any) -> str:
    return str(x).strip()


def _num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _weighted_sum(df: pd.DataFrame, value_col: str, weight_col: str = "weight_days") -> float:
    if value_col not in df.columns or df.empty:
        return 0.0
    return float((df[value_col].fillna(0.0) * df[weight_col]).sum())


def _attach_calendar(df: pd.DataFrame, rep_hours: pd.DataFrame) -> pd.DataFrame:
    """Attach season / day_type / weight to a per-period table via time_id."""
    out = df.copy()
    out.columns = [_clean_col(c) for c in out.columns]
    if "period" in out.columns:
        out = out.rename(columns={"period": "time_id"})
    out["time_id"] = out["time_id"].map(_label)

    hours = rep_hours.copy()
    hours.columns = [_clean_col(c) for c in hours.columns]
    hours["time_id"] = hours["time_id"].map(_label)
    keep = [c for c in ["time_id", "season", "day_type", "weight_days"] if c in hours.columns]

    merged = out.merge(hours[keep], on="time_id", how="left")
    merged["weight_days"] = pd.to_numeric(merged.get("weight_days", 1.0), errors="coerce").fillna(1.0)
    for c in ["down_cap_mw", "down_use_mw", "slack_mw", "up_req_mw", "up_shortfall_mw"]:
        if c in merged.columns:
            merged[c] = pd.to_numeric(merged[c], errors="coerce").fillna(0.0)
    return merged


def _network_metadata(network_df: pd.DataFrame | None) -> pd.DataFrame:
    """Clean 13_NetworkNeeds and fill in v3 defaults for new columns."""
    if network_df is None or network_df.empty:
        return pd.DataFrame(columns=["network_need_id", "direction", "region", "voltage_level",
                                      "timeframe", "zone_id", "contractual_means"])
    df = network_df.copy()
    df.columns = [_clean_col(c) for c in df.columns]
    df["network_need_id"] = df["network_need_id"].map(_label)
    if "timeframe" not in df.columns:
        df["timeframe"] = "structural"
    df["timeframe"] = df["timeframe"].astype(str).str.strip().str.lower().replace({"": "structural", "nan": "structural"})
    if "zone_id" not in df.columns:
        df["zone_id"] = ""
    df["zone_id"] = df["zone_id"].astype(str).str.strip().replace({"nan": ""})
    for c in ["direction", "region", "voltage_level", "contractual_means"]:
        if c not in df.columns:
            df[c] = ""
    return df


def _needs_table(level_rows: list[dict[str, Any]]) -> pd.DataFrame:
    out = pd.DataFrame(level_rows)
    return out


def compute_dso_needs(
    residual: pd.DataFrame,
    rep_hours: pd.DataFrame,
    dso_zones: pd.DataFrame | None,
) -> pd.DataFrame:
    """Proxy DSO flexibility needs from local hosting / feeder capacity limits.

    For each zone in `13b_DSO_Zones`, the zone's share of national RES
    availability / demand is compared against its hosting / feeder capacity.
    Any exceedance is reported as a downward (RES hosting) or upward (feeder
    congestion) DSO need, in MW (max over the year) and MWh (weighted annual,
    seasonal, day-type).
    """
    if dso_zones is None or dso_zones.empty:
        return pd.DataFrame()

    res = residual.copy()
    res.columns = [_clean_col(c) for c in res.columns]
    res = res.rename(columns={"period": "time_id"})
    for c in ["res_available_mw", "demand_mw"]:
        if c in res.columns:
            res[c] = pd.to_numeric(res[c], errors="coerce").fillna(0.0)

    hours = rep_hours.copy()
    hours.columns = [_clean_col(c) for c in hours.columns]
    keep = [c for c in ["time_id", "season", "day_type", "weight_days"] if c in hours.columns]
    df = res.merge(hours[keep], on="time_id", how="left")
    df["weight_days"] = pd.to_numeric(df.get("weight_days", 1.0), errors="coerce").fillna(1.0)

    zones = dso_zones.copy()
    zones.columns = [_clean_col(c) for c in zones.columns]

    rows: list[dict[str, Any]] = []
    for _, zone in zones.iterrows():
        zone_id = _label(zone.get("zone_id"))
        res_share = _num(zone.get("share_of_national_res_pct")) / 100.0
        dem_share = _num(zone.get("share_of_national_demand_pct")) / 100.0
        hosting_cap = _num(zone.get("hosting_capacity_mw"))
        feeder_cap = _num(zone.get("peak_feeder_capacity_mw"))

        zone_df = df.copy()
        zone_df["zone_res_mw"] = zone_df.get("res_available_mw", 0.0) * res_share
        zone_df["zone_demand_mw"] = zone_df.get("demand_mw", 0.0) * dem_share
        zone_df["downward_need_mw"] = (zone_df["zone_res_mw"] - hosting_cap).clip(lower=0.0)
        zone_df["upward_need_mw"] = (zone_df["zone_demand_mw"] - feeder_cap).clip(lower=0.0)

        for direction, need_col in (("downward", "downward_need_mw"), ("upward", "upward_need_mw")):
            total = _weighted_sum(zone_df, need_col)
            peak = float(zone_df[need_col].max()) if not zone_df.empty else 0.0
            rows.append({
                "zone_id": zone_id, "region": zone.get("region", ""), "voltage_level": zone.get("voltage_level", ""),
                "direction": direction, "level": "annual", "key": "total",
                "need_mw_max": peak, "need_mwh": total,
            })
            if "season" in zone_df.columns:
                for season, grp in zone_df.groupby("season"):
                    rows.append({
                        "zone_id": zone_id, "region": zone.get("region", ""), "voltage_level": zone.get("voltage_level", ""),
                        "direction": direction, "level": "seasonal", "key": season,
                        "need_mw_max": float(grp[need_col].max()) if not grp.empty else 0.0,
                        "need_mwh": _weighted_sum(grp, need_col),
                    })

    return _needs_table(rows)


def _aggregate_network_csv(
    network_csv: pd.DataFrame,
    network_meta: pd.DataFrame,
    rep_hours: pd.DataFrame,
    timeframe: str,
    zone_filter: str,
) -> pd.DataFrame:
    """Common aggregation for TSO (structural) and Article-14 (fine-tuning) needs."""
    if network_csv is None or network_csv.empty or network_meta.empty:
        return pd.DataFrame()

    meta = network_meta[network_meta["timeframe"] == timeframe]
    if zone_filter == "tso":
        meta = meta[meta["zone_id"].str.strip().str.lower().isin(_TSO_ZONE_LABELS)]
    else:
        meta = meta[~meta["zone_id"].str.strip().str.lower().isin(_TSO_ZONE_LABELS)]
    if meta.empty:
        return pd.DataFrame()

    df = _attach_calendar(network_csv, rep_hours)
    df["network_need_id"] = df["network_need_id"].map(_label)
    df = df.merge(meta, on="network_need_id", how="inner")
    if df.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for need_id, grp in df.groupby("network_need_id"):
        direction = grp["direction"].iloc[0]
        region = grp["region"].iloc[0]
        voltage = grp["voltage_level"].iloc[0]
        need_col = "slack_mw" if direction == "downward" else "up_shortfall_mw"

        def _emit(level: str, key: str, sub: pd.DataFrame) -> None:
            rows.append({
                "network_need_id": need_id, "direction": direction, "region": region,
                "voltage_level": voltage, "level": level, "key": key,
                "need_mw_max": float(sub[need_col].max()) if not sub.empty else 0.0,
                "need_mwh": _weighted_sum(sub, need_col),
            })

        _emit("annual", "total", grp)
        if "season" in grp.columns:
            for season, sub in grp.groupby("season"):
                _emit("seasonal", season, sub)

    return _needs_table(rows)


def compute_tso_needs(network_csv: pd.DataFrame, network_df: pd.DataFrame | None, rep_hours: pd.DataFrame) -> pd.DataFrame:
    """Structural TSO network needs (Art. 12): national-level entries of 13_NetworkNeeds
    with `timeframe == "structural"`."""
    meta = _network_metadata(network_df)
    return _aggregate_network_csv(network_csv, meta, rep_hours, timeframe="structural", zone_filter="tso")


def compute_fine_tuning_needs(network_csv: pd.DataFrame, network_df: pd.DataFrame | None, rep_hours: pd.DataFrame) -> pd.DataFrame:
    """Article-14 fine-tuning needs: entries of 13_NetworkNeeds with
    `timeframe == "fine_tuning"`, any zone."""
    meta = _network_metadata(network_df)
    structural = _aggregate_network_csv(network_csv, meta, rep_hours, timeframe="fine_tuning", zone_filter="tso")
    local = _aggregate_network_csv(network_csv, meta, rep_hours, timeframe="fine_tuning", zone_filter="dso")
    if structural.empty:
        return local
    if local.empty:
        return structural
    return pd.concat([structural, local], ignore_index=True)
