"""
fna_shortterm.py - ACER FNA short-term flexibility report (v3).

Builds the short-term system-needs indicator set requested by ACER's FNA
methodology (Art. 10: "system needs related to short-term operations") from
historical D-1 forecast/actual data plus the GAMS dispatch outputs, and
reports it as a single tidy table written to Excel sheet
``16_FNA_ShortTerm``.

Reuses, rather than re-derives, the calendar-merge / weighted-sum / event
helpers already in `fna_indicators.py`, `fna_ramping.py` and `io_excel.py`:

    _merge_calendar, _weighted_sum, _row   <- fna_indicators.py
    _runs                            <- fna_ramping.py
    _read_csv_robust, _write_df, _control_dict,
    _num, sort_xlwings_sheets        <- io_excel.py

Method (matches the user's brief, ACER terminology in parentheses):

 1. residual_load_forecast_error  - error_t = (load_actual-load_forecast)
                                     - (RES_actual-RES_forecast), scaled to
                                     the target year and shrunk by the
                                     expected forecast-improvement factors.
 2. error_distribution             - P0.1/P50/P99.9, mean, std of the error
                                     series, grouped by season, hour, day
                                     type, high/low-RES condition, and
                                     season x RES-condition.
 3/4. shortterm_need               - upward need = P99.9, downward need =
                                     -P0.1 of the (signed) error series, for
                                     each scope above.
 5. shortterm_need_by_speed         - very-fast / fast / slow split of the
                                     annual TOTAL need (FCR/aFRR/mFRR proxy
                                     shares from 01_Control).
 6. available_margin_comparison     - annual need (+ largest-unit-outage and
                                     optional interconnector-outage stress)
                                     vs. reserve.csv available_up/down_mw and
                                     12_Reserve_ForecastError FRR capacity.
 7. uncovered_shortterm_need /
    uncovered_event_stats /
    uncovered_probability           - per-hour uncovered need (need minus
                                     available margin), aggregated by hour,
                                     by season (expected energy, MWh), by
                                     event (duration/interval, reusing
                                     fna_ramping._runs), and by probability
                                     (percentile distribution).
 8. scaling_assumptions              - transparency record of the scaling and
                                     forecast-improvement factors actually
                                     applied.

Data-availability note
-----------------------
ACER's methodology asks for >=2 years of historical D-1 forecast/actual data.
ENTSO-E data is cached under `data/inputs/raw_<CC><year>/`
(`load.csv`, `res_generation_actual.csv`, `res_generation_forecast.csv`). The functions
below accept an arbitrary number of historical years concatenated into a
single time-indexed DataFrame - add a second year's CSVs to get a more robust
P0.1/P99.9 estimate; with one year, the by-(season,hour) percentiles are based
on ~365 samples, sometimes effectively the min/max, and this is reported as a
data-quality caveat in the output `notes`.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


from fna.config import ENTSOE_COUNTRY_CODE, ENTSOE_DATA_YEAR, EXCEL_FILENAME, PATHS, PROJECT_ROOT, csv_output_dir, raw_data_dir, resolve_input_workbook  # noqa: E402
from fna.io.indicators.core import _merge_calendar, _row, _weighted_sum  # noqa: E402
from fna.io.indicators.ramping import _runs  # noqa: E402
from fna.io.excel import (  # noqa: E402
    _control_dict,
    _num,
    _read_csv_robust,
    _write_df,
    sort_xlwings_sheets,
)

log = logging.getLogger(__name__)

_SEASON_BY_MONTH = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
}


# ---------------------------------------------------------------------------
# Step 1: residual-load forecast-error series from historical D-1 data
# ---------------------------------------------------------------------------

def build_historical_error_series(
    load_hist: pd.DataFrame,
    res_actual_hist: pd.DataFrame,
    res_forecast_hist: pd.DataFrame,
    load_scaling_factor: float = 1.0,
    wind_scaling_factor: float = 1.0,
    solar_scaling_factor: float = 1.0,
    load_improvement_pct: float = 0.0,
    wind_improvement_pct: float = 0.0,
    solar_improvement_pct: float = 0.0,
) -> pd.DataFrame:
    """Per-hour residual-load forecast-error series, scaled to the target year.

    `load_hist` needs columns `load_actual`, `load_forecast`;
    `res_actual_hist` needs `wind_offshore`, `wind_onshore`, `solar`;
    `res_forecast_hist` needs `wind_offshore_fc`, `wind_onshore_fc`, `solar_fc`.
    All three share a (tz-aware) datetime index; one or more historical years
    may be concatenated.

    `*_scaling_factor` rescale the historical MW errors to the target-year
    system size (e.g. ratio of target-year to historical RES capacity).
    `*_improvement_pct` shrink the scaled error to reflect expected forecast
    improvements (0 = no improvement, 100 = perfect foresight).
    """
    df = load_hist[["load_actual", "load_forecast"]].copy()
    df = df.join(res_actual_hist[["wind_offshore", "wind_onshore", "solar"]], how="inner")
    df = df.join(res_forecast_hist[["wind_offshore_fc", "wind_onshore_fc", "solar_fc"]], how="inner")
    df = df.dropna()

    load_err = (df["load_actual"] - df["load_forecast"]) * load_scaling_factor * (1.0 - load_improvement_pct / 100.0)
    wind_actual = df["wind_offshore"] + df["wind_onshore"]
    wind_fc = df["wind_offshore_fc"] + df["wind_onshore_fc"]
    wind_err = (wind_actual - wind_fc) * wind_scaling_factor * (1.0 - wind_improvement_pct / 100.0)
    solar_err = (df["solar"] - df["solar_fc"]) * solar_scaling_factor * (1.0 - solar_improvement_pct / 100.0)

    out = pd.DataFrame(index=df.index)
    out["load_error_mw"] = load_err
    out["wind_error_mw"] = wind_err
    out["solar_error_mw"] = solar_err
    out["res_error_mw"] = wind_err + solar_err
    # residual load = demand - RES; error_t = actual_residual - forecast_residual
    out["residual_error_mw"] = out["load_error_mw"] - out["res_error_mw"]

    res_forecast_total = wind_fc * wind_scaling_factor + df["solar_fc"] * solar_scaling_factor
    load_forecast_scaled = df["load_forecast"] * load_scaling_factor
    out["res_forecast_share"] = np.where(load_forecast_scaled > 0, res_forecast_total / load_forecast_scaled, 0.0)

    out["hour"] = out.index.hour
    out["season"] = out.index.month.map(_SEASON_BY_MONTH)
    out["day_type"] = np.where(out.index.dayofweek >= 5, "weekend", "weekday")

    median_share = float(np.median(out["res_forecast_share"])) if not out.empty else 0.0
    out["res_condition"] = np.where(out["res_forecast_share"] >= median_share, "high_res", "low_res")

    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_short_term(
    historical_errors: pd.DataFrame,
    residual: pd.DataFrame,
    reserve: pd.DataFrame,
    rep_hours: pd.DataFrame,
    reserve_forecast_error: pd.DataFrame | None = None,
    up_percentile: float = 99.9,
    dn_percentile: float = 0.1,
    speed_shares_pct: dict[str, float] | None = None,
    interconnector_outage_stress_mw: float = 0.0,
    event_threshold_mw: float = 0.0,
    scaling_notes: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Return the tidy ``16_FNA_ShortTerm`` table."""

    hist = historical_errors
    speed_shares_pct = speed_shares_pct or {"very_fast": 10.0, "fast": 30.0}

    rows: list[dict[str, Any]] = []
    rows += _error_overview_rows(hist)
    rows += _distribution_rows(hist, up_percentile, dn_percentile)
    rows += _need_rows(hist, up_percentile, dn_percentile)
    rows += _speed_split_rows(hist, up_percentile, dn_percentile, speed_shares_pct)

    need_total_up = float(np.percentile(hist["residual_error_mw"], up_percentile)) if not hist.empty else 0.0
    need_total_dn = float(-np.percentile(hist["residual_error_mw"], dn_percentile)) if not hist.empty else 0.0

    rows += _outage_stress_rows(reserve_forecast_error, interconnector_outage_stress_mw, need_total_up)
    rows += _margin_comparison_rows(need_total_up, need_total_dn, reserve, reserve_forecast_error,
                                     interconnector_outage_stress_mw)

    uncovered = _build_uncovered(hist, residual, reserve, rep_hours, reserve_forecast_error,
                                  up_percentile, dn_percentile, interconnector_outage_stress_mw)
    rows += _uncovered_need_rows(uncovered)
    rows += _uncovered_event_rows(uncovered, event_threshold_mw)
    rows += _uncovered_probability_rows(uncovered)

    rows += _scaling_assumption_rows(scaling_notes, up_percentile, dn_percentile, speed_shares_pct,
                                      interconnector_outage_stress_mw)

    return pd.DataFrame(rows, columns=["section", "scope", "key", "metric", "value", "unit", "notes"])


