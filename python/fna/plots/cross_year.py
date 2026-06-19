"""
fna_cross_year.py - Belgium FNA-ED/UC v3
=========================================
Cross-year comparison for the multi-year run (multi_year.py).

For every target year processed by multi_year.py this module collects a
handful of headline FNA indicators - RES curtailment, ramping needs,
short-term needs, energy not served, total system cost and reserve
shortfall - into one wide comparison table (one row per metric, one column
per target year), writes it to ``60_CrossYear_Comparison``, and renders
trend-line charts across years onto ``54_Risk_MC_Charts``.

Tidy-table convention note: unlike the per-year ACER sheets (long
section/scope/key/metric/value format), this comparison is naturally a
small wide table - one row per metric, one column per target year - which
is far easier to read side by side in Excel.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from openpyxl import Workbook  # type: ignore

from fna.io.excel import _delete_pictures, _indicator_value, _num, _write_df, sort_xlwings_sheets

log = logging.getLogger(__name__)

CHART_DPI = 150


def _weighted_sum_by_period(df: pd.DataFrame, value_col: str, rep_hours: pd.DataFrame) -> float:
    """Annual MWh: hourly value (period = time_id) * weight_days."""

    if df is None or df.empty or value_col not in df.columns:
        return 0.0
    if rep_hours is None or rep_hours.empty:
        return float(pd.to_numeric(df[value_col], errors="coerce").fillna(0.0).sum())

    hours = rep_hours.copy()
    hours.columns = [str(c).strip().lower() for c in hours.columns]
    if "time_id" not in hours.columns or "weight_days" not in hours.columns:
        return float(pd.to_numeric(df[value_col], errors="coerce").fillna(0.0).sum())

    merged = df.rename(columns={"period": "time_id"}).merge(
        hours[["time_id", "weight_days"]], on="time_id", how="left",
    )
    merged["weight_days"] = pd.to_numeric(merged["weight_days"], errors="coerce").fillna(1.0)
    merged[value_col] = pd.to_numeric(merged[value_col], errors="coerce").fillna(0.0)
    return float((merged[value_col] * merged["weight_days"]).sum())


def _table_value(table: pd.DataFrame | None, section: str, scope: str, metric: str) -> float:
    if table is None or table.empty:
        return float("nan")
    mask = (
        table["section"].astype(str).eq(section)
        & table["scope"].astype(str).eq(scope)
        & table["metric"].astype(str).eq(metric)
    )
    rows = table.loc[mask]
    if rows.empty:
        return float("nan")
    return _num(rows.iloc[0]["value"])


def collect_year_metrics(
    year: int,
    results: dict[str, Any],
    extra_tables: dict[str, pd.DataFrame] | None = None,
    rep_hours: pd.DataFrame | None = None,
    mc_results: dict[int, dict] | None = None,
) -> dict[str, float]:
    """Reduce one target year's run to the headline metrics used for the
    cross-year trend charts. Missing pieces (e.g. MC not run) yield NaN."""

    extra_tables = extra_tables or {}
    residual = results.get("residual")
    reserve = results.get("reserve")
    indicators = results.get("indicators")

    metrics: dict[str, float] = {}

    res_curtailment = _indicator_value({"indicators": indicators}, "res_curtailment")
    if res_curtailment is None or pd.isna(res_curtailment):
        res_curtailment = _weighted_sum_by_period(residual, "curtailment_mw", rep_hours)
    metrics["res_curtailment_mwh"] = _num(res_curtailment)

    ens = _indicator_value({"indicators": indicators}, "energy_not_served")
    if ens is None or pd.isna(ens):
        ens = _weighted_sum_by_period(residual, "ens_mw", rep_hours)
    metrics["energy_not_served_mwh"] = _num(ens)

    metrics["total_cost_eur"] = _num(_indicator_value({"indicators": indicators}, "total_cost"))

    metrics["reserve_shortfall_up_mwh"] = _weighted_sum_by_period(reserve, "up_shortfall_mw", rep_hours)
    metrics["reserve_shortfall_down_mwh"] = _weighted_sum_by_period(reserve, "down_shortfall_mw", rep_hours)

    ramping = extra_tables.get("ramping")
    metrics["ramping_uncovered_up_mwh"] = _table_value(ramping, "uncovered_ramping_need", "TOTAL", "annual_uncovered_up_mwh")
    metrics["ramping_uncovered_down_mwh"] = _table_value(ramping, "uncovered_ramping_need", "TOTAL", "annual_uncovered_down_mwh")

    shortterm = extra_tables.get("shortterm")
    metrics["shortterm_uncovered_up_mwh"] = _table_value(shortterm, "uncovered_shortterm_need", "TOTAL", "annual_uncovered_up_mwh")
    metrics["shortterm_uncovered_down_mwh"] = _table_value(shortterm, "uncovered_shortterm_need", "TOTAL", "annual_uncovered_down_mwh")

    if mc_results:
        costs = [_indicator_value(r, "total_cost") for r in mc_results.values()]
        costs = [float(c) for c in costs if c is not None and not pd.isna(c)]
        if costs:
            metrics["mc_cost_p5_eur"] = float(np.percentile(costs, 5))
            metrics["mc_cost_p50_eur"] = float(np.percentile(costs, 50))
            metrics["mc_cost_p95_eur"] = float(np.percentile(costs, 95))

    return metrics


# Metric -> (label, unit) used for both the comparison table and chart titles.
_METRIC_INFO: dict[str, tuple[str, str]] = {
    "res_curtailment_mwh": ("RES curtailment", "MWh"),
    "ramping_uncovered_up_mwh": ("Uncovered upward ramping need", "MWh"),
    "ramping_uncovered_down_mwh": ("Uncovered downward ramping need", "MWh"),
    "shortterm_uncovered_up_mwh": ("Uncovered upward short-term need", "MWh"),
    "shortterm_uncovered_down_mwh": ("Uncovered downward short-term need", "MWh"),
    "energy_not_served_mwh": ("Energy not served (ENS)", "MWh"),
    "total_cost_eur": ("Total system cost", "EUR"),
    "reserve_shortfall_up_mwh": ("Reserve shortfall, upward", "MWh"),
    "reserve_shortfall_down_mwh": ("Reserve shortfall, downward", "MWh"),
    "mc_cost_p5_eur": ("MC total cost P5", "EUR"),
    "mc_cost_p50_eur": ("MC total cost P50", "EUR"),
    "mc_cost_p95_eur": ("MC total cost P95", "EUR"),
}

# Trend-chart groups requested by the multi-year workflow: each entry becomes
# one chart with one line per metric in the group.
_CHART_GROUPS: list[tuple[str, list[str]]] = [
    ("RES curtailment", ["res_curtailment_mwh"]),
    ("Ramping needs (uncovered)", ["ramping_uncovered_up_mwh", "ramping_uncovered_down_mwh"]),
    ("Short-term needs (uncovered)", ["shortterm_uncovered_up_mwh", "shortterm_uncovered_down_mwh"]),
    ("Energy not served", ["energy_not_served_mwh"]),
    ("Total system cost", ["total_cost_eur", "mc_cost_p5_eur", "mc_cost_p50_eur", "mc_cost_p95_eur"]),
    ("Reserve shortfall", ["reserve_shortfall_up_mwh", "reserve_shortfall_down_mwh"]),
]


def build_cross_year_comparison(year_metrics: dict[int, dict[str, float]]) -> pd.DataFrame:
    """Wide table: one row per metric, one column per target year."""

    years = sorted(year_metrics)
    all_metric_keys = [k for k in _METRIC_INFO if any(k in year_metrics[y] for y in years)]

    rows = []
    for key in all_metric_keys:
        label, unit = _METRIC_INFO[key]
        row: dict[str, Any] = {"metric": label, "unit": unit}
        for year in years:
            row[str(year)] = year_metrics[year].get(key, float("nan"))
        rows.append(row)
    return pd.DataFrame(rows)


def _plot_trend(title: str, unit: str, series: dict[str, dict[int, float]], out_path: Path) -> Path | None:
    series = {name: vals for name, vals in series.items() if any(not pd.isna(v) for v in vals.values())}
    if not series:
        return None

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    for name, vals in series.items():
        years = sorted(vals)
        ax.plot(years, [vals[y] for y in years], marker="o", label=name)

    ax.set_title(title)
    ax.set_xlabel("Target year")
    ax.set_ylabel(unit)
    ax.set_xticks(sorted({y for vals in series.values() for y in vals}))
    if len(series) > 1:
        ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=CHART_DPI)
    plt.close(fig)
    return out_path


def generate_trend_charts(year_metrics: dict[int, dict[str, float]], img_dir: Path) -> dict[str, Path]:
    """Render one trend-line PNG per chart group. Returns {title: path}."""

    chart_paths: dict[str, Path] = {}
    for title, metric_keys in _CHART_GROUPS:
        series: dict[str, dict[int, float]] = {}
        unit = ""
        for key in metric_keys:
            label, unit = _METRIC_INFO[key]
            series[label] = {year: year_metrics[year].get(key, float("nan")) for year in year_metrics}

        slug = title.lower().replace(" ", "_").replace("(", "").replace(")", "")
        out_path = img_dir / f"cross_year_{slug}.png"
        path = _plot_trend(title, unit, series, out_path)
        if path is not None:
            chart_paths[title] = path
    return chart_paths


def write_cross_year_to_excel(
    wb: Workbook,
    year_metrics: dict[int, dict[str, float]],
    img_dir: Path,
) -> pd.DataFrame:
    """Write the cross-year comparison table (sheet 60) and trend charts (sheet 61)."""

    table = build_cross_year_comparison(year_metrics)
    if table.empty:
        log.warning("No cross-year metrics to write.")
        return table

    _write_df(wb, "60_CrossYear_Comparison", table)

    from fna.io.excel import RISK_CHART_SHEET, _add_image, _set_cell, _ws_get_existing_or_create

    chart_paths = generate_trend_charts(year_metrics, img_dir)
    legacy_prefix = "61_CrossYear_Charts"[:24]
    for sheet_name in list(wb.sheetnames):
        if sheet_name.startswith(legacy_prefix):
            del wb[sheet_name]
    sht = _ws_get_existing_or_create(wb, RISK_CHART_SHEET)

    if not sht["A1"].value:
        _set_cell(sht, "A1", "Monte Carlo And Cross-Year Risk Charts", bold=True, size=16)
    existing_images = len(getattr(sht, "_images", []) or [])
    start_row = 4 if existing_images == 0 else 5 + ((existing_images + 1) // 2) * 38 + 4
    _set_cell(sht, f"A{start_row}", "Cross-Year FNA Trend Charts", bold=True, size=14)
    _set_cell(sht, f"A{start_row + 1}", f"Target years: {', '.join(str(y) for y in sorted(year_metrics))}", italic=True)

    row_stride = 22
    col_positions = ["A", "K"]
    for index, (title, img) in enumerate(chart_paths.items()):
        row = start_row + 3 + (index // len(col_positions)) * row_stride
        column = col_positions[index % len(col_positions)]
        _set_cell(sht, f"{column}{row - 1}", title)
        try:
            _add_image(sht, img, f"{column}{row}", width=480)
        except Exception as exc:
            log.warning("Could not insert %s into Excel: %s", img, exc)

    sort_xlwings_sheets(wb)
    log.info("Wrote cross-year comparison (sheet 60) and %d trend charts (%s)", len(chart_paths), RISK_CHART_SHEET)
    return table
