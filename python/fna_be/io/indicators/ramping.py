"""
fna_ramping.py - ACER FNA ramping-needs report (v3).

Builds the ramping-needs indicator set requested by ACER's FNA methodology
(Art. 9: "system needs related to ramping") from the GAMS output CSVs and
reports it as a single tidy table written to Excel sheet ``15_FNA_Ramping``.

Reuses, rather than re-derives, the calendar-merge / weighted-sum helpers and
Excel/CSV plumbing already in `fna_indicators.py` and `io_excel.py`:

    _merge_calendar, _weighted_sum, _row      <- fna_indicators.py
    _read_csv_robust, _write_df, _control_dict,
    _num, _label, sort_xlwings_sheets         <- io_excel.py

Outputs (ACER terminology), all rows of one tidy DataFrame with columns
``section, scope, key, metric, value, unit, notes``:

 1. residual_load          - residual load = demand - non-dispatchable (RES
                              available) generation. Already computed by GAMS
                              and reported in residual.csv as residual_load_mw.
 2. ramping_need (up)       - upward ramp between consecutive market time
                              units. GAMS residualRampUp -> residual.csv
                              ramp_up_mw.
 3. ramping_need (down)     - downward ramp between consecutive market time
                              units. GAMS residualRampDn -> residual.csv
                              ramp_down_mw.
 4/5. available_ramp_margin - available upward/downward ramp margin from
                              dispatchable units + storage/DR (reserve.csv
                              up_available_mw / down_available_mw, i.e.
                              resUpGen+resUpFlex / resDnGen+resDnFlex) plus
                              an interconnector headroom term computed here
                              from dispatch.csv import/export flows and the
                              04_Interconnectors / 05_IntercoProfiles capacity
                              and availability inputs (see
                              `_interconnector_headroom`).
 6/7. uncovered_ramping_need- max(0, ramping_need - available_ramp_margin),
                              up and down.
 8.  event_duration         - length (consecutive hours) of uncovered-need
                              events, up and down.
 9.  interval_between_events- gap (hours) between consecutive uncovered-need
                              events, up and down.
 10. ramp_distribution      - percentile distribution (P1-P99, mean, std) of
                              ramp_up_mw, ramp_down_mw, uncovered_up_mw,
                              uncovered_down_mw.
 11. heatmap_hour_season     - mean ramp / uncovered need by hour x season.
 12. mc_ramping_bands        - P5/P50/P95 (+ mean) of annual max ramp and
                              annual uncovered-ramping energy across Monte
                              Carlo scenarios.

GAMS-output sufficiency note
-----------------------------
Items 1-3 and the generator/flex part of items 4-5 are already reported by
the existing dispatch/residual/reserve CSVs - no GAMS changes needed. The
interconnector contribution to items 4-5 is *not* exported by GAMS today, so
it is derived here in Python from `dispatch.csv` (`*_import`/`*_export` rows)
and the `04_Interconnectors`/`05_IntercoProfiles` input sheets. If a more
precise, ramp-rate-aware figure is wanted directly from the optimisation, the
following additional columns could be added to `reserve.csv`:

    interconnector_ramp_up_available_mw   = SUM(b, impCap(b)*interAvail(b,t) - imp.l(b,t)
                                                     + exp.l(b,t))
    interconnector_ramp_down_available_mw = SUM(b, expCap(b)*interAvail(b,t) - exp.l(b,t)
                                                     + imp.l(b,t))

Items 8-12 are pure post-processing of the residual-load/ramp series and are
not present anywhere else in the codebase; they are computed here.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


from fna_be.config import EXCEL_FILENAME, PATHS, PROJECT_ROOT  # noqa: E402
from fna_be.io.indicators.core import _merge_calendar, _row, _weighted_sum  # noqa: E402
from fna_be.io.excel import (  # noqa: E402
    _control_dict,
    _label,
    _num,
    _read_csv_robust,
    _write_df,
    sort_xlwings_sheets,
)

log = logging.getLogger(__name__)

PERCENTILES: tuple[int, ...] = (1, 5, 10, 25, 50, 75, 90, 95, 99)


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_ramping(
    residual: pd.DataFrame,
    reserve: pd.DataFrame,
    dispatch: pd.DataFrame,
    rep_hours: pd.DataFrame,
    rep_days: pd.DataFrame | None = None,
    borders: pd.DataFrame | None = None,
    border_profiles: pd.DataFrame | None = None,
    target_year: int | None = None,
    mc_ramping_stats: pd.DataFrame | None = None,
    event_threshold_mw: float = 0.0,
) -> pd.DataFrame:
    """Return the tidy ``15_FNA_Ramping`` table."""

    long_df = _ramping_long(residual, reserve, dispatch, rep_hours, rep_days, borders, border_profiles, target_year)

    rows: list[dict[str, Any]] = []
    rows += _residual_load_rows(long_df)
    rows += _ramp_need_rows(long_df)
    rows += _available_margin_rows(long_df)
    rows += _uncovered_need_rows(long_df)
    rows += _event_rows(long_df, event_threshold_mw)
    rows += _distribution_rows(long_df)
    rows += _heatmap_rows(long_df)
    rows += _mc_band_rows(mc_ramping_stats)

    return pd.DataFrame(rows, columns=["section", "scope", "key", "metric", "value", "unit", "notes"])


# ---------------------------------------------------------------------------
# Shared per-hour frame
# ---------------------------------------------------------------------------

def _ramping_long(
    residual: pd.DataFrame,
    reserve: pd.DataFrame,
    dispatch: pd.DataFrame,
    rep_hours: pd.DataFrame,
    rep_days: pd.DataFrame | None,
    borders: pd.DataFrame | None,
    border_profiles: pd.DataFrame | None,
    target_year: int | None,
) -> pd.DataFrame:
    """One row per time_id: residual load, ramps, available margins, uncovered need."""

    df = _merge_calendar(residual, rep_hours, rep_days if rep_days is not None else pd.DataFrame())
    df["ramp_up_mw"] = pd.to_numeric(df.get("ramp_up_mw", 0.0), errors="coerce").fillna(0.0)
    df["ramp_down_mw"] = pd.to_numeric(df.get("ramp_down_mw", 0.0), errors="coerce").fillna(0.0)

    r = reserve.copy()
    r.columns = [str(c).strip().lower() for c in r.columns]
    r = r.rename(columns={"period": "time_id"})
    for col in ["up_available_mw", "down_available_mw", "up_requirement_mw", "down_requirement_mw",
                "up_shortfall_mw", "down_shortfall_mw"]:
        if col in r.columns:
            r[col] = pd.to_numeric(r[col], errors="coerce").fillna(0.0)
    keep = ["time_id"] + [c for c in ["up_available_mw", "down_available_mw"] if c in r.columns]
    df = df.merge(r[keep], on="time_id", how="left")
    if "up_available_mw" not in df.columns:
        df["up_available_mw"] = 0.0
    if "down_available_mw" not in df.columns:
        df["down_available_mw"] = 0.0
    df["up_available_mw"] = pd.to_numeric(df["up_available_mw"], errors="coerce").fillna(0.0)
    df["down_available_mw"] = pd.to_numeric(df["down_available_mw"], errors="coerce").fillna(0.0)

    interco = _interconnector_headroom(dispatch, borders, border_profiles, target_year)
    if not interco.empty:
        df = df.merge(interco, on="time_id", how="left")
    if "interco_up_mw" not in df.columns:
        df["interco_up_mw"] = 0.0
    if "interco_down_mw" not in df.columns:
        df["interco_down_mw"] = 0.0
    df["interco_up_mw"] = pd.to_numeric(df["interco_up_mw"], errors="coerce").fillna(0.0)
    df["interco_down_mw"] = pd.to_numeric(df["interco_down_mw"], errors="coerce").fillna(0.0)

    df["available_up_mw"] = df["up_available_mw"] + df["interco_up_mw"]
    df["available_down_mw"] = df["down_available_mw"] + df["interco_down_mw"]

    df["uncovered_up_mw"] = (df["ramp_up_mw"] - df["available_up_mw"]).clip(lower=0.0)
    df["uncovered_down_mw"] = (df["ramp_down_mw"] - df["available_down_mw"]).clip(lower=0.0)

    # Chronological order so event/interval detection doesn't jump across days.
    if "rep_day_id" in df.columns:
        df = df.sort_values(["rep_day_id", "hour"]).reset_index(drop=True)
    else:
        df = df.sort_values("hour").reset_index(drop=True)
    return df


def _interconnector_headroom(
    dispatch: pd.DataFrame,
    borders: pd.DataFrame | None,
    border_profiles: pd.DataFrame | None,
    target_year: int | None,
) -> pd.DataFrame:
    """Per-time_id additional ramp margin available from interconnectors.

    headroom_up   = additional importable capacity + currently-exported flow
                     that could be reduced (both increase net import, helping
                     cover an upward residual-load ramp).
    headroom_down = additional exportable capacity + currently-imported flow
                     that could be reduced (both increase net export, helping
                     cover a downward residual-load ramp).
    """
    empty = pd.DataFrame(columns=["time_id", "interco_up_mw", "interco_down_mw"])
    if borders is None or border_profiles is None or borders.empty or border_profiles.empty:
        return empty
    if dispatch is None or dispatch.empty:
        return empty

    target_year = target_year or 2025
    b = borders.copy()
    b.columns = [str(c).strip().lower() for c in b.columns]
    if "border_id" not in b.columns:
        return empty

    imp_cols = [c for c in b.columns if c.startswith("import_capacity_mw")]
    exp_cols = [c for c in b.columns if c.startswith("export_capacity_mw")]
    cap_col_imp = f"import_capacity_mw_{target_year}" if f"import_capacity_mw_{target_year}" in b.columns else (imp_cols[0] if imp_cols else None)
    cap_col_exp = f"export_capacity_mw_{target_year}" if f"export_capacity_mw_{target_year}" in b.columns else (exp_cols[0] if exp_cols else None)
    if cap_col_imp is None or cap_col_exp is None:
        return empty

    b["border_id"] = b["border_id"].map(_label)
    imp_cap = dict(zip(b["border_id"], pd.to_numeric(b[cap_col_imp], errors="coerce").fillna(0.0)))
    exp_cap = dict(zip(b["border_id"], pd.to_numeric(b[cap_col_exp], errors="coerce").fillna(0.0)))

    bp = border_profiles.copy()
    bp.columns = [str(c).strip().lower() for c in bp.columns]
    if "border_id" not in bp.columns or "time_id" not in bp.columns:
        return empty
    bp["border_id"] = bp["border_id"].map(_label)
    bp["availability_pct"] = pd.to_numeric(bp.get("availability_pct", 1.0), errors="coerce").fillna(1.0)
    if bp["availability_pct"].max() > 1.5:
        bp["availability_pct"] = bp["availability_pct"] / 100.0
    bp["imp_cap_mw"] = bp["border_id"].map(imp_cap).fillna(0.0) * bp["availability_pct"]
    bp["exp_cap_mw"] = bp["border_id"].map(exp_cap).fillna(0.0) * bp["availability_pct"]

    d = dispatch.copy()
    d.columns = [str(c).strip().lower() for c in d.columns]
    d = d.rename(columns={"period": "time_id"})
    if "category" not in d.columns:
        return empty
    d["dispatch_mw"] = pd.to_numeric(d.get("dispatch_mw"), errors="coerce").fillna(0.0)

    imp_flow = d[d["category"] == "import"].copy()
    imp_flow["border_id"] = imp_flow["resource"].astype(str).str.removesuffix("_import")
    exp_flow = d[d["category"] == "export"].copy()
    exp_flow["border_id"] = exp_flow["resource"].astype(str).str.removesuffix("_export")

    flows = bp[["time_id", "border_id", "imp_cap_mw", "exp_cap_mw"]]
    flows = flows.merge(
        imp_flow[["time_id", "border_id", "dispatch_mw"]].rename(columns={"dispatch_mw": "imp_flow"}),
        on=["time_id", "border_id"], how="left",
    )
    flows = flows.merge(
        exp_flow[["time_id", "border_id", "dispatch_mw"]].rename(columns={"dispatch_mw": "exp_flow"}),
        on=["time_id", "border_id"], how="left",
    )
    flows[["imp_flow", "exp_flow"]] = flows[["imp_flow", "exp_flow"]].fillna(0.0)

    flows["headroom_up"] = (flows["imp_cap_mw"] - flows["imp_flow"]).clip(lower=0.0) + flows["exp_flow"]
    flows["headroom_down"] = (flows["exp_cap_mw"] - flows["exp_flow"]).clip(lower=0.0) + flows["imp_flow"]

    out = flows.groupby("time_id")[["headroom_up", "headroom_down"]].sum().reset_index()
    return out.rename(columns={"headroom_up": "interco_up_mw", "headroom_down": "interco_down_mw"})


# ---------------------------------------------------------------------------
# Outputs 1: residual load
# ---------------------------------------------------------------------------

def _residual_load_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    s = pd.to_numeric(df.get("residual_load_mw"), errors="coerce").dropna()
    if s.empty:
        return []
    return [
        _row("residual_load", "TOTAL", "mean_residual_load_mw", float(s.mean()), "MW",
             "residual load = demand - non-dispatchable (RES available) generation."),
        _row("residual_load", "TOTAL", "min_residual_load_mw", float(s.min()), "MW", ""),
        _row("residual_load", "TOTAL", "max_residual_load_mw", float(s.max()), "MW", ""),
        _row("residual_load", "TOTAL", "annual_residual_load_mwh", _weighted_sum(df, "residual_load_mw"), "MWh", ""),
    ]


# ---------------------------------------------------------------------------
# Outputs 2-3: upward / downward ramping need
# ---------------------------------------------------------------------------

def _ramp_need_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for direction, col in [("up", "ramp_up_mw"), ("down", "ramp_down_mw")]:
        s = df[col]
        rows.append(_row("ramping_need", "TOTAL", f"max_ramp_{direction}_mw_per_mtu", float(s.max()), "MW",
                          f"GAMS residualRamp{direction.title()}: hour-to-hour residual-load change."))
        rows.append(_row("ramping_need", "TOTAL", f"p95_ramp_{direction}_mw_per_mtu", float(np.percentile(s, 95)), "MW", ""))
        rows.append(_row("ramping_need", "TOTAL", f"mean_ramp_{direction}_mw_per_mtu", float(s.mean()), "MW", ""))
        if "season" in df.columns:
            for season, grp in df.groupby("season"):
                rows.append(_row("ramping_need", season, f"max_ramp_{direction}_mw_per_mtu", float(grp[col].max()), "MW", ""))
                rows.append(_row("ramping_need", season, f"p95_ramp_{direction}_mw_per_mtu", float(np.percentile(grp[col], 95)), "MW", ""))
    return rows


# ---------------------------------------------------------------------------
# Outputs 4-5: available upward / downward ramp margin
# ---------------------------------------------------------------------------

def _available_margin_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    specs = [
        ("up", "available_up_mw", "up_available_mw", "interco_up_mw"),
        ("down", "available_down_mw", "down_available_mw", "interco_down_mw"),
    ]
    for direction, total_col, gen_flex_col, interco_col in specs:
        rows.append(_row("available_ramp_margin", "TOTAL", f"mean_available_{direction}_mw", float(df[total_col].mean()), "MW",
                          "Reserve headroom from dispatchable units + storage/demand response "
                          f"(reserve.csv {gen_flex_col}) plus computed interconnector headroom."))
        rows.append(_row("available_ramp_margin", "TOTAL", f"p5_available_{direction}_mw", float(np.percentile(df[total_col], 5)), "MW", ""))
        rows.append(_row("available_ramp_margin", "TOTAL", f"min_available_{direction}_mw", float(df[total_col].min()), "MW", ""))
        rows.append(_row("available_ramp_margin", "TOTAL", f"mean_gen_flex_contribution_{direction}_mw", float(df[gen_flex_col].mean()), "MW", ""))
        rows.append(_row("available_ramp_margin", "TOTAL", f"mean_interconnector_contribution_{direction}_mw", float(df[interco_col].mean()), "MW", ""))
    return rows


# ---------------------------------------------------------------------------
# Outputs 6-7: uncovered upward / downward ramping need
# ---------------------------------------------------------------------------

def _uncovered_need_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for direction, col in [("up", "uncovered_up_mw"), ("down", "uncovered_down_mw")]:
        total_mwh = _weighted_sum(df, col)
        nonzero = df[df[col] > 0]
        rows.append(_row("uncovered_ramping_need", "TOTAL", f"annual_uncovered_{direction}_mwh", total_mwh, "MWh",
                          f"sum over hours of max(0, ramp_{direction}_mw - available_{direction}_mw) * weight_days"))
        rows.append(_row("uncovered_ramping_need", "TOTAL", f"uncovered_{direction}_event_hours", float(len(nonzero)), "hours", ""))
        rows.append(_row("uncovered_ramping_need", "TOTAL", f"max_uncovered_{direction}_mw", float(df[col].max()), "MW", ""))
        if "season" in df.columns:
            for season, grp in df.groupby("season"):
                rows.append(_row("uncovered_ramping_need", season, f"annual_uncovered_{direction}_mwh", _weighted_sum(grp, col), "MWh", ""))
    return rows


# ---------------------------------------------------------------------------
# Outputs 8-9: event duration and interval between events
# ---------------------------------------------------------------------------

def _runs(flag: pd.Series) -> tuple[list[int], list[int]]:
    """Return (event durations, gaps between events), in hours, from a
    chronologically-ordered boolean series."""
    durations: list[int] = []
    gaps: list[int] = []
    in_event = False
    cur_len = 0
    cur_gap = 0
    seen_event = False
    for v in flag.to_numpy():
        if v:
            if not in_event:
                if seen_event:
                    gaps.append(cur_gap)
                in_event = True
                cur_len = 1
                seen_event = True
            else:
                cur_len += 1
        else:
            if in_event:
                durations.append(cur_len)
                in_event = False
                cur_gap = 0
            cur_gap += 1
    if in_event:
        durations.append(cur_len)
    return durations, gaps


def _event_rows(df: pd.DataFrame, threshold_mw: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for direction, col in [("up", "uncovered_up_mw"), ("down", "uncovered_down_mw")]:
        flag = df[col] > threshold_mw
        durations, gaps = _runs(flag)
        note = f"Consecutive-hour runs where uncovered_{direction}_mw > {threshold_mw:g} MW."
        if durations:
            d = np.asarray(durations, dtype=float)
            rows.append(_row("event_duration", "TOTAL", f"n_events_{direction}", float(len(d)), "count", note))
            rows.append(_row("event_duration", "TOTAL", f"mean_event_duration_{direction}_h", float(d.mean()), "hours", ""))
            rows.append(_row("event_duration", "TOTAL", f"max_event_duration_{direction}_h", float(d.max()), "hours", ""))
            rows.append(_row("event_duration", "TOTAL", f"p95_event_duration_{direction}_h", float(np.percentile(d, 95)), "hours", ""))
        else:
            rows.append(_row("event_duration", "TOTAL", f"n_events_{direction}", 0.0, "count", "No uncovered ramping events found."))

        if gaps:
            g = np.asarray(gaps, dtype=float)
            gap_note = f"Gap (hours) between consecutive uncovered_{direction} events."
            rows.append(_row("interval_between_events", "TOTAL", f"mean_interval_{direction}_h", float(g.mean()), "hours", gap_note))
            rows.append(_row("interval_between_events", "TOTAL", f"min_interval_{direction}_h", float(g.min()), "hours", ""))
            rows.append(_row("interval_between_events", "TOTAL", f"p5_interval_{direction}_h", float(np.percentile(g, 5)), "hours", ""))
        else:
            rows.append(_row("interval_between_events", "TOTAL", f"mean_interval_{direction}_h", float("nan"), "hours",
                              "Fewer than two events; interval undefined."))
    return rows


# ---------------------------------------------------------------------------
# Output 10: probability distribution and percentiles
# ---------------------------------------------------------------------------

def _distribution_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    specs = [
        ("ramp_up", "ramp_up_mw"),
        ("ramp_down", "ramp_down_mw"),
        ("uncovered_up", "uncovered_up_mw"),
        ("uncovered_down", "uncovered_down_mw"),
    ]
    for label, col in specs:
        s = pd.to_numeric(df.get(col), errors="coerce").dropna()
        if s.empty:
            continue
        for p in PERCENTILES:
            rows.append(_row("ramp_distribution", "TOTAL", f"{label}_p{p}_mw", float(np.percentile(s, p)), "MW", ""))
        rows.append(_row("ramp_distribution", "TOTAL", f"{label}_mean_mw", float(s.mean()), "MW", ""))
        rows.append(_row("ramp_distribution", "TOTAL", f"{label}_std_mw", float(s.std()), "MW", ""))
    return rows


# ---------------------------------------------------------------------------
# Output 11: heatmap by hour and season
# ---------------------------------------------------------------------------

def _heatmap_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "season" not in df.columns or "hour" not in df.columns:
        return rows
    specs = [
        ("ramp_up", "ramp_up_mw"),
        ("ramp_down", "ramp_down_mw"),
        ("uncovered_up", "uncovered_up_mw"),
        ("uncovered_down", "uncovered_down_mw"),
    ]
    for label, col in specs:
        grouped = df.groupby(["season", "hour"])[col].mean().reset_index()
        for _, r in grouped.iterrows():
            key = f"{r['season']}_H{int(r['hour']):02d}"
            rows.append(_row("heatmap_hour_season", key, f"mean_{label}_mw", float(r[col]), "MW", ""))
    return rows


# ---------------------------------------------------------------------------
# Output 12: Monte Carlo P5/P50/P95 bands
# ---------------------------------------------------------------------------

def mc_ramping_from_scenarios(
    mc_results: dict[int, dict[str, pd.DataFrame]],
    rep_hours: pd.DataFrame,
    rep_days: pd.DataFrame | None,
    borders: pd.DataFrame | None,
    border_profiles: pd.DataFrame | None,
    target_year: int | None,
) -> pd.DataFrame:
    """Per-scenario ramping summary stats, one row per scenario."""

    rows: list[dict[str, Any]] = []
    for scenario_id, result in mc_results.items():
        residual = result.get("residual")
        reserve = result.get("reserve")
        dispatch = result.get("dispatch")
        if residual is None or residual.empty or reserve is None or reserve.empty:
            continue
        long_df = _ramping_long(residual, reserve, dispatch if dispatch is not None else pd.DataFrame(),
                                 rep_hours, rep_days, borders, border_profiles, target_year)
        rows.append({
            "scenario_id": scenario_id,
            "max_ramp_up_mw": float(long_df["ramp_up_mw"].max()),
            "max_ramp_down_mw": float(long_df["ramp_down_mw"].max()),
            "annual_uncovered_up_mwh": _weighted_sum(long_df, "uncovered_up_mw"),
            "annual_uncovered_down_mwh": _weighted_sum(long_df, "uncovered_down_mw"),
        })
    return pd.DataFrame(rows)


def _mc_band_rows(mc_stats: pd.DataFrame | None) -> list[dict[str, Any]]:
    if mc_stats is None or mc_stats.empty:
        return [_row("mc_ramping_bands", "TOTAL", "p50_max_ramp_up_mw", float("nan"), "MW",
                      "No Monte Carlo scenario results supplied.")]

    rows: list[dict[str, Any]] = []
    metric_units = {
        "max_ramp_up_mw": "MW",
        "max_ramp_down_mw": "MW",
        "annual_uncovered_up_mwh": "MWh",
        "annual_uncovered_down_mwh": "MWh",
    }
    for metric, unit in metric_units.items():
        if metric not in mc_stats.columns:
            continue
        values = mc_stats[metric].dropna().to_numpy(dtype=float)
        if values.size == 0:
            continue
        for p, label in [(5, "p5"), (50, "p50"), (95, "p95")]:
            rows.append(_row("mc_ramping_bands", "TOTAL", f"{label}_{metric}", float(np.percentile(values, p)), unit, ""))
        rows.append(_row("mc_ramping_bands", "TOTAL", f"mean_{metric}", float(values.mean()), unit, ""))
    rows.append(_row("mc_ramping_bands", "TOTAL", "n_scenarios", float(len(mc_stats)), "count", ""))
    return rows


# ---------------------------------------------------------------------------
# Standalone entry point: read outputs/workbook, write 15_FNA_Ramping
# ---------------------------------------------------------------------------

def write_ramping_to_excel(
    wb: Any,
    residual: pd.DataFrame,
    reserve: pd.DataFrame,
    dispatch: pd.DataFrame,
    rep_hours: pd.DataFrame,
    rep_days: pd.DataFrame | None = None,
    borders: pd.DataFrame | None = None,
    border_profiles: pd.DataFrame | None = None,
    control: dict[str, Any] | None = None,
    mc_results: dict[int, dict[str, pd.DataFrame]] | None = None,
    event_threshold_mw: float = 0.0,
    sheet_suffix: str = "",
) -> pd.DataFrame:
    """Build the report and write it to ``15_FNA_Ramping``. Returns the table."""

    control = control or {}
    target_year = int(_num(control.get("target_year", 2025))) or 2025

    mc_stats: pd.DataFrame | None = None
    if mc_results:
        mc_stats = mc_ramping_from_scenarios(mc_results, rep_hours, rep_days, borders, border_profiles, target_year)

    table = build_ramping(
        residual=residual, reserve=reserve, dispatch=dispatch, rep_hours=rep_hours, rep_days=rep_days,
        borders=borders, border_profiles=border_profiles, target_year=target_year,
        mc_ramping_stats=mc_stats, event_threshold_mw=event_threshold_mw,
    )
    _write_df(wb, f"15_FNA_Ramping{sheet_suffix}", table)
    sort_xlwings_sheets(wb)
    log.info("Wrote 15_FNA_Ramping%s (%d rows)", sheet_suffix, len(table))
    return table


def main() -> None:
    """CLI entry point: read the latest GAMS outputs + workbook, write the report."""
    from openpyxl import load_workbook

    from fna_be.config import output_excel_path
    from fna_be.io.excel import _read_sheet
    from fna_be.model.run import _open_output_workbook

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    out_dir = Path(PATHS["out_dir"])
    residual = _read_csv_robust(out_dir / "residual.csv")
    reserve = _read_csv_robust(out_dir / "reserve.csv")
    dispatch = _read_csv_robust(out_dir / "dispatch.csv")

    wb_path = PROJECT_ROOT / "excel" / EXCEL_FILENAME
    in_wb = load_workbook(wb_path, read_only=True, data_only=True)
    rep_hours = _read_sheet(in_wb, "02_RepHours")
    borders = _read_sheet(in_wb, "04_Interconnectors")
    border_profiles = _read_sheet(in_wb, "05_IntercoProfiles")
    control = _control_dict(_read_sheet(in_wb, "01_Control"))

    mc_results: dict[int, dict[str, pd.DataFrame]] = {}
    for scenario_dir in sorted(out_dir.glob("scenario_*")):
        disp_path = scenario_dir / "dispatch.csv"
        res_path = scenario_dir / "residual.csv"
        rsv_path = scenario_dir / "reserve.csv"
        if disp_path.exists() and res_path.exists() and rsv_path.exists():
            idx = int(scenario_dir.name.split("_")[-1])
            mc_results[idx] = {
                "dispatch": _read_csv_robust(disp_path),
                "residual": _read_csv_robust(res_path),
                "reserve": _read_csv_robust(rsv_path),
            }

    out_wb = _open_output_workbook()
    write_ramping_to_excel(
        out_wb, residual, reserve, dispatch, rep_hours,
        borders=borders, border_profiles=border_profiles, control=control,
        mc_results=mc_results or None,
    )
    out_wb.save(output_excel_path())


if __name__ == "__main__":
    main()