# ---------------------------------------------------------------------------
# Output 1: residual-load forecast-error overview
# ---------------------------------------------------------------------------

def _error_overview_rows(hist: pd.DataFrame) -> list[dict[str, Any]]:
    if hist.empty:
        return [_row("residual_load_forecast_error", "TOTAL", "n_hours", 0.0, "count",
                      "No historical forecast/actual data supplied.")]
    rows = [_row("residual_load_forecast_error", "TOTAL", "n_hours", float(len(hist)), "count",
                  "Number of historical hours used to build the error distribution.")]
    for label, col in [("residual", "residual_error_mw"), ("load", "load_error_mw"),
                        ("wind", "wind_error_mw"), ("solar", "solar_error_mw")]:
        s = hist[col]
        rows.append(_row("residual_load_forecast_error", "TOTAL", f"{label}_mean_mw", float(s.mean()), "MW", ""))
        rows.append(_row("residual_load_forecast_error", "TOTAL", f"{label}_std_mw", float(s.std()), "MW", ""))
        rows.append(_row("residual_load_forecast_error", "TOTAL", f"{label}_p0_1_mw", float(np.percentile(s, 0.1)), "MW", ""))
        rows.append(_row("residual_load_forecast_error", "TOTAL", f"{label}_p99_9_mw", float(np.percentile(s, 99.9)), "MW", ""))
    return rows


