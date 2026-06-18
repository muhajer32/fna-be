"""
fna_res_integration.py - ACER FNA RES-integration report (v3).

Builds the RES-integration indicator set requested by ACER's FNA methodology
(Art. 8: "system needs related to the integration of RES") from the GAMS
output CSVs and reports it as a single tidy table written to Excel sheet
``14_FNA_RES_Integration``.

Reuses, rather than re-derives, the calendar-merge / weighted-sum helpers and
Excel/CSV plumbing already in `fna_indicators.py` and `io_excel.py`:

    _merge_calendar, _weighted_sum, _row  <- fna_indicators.py
    _read_csv_robust, _write_df,
    _control_dict, _num, sort_xlwings_sheets <- io_excel.py

Outputs (ACER terminology), all rows of one tidy DataFrame with columns
``section, scope, key, metric, value, unit, notes``:

 1. annual_res_generation        - potential RES generation (res_used + curtailment), by technology + TOTAL
 2. annual_res_curtailment        - curtailed RES energy, by technology + TOTAL
 3. curtailment_share             - curtailment / generation, by technology + TOTAL
 4. effectively_integrated_res    - RES generation actually absorbed (res_used), by technology + TOTAL
 5. uncovered_res_integration_need- gap between the NECP target-year RES target and
                                     effectively integrated RES (TOTAL only)
 6. curtailment_statistics        - seasonal / day-type / hourly curtailment breakdown, TOTAL + by technology
 7. mc_curtailment_percentiles    - P5 / P50 / P95 (+ mean/min/max) of annual RES
                                     curtailment across Monte Carlo scenarios
 8. required_flexibility_ep_ratio - additional flexible power/energy capacity needed to
                                     absorb high-curtailment hours, for E/P duration
                                     classes 2h, 4h, 8h, 20h, 50h, 100h, 200h
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from config import EXCEL_FILENAME, PATHS, PROJECT_ROOT  # noqa: E402
from fna_indicators import _merge_calendar, _row, _weighted_sum  # noqa: E402
from io_excel import (  # noqa: E402
    _control_dict,
    _num,
    _read_csv_robust,
    _write_df,
    sort_xlwings_sheets,
)

log = logging.getLogger(__name__)

EP_RATIOS_H: tuple[float, ...] = (2.0, 4.0, 8.0, 20.0, 50.0, 100.0, 200.0)
CURTAILMENT_SUFFIX = "_curtailment"


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_res_integration(
    dispatch: pd.DataFrame,
    residual: pd.DataFrame,
    res_portfolios: pd.DataFrame,
    rep_hours: pd.DataFrame,
    necp_res_target_mwh: float | None = None,
    necp_res_target_share_pct: float | None = None,
    curtailment_threshold_pct: float | None = None,
    mc_curtailment_mwh: list[float] | None = None,
    ep_ratios_h: tuple[float, ...] = EP_RATIOS_H,
) -> pd.DataFrame:
    """Return the tidy ``14_FNA_RES_Integration`` table.

    `mc_curtailment_mwh`, if given, is a list of annual RES-curtailment totals
    (MWh), one per Monte Carlo scenario (see `mc_curtailment_from_scenarios`).
    """

    res_long = _res_long(dispatch, res_portfolios, rep_hours)
    rows: list[dict[str, Any]] = []

    rows += _annual_generation_rows(res_long)
    rows += _annual_curtailment_rows(res_long)
    rows += _curtailment_share_rows(res_long)
    rows += _effective_res_rows(res_long)
    rows += _uncovered_need_rows(
        res_long, residual, rep_hours, necp_res_target_mwh, necp_res_target_share_pct,
    )
    rows += _curtailment_statistics_rows(res_long, curtailment_threshold_pct)
    rows += _mc_percentile_rows(mc_curtailment_mwh)
    rows += _ep_ratio_rows(res_long, ep_ratios_h)

    return pd.DataFrame(rows, columns=["section", "scope", "key", "metric", "value", "unit", "notes"])


# ---------------------------------------------------------------------------
# Shared per-resource, per-hour frame
# ---------------------------------------------------------------------------

def _res_long(dispatch: pd.DataFrame, res_portfolios: pd.DataFrame, rep_hours: pd.DataFrame) -> pd.DataFrame:
    """One row per (res_id, time_id): generation_mw, curtailment_mw, potential_mw,
    technology, season, day_type, hour, weight_days."""

    d = dispatch.copy()
    d.columns = [str(c).strip().lower() for c in d.columns]
    d = d.rename(columns={"period": "time_id"})
    d["dispatch_mw"] = pd.to_numeric(d.get("dispatch_mw"), errors="coerce").fillna(0.0)

    res = res_portfolios.copy()
    res.columns = [str(c).strip().lower() for c in res.columns]
    res_ids = set(res["res_id"].astype(str))

    used = d[(d["category"] == "res_used") & (d["resource"].astype(str).isin(res_ids))].copy()
    used = used.rename(columns={"resource": "res_id", "dispatch_mw": "generation_mw"})

    curt = d[d["category"] == "curtailment"].copy()
    curt["res_id"] = curt["resource"].astype(str).str.removesuffix(CURTAILMENT_SUFFIX)
    curt = curt[curt["res_id"].isin(res_ids)]
    curt = curt.rename(columns={"dispatch_mw": "curtailment_mw"})

    merged = used[["time_id", "res_id", "generation_mw"]].merge(
        curt[["time_id", "res_id", "curtailment_mw"]], on=["time_id", "res_id"], how="outer",
    )
    merged["generation_mw"] = pd.to_numeric(merged["generation_mw"], errors="coerce").fillna(0.0)
    merged["curtailment_mw"] = pd.to_numeric(merged["curtailment_mw"], errors="coerce").fillna(0.0)
    merged["potential_mw"] = merged["generation_mw"] + merged["curtailment_mw"]

    merged = merged.merge(res[["res_id", "technology"]], on="res_id", how="left")
    merged["technology"] = merged["technology"].fillna("unknown")

    hours = rep_hours.copy()
    hours.columns = [str(c).strip().lower() for c in hours.columns]
    keep = [c for c in ["time_id", "rep_day_id", "hour", "season", "day_type", "weight_days"] if c in hours.columns]
    merged = merged.merge(hours[keep], on="time_id", how="left")
    merged["weight_days"] = pd.to_numeric(merged.get("weight_days", 1.0), errors="coerce").fillna(1.0)
    merged["hour"] = pd.to_numeric(merged.get("hour", 0), errors="coerce").fillna(0).astype(int)
    return merged


def _technology_scopes(res_long: pd.DataFrame) -> list[str]:
    return sorted(res_long["technology"].dropna().unique().tolist())


# ---------------------------------------------------------------------------
# Outputs 1-4: annual generation / curtailment / share / effective integration
# ---------------------------------------------------------------------------

def _annual_generation_rows(res_long: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    total = _weighted_sum(res_long, "potential_mw")
    rows.append(_row("annual_res_generation", "TOTAL", "annual_res_generation_mwh", total, "MWh",
                      "Potential RES generation (effectively integrated + curtailed)."))
    for tech, grp in res_long.groupby("technology"):
        rows.append(_row("annual_res_generation", tech, "annual_res_generation_mwh",
                          _weighted_sum(grp, "potential_mw"), "MWh", ""))
    return rows


def _annual_curtailment_rows(res_long: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    total = _weighted_sum(res_long, "curtailment_mw")
    rows.append(_row("annual_res_curtailment", "TOTAL", "annual_res_curtailment_mwh", total, "MWh",
                      "RES energy curtailed by the dispatch/UC model."))
    for tech, grp in res_long.groupby("technology"):
        rows.append(_row("annual_res_curtailment", tech, "annual_res_curtailment_mwh",
                          _weighted_sum(grp, "curtailment_mw"), "MWh", ""))
    return rows


def _curtailment_share_rows(res_long: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    rows.append(_row("curtailment_share", "TOTAL", "curtailment_share_pct",
                      _share_pct(res_long), "%", "Curtailment as a share of potential RES generation."))
    for tech, grp in res_long.groupby("technology"):
        rows.append(_row("curtailment_share", tech, "curtailment_share_pct", _share_pct(grp), "%", ""))
    return rows


def _share_pct(df: pd.DataFrame) -> float:
    potential = _weighted_sum(df, "potential_mw")
    curtailed = _weighted_sum(df, "curtailment_mw")
    return float(curtailed / potential * 100.0) if potential > 0 else 0.0


def _effective_res_rows(res_long: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    total = _weighted_sum(res_long, "generation_mw")
    rows.append(_row("effectively_integrated_res", "TOTAL", "effectively_integrated_res_mwh", total, "MWh",
                      "RES generation actually absorbed by the system (potential minus curtailment)."))
    for tech, grp in res_long.groupby("technology"):
        rows.append(_row("effectively_integrated_res", tech, "effectively_integrated_res_mwh",
                          _weighted_sum(grp, "generation_mw"), "MWh", ""))
    return rows


# ---------------------------------------------------------------------------
# Output 5: uncovered RES integration need vs. NECP target
# ---------------------------------------------------------------------------

def _uncovered_need_rows(
    res_long: pd.DataFrame,
    residual: pd.DataFrame,
    rep_hours: pd.DataFrame,
    necp_res_target_mwh: float | None,
    necp_res_target_share_pct: float | None,
) -> list[dict[str, Any]]:
    effective_mwh = _weighted_sum(res_long, "generation_mw")

    target_mwh: float | None = necp_res_target_mwh
    if target_mwh is None and necp_res_target_share_pct is not None:
        demand_df = _merge_calendar(residual, rep_hours, pd.DataFrame())
        annual_demand_mwh = _weighted_sum(demand_df, "demand_mw")
        target_mwh = annual_demand_mwh * (necp_res_target_share_pct / 100.0)

    if target_mwh is None:
        return [_row("uncovered_res_integration_need", "TOTAL", "uncovered_res_integration_need_mwh",
                      float("nan"), "MWh",
                      "No NECP target-year RES target supplied (necp_res_target_mwh / "
                      "necp_res_target_share_pct); supply one to compute this metric.")]

    uncovered_mwh = max(0.0, target_mwh - effective_mwh)
    uncovered_pct = (uncovered_mwh / target_mwh * 100.0) if target_mwh > 0 else 0.0
    return [
        _row("uncovered_res_integration_need", "TOTAL", "necp_res_target_mwh", target_mwh, "MWh",
             "Target-year RES generation target from the NECP."),
        _row("uncovered_res_integration_need", "TOTAL", "effectively_integrated_res_mwh", effective_mwh, "MWh", ""),
        _row("uncovered_res_integration_need", "TOTAL", "uncovered_res_integration_need_mwh", uncovered_mwh, "MWh",
             "max(0, NECP target - effectively integrated RES)."),
        _row("uncovered_res_integration_need", "TOTAL", "uncovered_res_integration_need_pct_of_target",
             uncovered_pct, "%", ""),
    ]


# ---------------------------------------------------------------------------
# Output 6: seasonal / day-type / hourly curtailment statistics
# ---------------------------------------------------------------------------

def _curtailment_statistics_rows(res_long: pd.DataFrame, curtailment_threshold_pct: float | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scopes = ["TOTAL"] + _technology_scopes(res_long)

    for scope in scopes:
        grp_all = res_long if scope == "TOTAL" else res_long[res_long["technology"] == scope]
        total = _weighted_sum(grp_all, "curtailment_mw")

        if "season" in grp_all.columns:
            for season, grp in grp_all.groupby("season"):
                value = _weighted_sum(grp, "curtailment_mw")
                rows.append(_row("curtailment_statistics", scope, f"season_{season}_curtailment_mwh", value, "MWh",
                                  _share_note(value, total)))
        if "day_type" in grp_all.columns:
            for day_type, grp in grp_all.groupby("day_type"):
                value = _weighted_sum(grp, "curtailment_mw")
                rows.append(_row("curtailment_statistics", scope, f"daytype_{day_type}_curtailment_mwh", value, "MWh",
                                  _share_note(value, total)))
        for hour, grp in grp_all.groupby("hour"):
            value = _weighted_sum(grp, "curtailment_mw")
            rows.append(_row("curtailment_statistics", scope, f"hour_H{int(hour):02d}_curtailment_mwh", value, "MWh",
                              _share_note(value, total)))

        if curtailment_threshold_pct is not None:
            share = _share_pct(grp_all)
            exceeded = share > curtailment_threshold_pct
            rows.append(_row("curtailment_statistics", scope, "curtailment_vs_threshold",
                              share - curtailment_threshold_pct, "pct_points",
                              f"Curtailment share {share:.2f}% vs. threshold {curtailment_threshold_pct:.2f}%: "
                              + ("EXCEEDED" if exceeded else "within limit")))
    return rows


def _share_note(value: float, total: float) -> str:
    if total <= 0:
        return "share_of_total_pct=0.00"
    return f"share_of_total_pct={value / total * 100.0:.2f}"


# ---------------------------------------------------------------------------
# Output 7: P5 / P50 / P95 curtailment across Monte Carlo scenarios
# ---------------------------------------------------------------------------

def mc_curtailment_from_scenarios(
    mc_results: dict[int, dict[str, pd.DataFrame]],
    res_portfolios: pd.DataFrame,
    rep_hours: pd.DataFrame,
) -> list[float]:
    """Annual RES-curtailment total (MWh) per Monte Carlo scenario."""

    totals: list[float] = []
    for scenario_id, result in mc_results.items():
        dispatch = result.get("dispatch")
        if dispatch is None or dispatch.empty:
            continue
        res_long = _res_long(dispatch, res_portfolios, rep_hours)
        totals.append(_weighted_sum(res_long, "curtailment_mw"))
    return totals


def _mc_percentile_rows(mc_curtailment_mwh: list[float] | None) -> list[dict[str, Any]]:
    if not mc_curtailment_mwh:
        return [_row("mc_curtailment_percentiles", "TOTAL", "p50_curtailment_mwh", float("nan"), "MWh",
                      "No Monte Carlo scenario results supplied.")]

    values = np.asarray(mc_curtailment_mwh, dtype=float)
    rows = [
        _row("mc_curtailment_percentiles", "TOTAL", "p5_curtailment_mwh", float(np.percentile(values, 5)), "MWh", ""),
        _row("mc_curtailment_percentiles", "TOTAL", "p50_curtailment_mwh", float(np.percentile(values, 50)), "MWh", ""),
        _row("mc_curtailment_percentiles", "TOTAL", "p95_curtailment_mwh", float(np.percentile(values, 95)), "MWh", ""),
        _row("mc_curtailment_percentiles", "TOTAL", "mean_curtailment_mwh", float(values.mean()), "MWh", ""),
        _row("mc_curtailment_percentiles", "TOTAL", "min_curtailment_mwh", float(values.min()), "MWh", ""),
        _row("mc_curtailment_percentiles", "TOTAL", "max_curtailment_mwh", float(values.max()), "MWh", ""),
        _row("mc_curtailment_percentiles", "TOTAL", "n_scenarios", float(len(values)), "count", ""),
    ]
    return rows


# ---------------------------------------------------------------------------
# Output 8: required additional flexibility, by E/P duration class
# ---------------------------------------------------------------------------

def _ep_ratio_rows(res_long: pd.DataFrame, ep_ratios_h: tuple[float, ...]) -> list[dict[str, Any]]:
    hourly_curtailment = (
        res_long.groupby("time_id")["curtailment_mw"].sum() if "time_id" in res_long.columns else pd.Series(dtype=float)
    )
    if hourly_curtailment.empty or hourly_curtailment.max() <= 0:
        return [_row("required_flexibility_ep_ratio", "TOTAL", "required_power_mw", 0.0, "MW",
                      "No curtailment hours found; no additional flexibility required.")]

    # P95 across curtailment EVENTS (hours with curtailment > 0), not across
    # all hours: curtailment is typically a small fraction of hours, so a
    # P95-of-all-hours figure collapses to zero and understates the severity
    # of the events flexibility would actually need to absorb.
    nonzero = hourly_curtailment[hourly_curtailment > 0]
    required_power_mw = float(np.percentile(nonzero.to_numpy(), 95))
    max_power_mw = float(hourly_curtailment.max())

    rows = [
        _row("required_flexibility_ep_ratio", "TOTAL", "required_power_mw", required_power_mw, "MW",
             "P95 of hourly system-wide RES curtailment, across hours with curtailment > 0: "
             "additional flexible (charging/storage) power capacity needed to absorb most "
             "curtailment events."),
        _row("required_flexibility_ep_ratio", "TOTAL", "peak_curtailment_mw", max_power_mw, "MW",
             "Maximum single-hour RES curtailment, for reference."),
        _row("required_flexibility_ep_ratio", "TOTAL", "curtailment_event_hours", float(len(nonzero)), "hours",
             "Number of hours per year with curtailment > 0."),
    ]
    for duration_h in ep_ratios_h:
        rows.append(_row(
            "required_flexibility_ep_ratio", f"{duration_h:g}h", "required_energy_mwh",
            required_power_mw * duration_h, "MWh",
            f"Energy capacity of a {duration_h:g}h-duration (E/P={duration_h:g}h) flexibility resource "
            f"sized at the required power ({required_power_mw:.1f} MW).",
        ))
    return rows


# ---------------------------------------------------------------------------
# Standalone entry point: read outputs/workbook, write 14_FNA_RES_Integration
# ---------------------------------------------------------------------------

def write_res_integration_to_excel(
    wb: Any,
    dispatch: pd.DataFrame,
    residual: pd.DataFrame,
    res_portfolios: pd.DataFrame,
    rep_hours: pd.DataFrame,
    control: dict[str, Any] | None = None,
    mc_results: dict[int, dict[str, pd.DataFrame]] | None = None,
    sheet_suffix: str = "",
) -> pd.DataFrame:
    """Build the report and write it to ``14_FNA_RES_Integration``. Returns the table."""

    control = control or {}
    necp_target_mwh = _opt_num(control.get("necp_res_target_mwh"))
    necp_target_share = _opt_num(control.get("necp_res_target_share_pct"))
    threshold_pct = _opt_num(control.get("curtailment_threshold_pct"))

    mc_curtailment: list[float] | None = None
    if mc_results:
        mc_curtailment = mc_curtailment_from_scenarios(mc_results, res_portfolios, rep_hours)

    table = build_res_integration(
        dispatch=dispatch,
        residual=residual,
        res_portfolios=res_portfolios,
        rep_hours=rep_hours,
        necp_res_target_mwh=necp_target_mwh,
        necp_res_target_share_pct=necp_target_share,
        curtailment_threshold_pct=threshold_pct,
        mc_curtailment_mwh=mc_curtailment,
    )
    _write_df(wb, f"14_FNA_RES_Integration{sheet_suffix}", table)
    sort_xlwings_sheets(wb)
    log.info("Wrote 14_FNA_RES_Integration%s (%d rows)", sheet_suffix, len(table))
    return table


def _opt_num(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return _num(value)


def main() -> None:
    """CLI entry point: read the latest GAMS outputs + workbook, write the report."""
    from openpyxl import load_workbook

    from config import output_excel_path
    from io_excel import _read_sheet
    from main import _open_output_workbook

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    out_dir = Path(PATHS["out_dir"])
    dispatch = _read_csv_robust(out_dir / "dispatch.csv")
    residual = _read_csv_robust(out_dir / "residual.csv")

    wb_path = PROJECT_ROOT / "excel" / EXCEL_FILENAME
    in_wb = load_workbook(wb_path, read_only=True, data_only=True)
    res_portfolios = _read_sheet(in_wb, "07_RES_Portfolios")
    rep_hours = _read_sheet(in_wb, "02_RepHours")
    control = _control_dict(_read_sheet(in_wb, "01_Control"))

    mc_results: dict[int, dict[str, pd.DataFrame]] = {}
    for scenario_dir in sorted(out_dir.glob("scenario_*")):
        disp_path = scenario_dir / "dispatch.csv"
        if disp_path.exists():
            idx = int(scenario_dir.name.split("_")[-1])
            mc_results[idx] = {"dispatch": _read_csv_robust(disp_path)}

    out_wb = _open_output_workbook()
    write_res_integration_to_excel(
        out_wb, dispatch, residual, res_portfolios, rep_hours, control=control,
        mc_results=mc_results or None,
    )
    out_wb.save(output_excel_path())


if __name__ == "__main__":
    main()