# ---------------------------------------------------------------------------
# Output 2: error distributions by season / hour / day type / RES condition
# ---------------------------------------------------------------------------

def _distribution_rows(hist: pd.DataFrame, up_pct: float, dn_pct: float) -> list[dict[str, Any]]:
    if hist.empty:
        return []
    rows: list[dict[str, Any]] = []

    def _stats(grp: pd.Series, scope: str, n_note: str = "") -> None:
        rows.append(_row("error_distribution", scope, "mean_mw", float(grp.mean()), "MW", n_note))
        rows.append(_row("error_distribution", scope, "std_mw", float(grp.std()), "MW", ""))
        rows.append(_row("error_distribution", scope, "p_dn_mw", float(np.percentile(grp, dn_pct)), "MW",
                          f"P{dn_pct:g}"))
        rows.append(_row("error_distribution", scope, "median_mw", float(np.percentile(grp, 50)), "MW", ""))
        rows.append(_row("error_distribution", scope, "p_up_mw", float(np.percentile(grp, up_pct)), "MW",
                          f"P{up_pct:g}"))
        rows.append(_row("error_distribution", scope, "n_hours", float(len(grp)), "count", ""))

    note = ("Single-year sample: P{:.1f}/P{:.1f} of <=365 points are close to the "
            "min/max of the group; add a second historical year to firm this up.").format(dn_pct, up_pct)

    _stats(hist["residual_error_mw"], "TOTAL", note)
    for season, grp in hist.groupby("season"):
        _stats(grp["residual_error_mw"], f"season_{season}")
    for hour, grp in hist.groupby("hour"):
        _stats(grp["residual_error_mw"], f"hour_H{int(hour):02d}")
    for day_type, grp in hist.groupby("day_type"):
        _stats(grp["residual_error_mw"], f"daytype_{day_type}")
    for res_cond, grp in hist.groupby("res_condition"):
        _stats(grp["residual_error_mw"], f"res_{res_cond}")
    for (season, res_cond), grp in hist.groupby(["season", "res_condition"]):
        _stats(grp["residual_error_mw"], f"season_{season}_res_{res_cond}")
    return rows


# ---------------------------------------------------------------------------
# Outputs 3-4: upward / downward short-term needs
# ---------------------------------------------------------------------------

def _need_rows(hist: pd.DataFrame, up_pct: float, dn_pct: float) -> list[dict[str, Any]]:
    if hist.empty:
        return []
    rows: list[dict[str, Any]] = []

    def _needs(grp: pd.Series, scope: str) -> None:
        rows.append(_row("shortterm_need", scope, "upward_need_mw", float(np.percentile(grp, up_pct)), "MW",
                          f"P{up_pct:g} of the residual-load forecast-error distribution."))
        rows.append(_row("shortterm_need", scope, "downward_need_mw", float(-np.percentile(grp, dn_pct)), "MW",
                          f"-P{dn_pct:g} of the residual-load forecast-error distribution."))

    _needs(hist["residual_error_mw"], "TOTAL")
    for season, grp in hist.groupby("season"):
        _needs(grp["residual_error_mw"], f"season_{season}")
    for hour, grp in hist.groupby("hour"):
        _needs(grp["residual_error_mw"], f"hour_H{int(hour):02d}")
    for day_type, grp in hist.groupby("day_type"):
        _needs(grp["residual_error_mw"], f"daytype_{day_type}")
    for res_cond, grp in hist.groupby("res_condition"):
        _needs(grp["residual_error_mw"], f"res_{res_cond}")
    return rows


# ---------------------------------------------------------------------------
# Output 5: very-fast / fast / slow split (optional)
# ---------------------------------------------------------------------------

def _speed_split_rows(hist: pd.DataFrame, up_pct: float, dn_pct: float, shares_pct: dict[str, float]) -> list[dict[str, Any]]:
    if hist.empty:
        return []
    very_fast = float(shares_pct.get("very_fast", 10.0))
    fast = float(shares_pct.get("fast", 30.0))
    slow = max(0.0, 100.0 - very_fast - fast)

    need_up = float(np.percentile(hist["residual_error_mw"], up_pct))
    need_dn = float(-np.percentile(hist["residual_error_mw"], dn_pct))

    rows: list[dict[str, Any]] = []
    note = ("Proxy split of the annual TOTAL short-term need into FCR-like "
            "(very_fast), aFRR-like (fast) and mFRR-like (slow) shares; shares "
            "configurable via 01_Control shortterm_very_fast_share_pct / "
            "shortterm_fast_share_pct.")
    for band, share in [("very_fast", very_fast), ("fast", fast), ("slow", slow)]:
        rows.append(_row("shortterm_need_by_speed", band, "share_pct", share, "%", note if band == "very_fast" else ""))
        rows.append(_row("shortterm_need_by_speed", band, "upward_need_mw", need_up * share / 100.0, "MW", ""))
        rows.append(_row("shortterm_need_by_speed", band, "downward_need_mw", need_dn * share / 100.0, "MW", ""))
    return rows


# ---------------------------------------------------------------------------
# Outage stress (largest-unit + optional interconnector)
# ---------------------------------------------------------------------------

def _outage_stress_rows(reserve_forecast_error: pd.DataFrame | None, interconnector_outage_stress_mw: float,
                         need_total_up: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    largest_unit_mean = 0.0
    largest_unit_max = 0.0
    if reserve_forecast_error is not None and not reserve_forecast_error.empty:
        rfe = reserve_forecast_error.copy()
        rfe.columns = [str(c).strip().lower() for c in rfe.columns]
        if "largest_unit_outage_stress_mw" in rfe.columns:
            s = pd.to_numeric(rfe["largest_unit_outage_stress_mw"], errors="coerce").dropna()
            if not s.empty:
                largest_unit_mean = float(s.mean())
                largest_unit_max = float(s.max())

    rows.append(_row("outage_stress", "TOTAL", "largest_unit_outage_stress_mean_mw", largest_unit_mean, "MW",
                      "N-1 largest-unit outage stress from 12_Reserve_ForecastError, added to the "
                      "upward short-term need as a deterministic contingency component."))
    rows.append(_row("outage_stress", "TOTAL", "largest_unit_outage_stress_max_mw", largest_unit_max, "MW", ""))
    rows.append(_row("outage_stress", "TOTAL", "interconnector_outage_stress_mw", interconnector_outage_stress_mw, "MW",
                      "Optional interconnector-outage stress (01_Control interconnector_outage_stress_mw); "
                      "0 if not modelled."))
    rows.append(_row("outage_stress", "TOTAL", "combined_upward_need_mw",
                      need_total_up + largest_unit_mean + interconnector_outage_stress_mw, "MW",
                      "shortterm_need.TOTAL.upward_need_mw + largest_unit_outage_stress_mean_mw + "
                      "interconnector_outage_stress_mw."))
    return rows


# ---------------------------------------------------------------------------
# Output 6: comparison against residual margins and FRR capacity
# ---------------------------------------------------------------------------

def _margin_comparison_rows(need_total_up: float, need_total_dn: float, reserve: pd.DataFrame,
                             reserve_forecast_error: pd.DataFrame | None,
                             interconnector_outage_stress_mw: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    r = reserve.copy()
    r.columns = [str(c).strip().lower() for c in r.columns]
    mean_up_avail = float(pd.to_numeric(r.get("up_available_mw"), errors="coerce").fillna(0.0).mean()) if "up_available_mw" in r.columns else 0.0
    mean_dn_avail = float(pd.to_numeric(r.get("down_available_mw"), errors="coerce").fillna(0.0).mean()) if "down_available_mw" in r.columns else 0.0

    rows.append(_row("available_margin_comparison", "TOTAL", "upward_need_mw", need_total_up, "MW", ""))
    rows.append(_row("available_margin_comparison", "TOTAL", "mean_available_up_mw", mean_up_avail, "MW",
                      "Mean reserve.csv up_available_mw (dispatchable + storage/DR reserve headroom)."))
    rows.append(_row("available_margin_comparison", "TOTAL", "up_margin_gap_mw", need_total_up - mean_up_avail, "MW",
                      "upward_need_mw - mean_available_up_mw; positive means the average residual "
                      "margin would not cover the short-term need."))

    rows.append(_row("available_margin_comparison", "TOTAL", "downward_need_mw", need_total_dn, "MW", ""))
    rows.append(_row("available_margin_comparison", "TOTAL", "mean_available_down_mw", mean_dn_avail, "MW", ""))
    rows.append(_row("available_margin_comparison", "TOTAL", "down_margin_gap_mw", need_total_dn - mean_dn_avail, "MW", ""))

    if reserve_forecast_error is not None and not reserve_forecast_error.empty:
        rfe = reserve_forecast_error.copy()
        rfe.columns = [str(c).strip().lower() for c in rfe.columns]
        frr_up_cols = [c for c in ["fcr_mw", "afrr_up_mw", "mfrr_up_mw"] if c in rfe.columns]
        frr_dn_cols = [c for c in ["fcr_mw", "afrr_down_mw", "mfrr_down_mw"] if c in rfe.columns]
        if frr_up_cols:
            frr_up = pd.to_numeric(rfe[frr_up_cols].sum(axis=1), errors="coerce").fillna(0.0)
            mean_frr_up = float(frr_up.mean())
            rows.append(_row("available_margin_comparison", "TOTAL", "mean_frr_up_capacity_mw", mean_frr_up, "MW",
                              "Mean FCR + aFRR_up + mFRR_up from 12_Reserve_ForecastError."))
            rows.append(_row("available_margin_comparison", "TOTAL", "up_need_vs_frr_capacity_mw",
                              need_total_up + interconnector_outage_stress_mw - mean_frr_up, "MW",
                              "(upward_need_mw + interconnector_outage_stress_mw) - mean_frr_up_capacity_mw."))
        if frr_dn_cols:
            frr_dn = pd.to_numeric(rfe[frr_dn_cols].sum(axis=1), errors="coerce").fillna(0.0)
            mean_frr_dn = float(frr_dn.mean())
            rows.append(_row("available_margin_comparison", "TOTAL", "mean_frr_down_capacity_mw", mean_frr_dn, "MW",
                              "Mean FCR + aFRR_down + mFRR_down from 12_Reserve_ForecastError."))
            rows.append(_row("available_margin_comparison", "TOTAL", "down_need_vs_frr_capacity_mw",
                              need_total_dn - mean_frr_dn, "MW",
                              "downward_need_mw - mean_frr_down_capacity_mw."))
    return rows


# ---------------------------------------------------------------------------
# Output 7: uncovered short-term need, by hour / event / probability / energy
# ---------------------------------------------------------------------------

def _build_uncovered(
    hist: pd.DataFrame,
    residual: pd.DataFrame,
    reserve: pd.DataFrame,
    rep_hours: pd.DataFrame,
    reserve_forecast_error: pd.DataFrame | None,
    up_pct: float,
    dn_pct: float,
    interconnector_outage_stress_mw: float,
) -> pd.DataFrame:
    """Per operational hour (target-year, 8760h): need vs available margin."""

    df = _merge_calendar(residual, rep_hours, pd.DataFrame())
    if df.empty or hist.empty:
        return pd.DataFrame()

    r = reserve.copy()
    r.columns = [str(c).strip().lower() for c in r.columns]
    r = r.rename(columns={"period": "time_id"})
    keep = ["time_id"] + [c for c in ["up_available_mw", "down_available_mw"] if c in r.columns]
    df = df.merge(r[keep], on="time_id", how="left")
    for col in ["up_available_mw", "down_available_mw"]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # Per-(season,hour) need from the historical error distribution.
    grp = hist.groupby(["season", "hour"])["residual_error_mw"]
    need_lookup = pd.DataFrame({
        "need_up_mw": grp.apply(lambda s: float(np.percentile(s, up_pct))),
        "need_down_mw": grp.apply(lambda s: float(-np.percentile(s, dn_pct))),
    }).reset_index()
    df = df.merge(need_lookup, on=["season", "hour"], how="left")
    df["need_up_mw"] = df["need_up_mw"].fillna(0.0)
    df["need_down_mw"] = df["need_down_mw"].fillna(0.0)

    largest_unit = pd.Series(0.0, index=df.index)
    if reserve_forecast_error is not None and not reserve_forecast_error.empty:
        rfe = reserve_forecast_error.copy()
        rfe.columns = [str(c).strip().lower() for c in rfe.columns]
        rfe = rfe.rename(columns={"period": "time_id"})
        if "time_id" in rfe.columns and "largest_unit_outage_stress_mw" in rfe.columns:
            lu = rfe[["time_id", "largest_unit_outage_stress_mw"]].copy()
            lu["largest_unit_outage_stress_mw"] = pd.to_numeric(lu["largest_unit_outage_stress_mw"], errors="coerce").fillna(0.0)
            df = df.merge(lu, on="time_id", how="left")
            largest_unit = df["largest_unit_outage_stress_mw"].fillna(0.0)
    df["largest_unit_outage_stress_mw"] = largest_unit

    df["uncovered_up_mw"] = (
        df["need_up_mw"] + df["largest_unit_outage_stress_mw"] + interconnector_outage_stress_mw - df["up_available_mw"]
    ).clip(lower=0.0)
    df["uncovered_down_mw"] = (df["need_down_mw"] - df["down_available_mw"]).clip(lower=0.0)

    if "rep_day_id" in df.columns:
        df = df.sort_values(["rep_day_id", "hour"]).reset_index(drop=True)
    else:
        df = df.sort_values("hour").reset_index(drop=True)
    return df


def _uncovered_need_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return [_row("uncovered_shortterm_need", "TOTAL", "annual_uncovered_up_mwh", float("nan"), "MWh",
                      "No historical error distribution and/or GAMS outputs supplied.")]
    rows: list[dict[str, Any]] = []
    for direction, col in [("up", "uncovered_up_mw"), ("down", "uncovered_down_mw")]:
        total_mwh = _weighted_sum(df, col)
        nonzero = df[df[col] > 0]
        rows.append(_row("uncovered_shortterm_need", "TOTAL", f"annual_uncovered_{direction}_mwh", total_mwh, "MWh",
                          f"sum over hours of max(0, need_{direction}_mw [+ outage stress] - available_{direction}_mw) "
                          "* weight_days"))
        rows.append(_row("uncovered_shortterm_need", "TOTAL", f"uncovered_{direction}_event_hours", float(len(nonzero)), "hours", ""))
        rows.append(_row("uncovered_shortterm_need", "TOTAL", f"max_uncovered_{direction}_mw", float(df[col].max()), "MW", ""))
        if "season" in df.columns:
            for season, grp in df.groupby("season"):
                rows.append(_row("uncovered_shortterm_need", season, f"annual_uncovered_{direction}_mwh", _weighted_sum(grp, col), "MWh", ""))
        if "hour" in df.columns:
            for hour, grp in df.groupby("hour"):
                rows.append(_row("uncovered_shortterm_need", f"hour_H{int(hour):02d}", f"mean_uncovered_{direction}_mw", float(grp[col].mean()), "MW", ""))
    return rows


def _uncovered_event_rows(df: pd.DataFrame, threshold_mw: float) -> list[dict[str, Any]]:
    if df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for direction, col in [("up", "uncovered_up_mw"), ("down", "uncovered_down_mw")]:
        flag = df[col] > threshold_mw
        durations, gaps = _runs(flag)
        note = f"Consecutive-hour runs where uncovered_{direction}_mw > {threshold_mw:g} MW."
        if durations:
            d = np.asarray(durations, dtype=float)
            rows.append(_row("uncovered_event_stats", "TOTAL", f"n_events_{direction}", float(len(d)), "count", note))
            rows.append(_row("uncovered_event_stats", "TOTAL", f"mean_event_duration_{direction}_h", float(d.mean()), "hours", ""))
            rows.append(_row("uncovered_event_stats", "TOTAL", f"max_event_duration_{direction}_h", float(d.max()), "hours", ""))
            rows.append(_row("uncovered_event_stats", "TOTAL", f"p95_event_duration_{direction}_h", float(np.percentile(d, 95)), "hours", ""))
        else:
            rows.append(_row("uncovered_event_stats", "TOTAL", f"n_events_{direction}", 0.0, "count", "No uncovered short-term events found."))

        if gaps:
            g = np.asarray(gaps, dtype=float)
            rows.append(_row("uncovered_event_stats", "TOTAL", f"mean_interval_{direction}_h", float(g.mean()), "hours",
                              f"Gap (hours) between consecutive uncovered_{direction} events."))
            rows.append(_row("uncovered_event_stats", "TOTAL", f"min_interval_{direction}_h", float(g.min()), "hours", ""))
        else:
            rows.append(_row("uncovered_event_stats", "TOTAL", f"mean_interval_{direction}_h", float("nan"), "hours",
                              "Fewer than two events; interval undefined."))
    return rows


def _uncovered_probability_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for direction, col in [("up", "uncovered_up_mw"), ("down", "uncovered_down_mw")]:
        s = df[col].dropna()
        if s.empty:
            continue
        for p in (50, 75, 90, 95, 99):
            rows.append(_row("uncovered_probability", "TOTAL", f"{direction}_p{p}_mw", float(np.percentile(s, p)), "MW", ""))
        rows.append(_row("uncovered_probability", "TOTAL", f"{direction}_prob_gt0_pct", float((s > 0).mean() * 100.0), "%",
                          f"Share of hours with uncovered_{direction}_mw > 0."))
    return rows


# ---------------------------------------------------------------------------
# Transparency: scaling / improvement assumptions actually applied
# ---------------------------------------------------------------------------

def _scaling_assumption_rows(scaling_notes: dict[str, float] | None, up_pct: float, dn_pct: float,
                              speed_shares_pct: dict[str, float],
                              interconnector_outage_stress_mw: float) -> list[dict[str, Any]]:
    rows = [
        _row("scaling_assumptions", "TOTAL", "shortterm_up_percentile", up_pct, "percentile", "01_Control shortterm_up_percentile."),
        _row("scaling_assumptions", "TOTAL", "shortterm_dn_percentile", dn_pct, "percentile", "01_Control shortterm_dn_percentile."),
        _row("scaling_assumptions", "TOTAL", "very_fast_share_pct", float(speed_shares_pct.get("very_fast", 10.0)), "%", ""),
        _row("scaling_assumptions", "TOTAL", "fast_share_pct", float(speed_shares_pct.get("fast", 30.0)), "%", ""),
        _row("scaling_assumptions", "TOTAL", "interconnector_outage_stress_mw", interconnector_outage_stress_mw, "MW", ""),
    ]
    if scaling_notes:
        for key, value in scaling_notes.items():
            unit = "%" if "improvement" in key else "x"
            rows.append(_row("scaling_assumptions", "TOTAL", key, float(value), unit, ""))
    return rows


# ---------------------------------------------------------------------------
# Scaling-factor helpers
# ---------------------------------------------------------------------------

def compute_scaling_factors(
    rep_hours: pd.DataFrame,
    res_portfolios: pd.DataFrame,
    load_hist: pd.DataFrame,
    historical_res_capacity_mw: dict[str, float] | None,
    target_year: int | None,
) -> tuple[float, float, float, dict[str, float]]:
    """Derive load/wind/solar scaling factors (target-year / historical).

    Falls back to 1.0 (no scaling) for any factor that cannot be computed,
    e.g. if `historical_res_capacity_mw` is not supplied.
    """
    target_year = target_year or 2025
    notes: dict[str, float] = {}

    load_scaling = 1.0
    rh = rep_hours.copy()
    rh.columns = [str(c).strip().lower() for c in rh.columns]
    demand_col = f"gross_demand_mw_{target_year}"
    if demand_col in rh.columns and "load_actual" in load_hist.columns and not load_hist.empty:
        target_demand = pd.to_numeric(rh[demand_col], errors="coerce").fillna(0.0).sum()
        hist_demand = pd.to_numeric(load_hist["load_actual"], errors="coerce").fillna(0.0).sum()
        if hist_demand > 0:
            load_scaling = float(target_demand / hist_demand)
    notes["load_scaling_factor"] = load_scaling

    wind_scaling = 1.0
    solar_scaling = 1.0
    if historical_res_capacity_mw:
        rp = res_portfolios.copy()
        rp.columns = [str(c).strip().lower() for c in rp.columns]
        cap_col = f"capacity_mw_{target_year}"
        if cap_col in rp.columns and "technology" in rp.columns:
            cap = pd.to_numeric(rp[cap_col], errors="coerce").fillna(0.0)
            wind_target = float(cap[rp["technology"].str.contains("wind", case=False, na=False)].sum())
            solar_target = float(cap[rp["technology"].str.contains("solar", case=False, na=False)].sum())
            wind_hist = float(historical_res_capacity_mw.get("wind", 0.0))
            solar_hist = float(historical_res_capacity_mw.get("solar", 0.0))
            if wind_hist > 0:
                wind_scaling = wind_target / wind_hist
            if solar_hist > 0:
                solar_scaling = solar_target / solar_hist
    notes["wind_scaling_factor"] = wind_scaling
    notes["solar_scaling_factor"] = solar_scaling

    return load_scaling, wind_scaling, solar_scaling, notes


# ---------------------------------------------------------------------------
# Standalone entry point: read outputs/workbook/raw data, write 16_FNA_ShortTerm
# ---------------------------------------------------------------------------

def write_short_term_to_excel(
    wb: Any,
    residual: pd.DataFrame,
    reserve: pd.DataFrame,
    rep_hours: pd.DataFrame,
    res_portfolios: pd.DataFrame,
    load_hist: pd.DataFrame,
    res_actual_hist: pd.DataFrame,
    res_forecast_hist: pd.DataFrame,
    reserve_forecast_error: pd.DataFrame | None = None,
    historical_res_capacity_mw: dict[str, float] | None = None,
    control: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Build the report and write it to ``16_FNA_ShortTerm``. Returns the table."""

    control = control or {}
    target_year = int(_num(control.get("target_year", 2025))) or 2025
    up_pct = _num(control.get("shortterm_up_percentile", 99.9)) or 99.9
    dn_pct = _num(control.get("shortterm_dn_percentile", 0.1))
    load_improve = _num(control.get("load_forecast_error_improvement_pct", 0.0))
    wind_improve = _num(control.get("wind_forecast_error_improvement_pct", 0.0))
    solar_improve = _num(control.get("solar_forecast_error_improvement_pct", 0.0))
    very_fast_share = _num(control.get("shortterm_very_fast_share_pct", 10.0)) or 10.0
    fast_share = _num(control.get("shortterm_fast_share_pct", 30.0)) or 30.0
    interco_outage = _num(control.get("interconnector_outage_stress_mw", 0.0))

    load_scaling, wind_scaling, solar_scaling, scaling_notes = compute_scaling_factors(
        rep_hours, res_portfolios, load_hist, historical_res_capacity_mw, target_year,
    )
    scaling_notes["load_forecast_error_improvement_pct"] = load_improve
    scaling_notes["wind_forecast_error_improvement_pct"] = wind_improve
    scaling_notes["solar_forecast_error_improvement_pct"] = solar_improve

    hist = build_historical_error_series(
        load_hist, res_actual_hist, res_forecast_hist,
        load_scaling_factor=load_scaling, wind_scaling_factor=wind_scaling, solar_scaling_factor=solar_scaling,
        load_improvement_pct=load_improve, wind_improvement_pct=wind_improve, solar_improvement_pct=solar_improve,
    )

    table = build_short_term(
        historical_errors=hist, residual=residual, reserve=reserve, rep_hours=rep_hours,
        reserve_forecast_error=reserve_forecast_error, up_percentile=up_pct, dn_percentile=dn_pct,
        speed_shares_pct={"very_fast": very_fast_share, "fast": fast_share},
        interconnector_outage_stress_mw=interco_outage, scaling_notes=scaling_notes,
    )
    _write_df(wb, "16_FNA_ShortTerm", table)
    sort_xlwings_sheets(wb)
    log.info("Wrote 16_FNA_ShortTerm (%d rows)", len(table))
    return table


def main() -> None:
    """CLI entry point: read raw history + GAMS outputs + workbook, write the report."""
    from openpyxl import load_workbook

    from fna.config import output_excel_path
    from fna.io.excel import _read_sheet
    from fna.model.run import _open_output_workbook

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    out_dir = Path(PATHS["out_dir"])
    results_dir = csv_output_dir(out_dir) if csv_output_dir(out_dir).exists() else out_dir
    residual = _read_csv_robust(results_dir / "residual.csv")
    reserve = _read_csv_robust(results_dir / "reserve.csv")

    raw_dir = raw_data_dir(ENTSOE_COUNTRY_CODE, ENTSOE_DATA_YEAR or 2023)
    load_hist = pd.read_csv(raw_dir / "load.csv", index_col=0)
    load_hist.index = pd.to_datetime(load_hist.index, utc=True)
    res_actual_hist = pd.read_csv(raw_dir / "res_generation_actual.csv", index_col=0)
    res_actual_hist.index = pd.to_datetime(res_actual_hist.index, utc=True)
    res_forecast_hist = pd.read_csv(raw_dir / "res_generation_forecast.csv", index_col=0)
    res_forecast_hist.index = pd.to_datetime(res_forecast_hist.index, utc=True)

    historical_res_capacity_mw: dict[str, float] | None = None
    cap_path = raw_dir / "installed_capacity.csv"
    if cap_path.exists():
        ic = pd.read_csv(cap_path, index_col=0)
        if not ic.empty:
            row = ic.iloc[0]
            historical_res_capacity_mw = {
                "wind": float(row.get("Wind Offshore", 0.0)) + float(row.get("Wind Onshore", 0.0)),
                "solar": float(row.get("Solar", 0.0)),
            }

    wb_path = resolve_input_workbook(EXCEL_FILENAME)
    in_wb = load_workbook(wb_path, read_only=True, data_only=True)
    rep_hours = _read_sheet(in_wb, "02_RepHours")
    res_portfolios = _read_sheet(in_wb, "07_RES_Portfolios")
    reserve_forecast_error = _read_sheet(in_wb, "12_Reserve_ForecastError")
    control = _control_dict(_read_sheet(in_wb, "01_Control"))

    out_wb = _open_output_workbook()
    write_short_term_to_excel(
        out_wb, residual, reserve, rep_hours, res_portfolios,
        load_hist, res_actual_hist, res_forecast_hist,
        reserve_forecast_error=reserve_forecast_error,
        historical_res_capacity_mw=historical_res_capacity_mw,
        control=control,
    )
    out_wb.save(output_excel_path())


if __name__ == "__main__":
    main()
