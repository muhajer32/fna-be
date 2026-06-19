"""
fna_charts.py - Belgium FNA-ED/UC v3
=====================================
Chart layer for the ACER FNA indicator tables produced by
fna_res_integration.py (sheet 14, Art. 8), fna_ramping.py (sheet 15, Art. 9)
and fna_shortterm.py (sheet 16, Art. 10).

Each ``build_*`` function in those modules returns a tidy long-format table
with columns ``section, scope, key, metric, value, unit, notes``. This module
reads those tables and renders a handful of industry-standard summary charts
(grouped bars, heatmaps, percentile/duration curves) as PNGs, then inserts
them into the consolidated "40_Deterministic_Charts" sheet next to the data sheets.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from fna.io.excel import _delete_pictures, sort_xlwings_sheets
from fna.plots.base import COL, _save, _style

log = logging.getLogger(__name__)

CHART_DPI = 150
SEASON_ORDER = ["winter", "spring", "summer", "autumn", "fall"]


# ------------------------------------------------------------------------------
# Tidy-table helpers
# ------------------------------------------------------------------------------
def _section(table: pd.DataFrame, section: str) -> pd.DataFrame:
    return table[table["section"].astype(str) == section]


def _value(table: pd.DataFrame, section: str, scope: str, metric: str, default: float = float("nan")) -> float:
    rows = table[
        table["section"].astype(str).eq(section)
        & table["scope"].astype(str).eq(scope)
        & table["metric"].astype(str).eq(metric)
    ]
    if rows.empty:
        return default
    return float(pd.to_numeric(rows.iloc[0]["value"], errors="coerce"))


def _sort_seasons(seasons: list[str]) -> list[str]:
    known = [s for s in SEASON_ORDER if s in seasons]
    other = sorted(s for s in seasons if s not in SEASON_ORDER)
    return known + other


# ------------------------------------------------------------------------------
# 14_FNA_RES_Integration charts
# ------------------------------------------------------------------------------
def _plot_res_generation_vs_curtailment(table: pd.DataFrame, img_dir: Path) -> Path | None:
    gen = _section(table, "annual_res_generation")
    curt = _section(table, "annual_res_curtailment")
    techs = sorted(set(gen.loc[gen["scope"] != "TOTAL", "scope"]) | set(curt.loc[curt["scope"] != "TOTAL", "scope"]))
    if not techs:
        return None

    gen_vals = [_value(table, "annual_res_generation", t, "annual_res_generation_mwh", 0.0) for t in techs]
    curt_vals = [_value(table, "annual_res_curtailment", t, "annual_res_curtailment_mwh", 0.0) for t in techs]

    x = np.arange(len(techs))
    width = 0.38
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x - width / 2, gen_vals, width, label="RES generation", color=COL["res_used"])
    ax.bar(x + width / 2, curt_vals, width, label="RES curtailment", color=COL["curtailment"])
    ax.set_xticks(x)
    ax.set_xticklabels([t.replace("_", " ").title() for t in techs], rotation=20, ha="right")
    _style(ax, "Annual RES Generation vs. Curtailment by Technology", "MWh", "Technology")
    ax.legend(frameon=False)
    return _save(fig, img_dir / "fna14_01_generation_vs_curtailment.png", dpi=CHART_DPI)


def _plot_curtailment_share(table: pd.DataFrame, img_dir: Path) -> Path | None:
    rows = _section(table, "curtailment_share")
    rows = rows[rows["scope"] != "TOTAL"]
    if rows.empty:
        return None

    techs = rows["scope"].tolist()
    vals = pd.to_numeric(rows["value"], errors="coerce").values
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar([t.replace("_", " ").title() for t in techs], vals, color=COL["curtailment"])
    _style(ax, "RES Curtailment Share by Technology", "%", "Technology")
    ax.tick_params(axis="x", rotation=20)
    return _save(fig, img_dir / "fna14_02_curtailment_share.png", dpi=CHART_DPI)


def _plot_curtailment_profile(table: pd.DataFrame, img_dir: Path) -> Path | None:
    rows = _section(table, "curtailment_statistics")
    rows = rows[rows["scope"] == "TOTAL"]
    season_rows = rows[rows["metric"].astype(str).str.match(r"^season_.*_curtailment_mwh$")]
    hour_rows = rows[rows["metric"].astype(str).str.match(r"^hour_H\d+_curtailment_mwh$")]
    if season_rows.empty and hour_rows.empty:
        return None

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))

    if not season_rows.empty:
        labels = [re.match(r"^season_(.*)_curtailment_mwh$", m).group(1) for m in season_rows["metric"]]
        vals = pd.to_numeric(season_rows["value"], errors="coerce").values
        order = _sort_seasons(labels)
        ordered_vals = [vals[labels.index(s)] for s in order]
        axes[0].bar(order, ordered_vals, color=COL["curtailment"])
        _style(axes[0], "Curtailment by Season", "MWh", "Season")
    else:
        axes[0].axis("off")

    if not hour_rows.empty:
        hours = [int(re.match(r"^hour_H(\d+)_curtailment_mwh$", m).group(1)) for m in hour_rows["metric"]]
        vals = pd.to_numeric(hour_rows["value"], errors="coerce").values
        order = np.argsort(hours)
        axes[1].plot(np.array(hours)[order], vals[order], marker="o", color=COL["curtailment"])
        _style(axes[1], "Curtailment by Hour of Day", "MWh", "Hour")
    else:
        axes[1].axis("off")

    fig.suptitle("RES Curtailment Profile", fontsize=14, fontweight="bold")
    return _save(fig, img_dir / "fna14_03_curtailment_profile.png", dpi=CHART_DPI)


def _plot_necp_target(table: pd.DataFrame, img_dir: Path) -> Path | None:
    target = _value(table, "uncovered_res_integration_need", "TOTAL", "necp_res_target_mwh")
    if pd.isna(target):
        return None
    effective = _value(table, "uncovered_res_integration_need", "TOTAL", "effectively_integrated_res_mwh")
    uncovered = _value(table, "uncovered_res_integration_need", "TOTAL", "uncovered_res_integration_need_mwh")

    labels = ["NECP target", "Effectively integrated", "Uncovered need"]
    vals = [target, effective, uncovered]
    colors = [COL["residual"], COL["res_used"], COL["curtailment"]]
    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    ax.bar(labels, vals, color=colors)
    _style(ax, "RES Integration vs. NECP Target", "MWh", "")
    return _save(fig, img_dir / "fna14_04_necp_target.png", dpi=CHART_DPI)


def _plot_mc_curtailment_percentiles(table: pd.DataFrame, img_dir: Path) -> Path | None:
    p5 = _value(table, "mc_curtailment_percentiles", "TOTAL", "p5_curtailment_mwh")
    if pd.isna(p5):
        return None
    p50 = _value(table, "mc_curtailment_percentiles", "TOTAL", "p50_curtailment_mwh")
    p95 = _value(table, "mc_curtailment_percentiles", "TOTAL", "p95_curtailment_mwh")
    mean = _value(table, "mc_curtailment_percentiles", "TOTAL", "mean_curtailment_mwh")

    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    ax.bar(["P5", "Mean", "P50", "P95"], [p5, mean, p50, p95], color=COL["curtailment"])
    _style(ax, "Monte Carlo RES Curtailment Percentiles", "MWh", "")
    return _save(fig, img_dir / "fna14_05_mc_curtailment_percentiles.png", dpi=CHART_DPI)


def plot_res_integration_charts(table: pd.DataFrame, img_dir: Path) -> dict[str, Path]:
    if table is None or table.empty:
        return {}
    charts: dict[str, Path] = {}
    for key, fn in [
        ("generation_vs_curtailment", _plot_res_generation_vs_curtailment),
        ("curtailment_share", _plot_curtailment_share),
        ("curtailment_profile", _plot_curtailment_profile),
        ("necp_target", _plot_necp_target),
        ("mc_curtailment_percentiles", _plot_mc_curtailment_percentiles),
    ]:
        try:
            path = fn(table, img_dir)
        except Exception as exc:
            log.warning("RES integration chart '%s' failed: %s", key, exc)
            path = None
        if path is not None:
            charts[key] = path
    return charts


# ------------------------------------------------------------------------------
# 15_FNA_Ramping charts
# ------------------------------------------------------------------------------
def _plot_ramping_heatmaps(table: pd.DataFrame, img_dir: Path) -> Path | None:
    rows = _section(table, "heatmap_hour_season")
    if rows.empty:
        return None

    parsed = rows["scope"].astype(str).str.extract(r"^(?P<season>.+)_H(?P<hour>\d+)$")
    df = rows.copy()
    df["season"] = parsed["season"]
    df["hour"] = pd.to_numeric(parsed["hour"], errors="coerce")
    df = df.dropna(subset=["season", "hour"])
    if df.empty:
        return None
    df["hour"] = df["hour"].astype(int)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    panels = [
        ("mean_uncovered_up_mw", "Uncovered Upward Ramping Need (mean MW)"),
        ("mean_uncovered_down_mw", "Uncovered Downward Ramping Need (mean MW)"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    plotted = False
    for ax, (metric, title) in zip(axes, panels):
        sub = df[df["metric"] == metric]
        if sub.empty:
            ax.axis("off")
            continue
        pivot = sub.pivot(index="season", columns="hour", values="value")
        pivot = pivot.reindex(_sort_seasons(list(pivot.index)))
        im = ax.imshow(pivot, aspect="auto", cmap="YlOrRd")
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels(pivot.columns, fontsize=8)
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels(pivot.index)
        ax.set_xlabel("Hour of day")
        ax.set_title(title, fontsize=11, fontweight="bold")
        plt.colorbar(im, ax=ax, label="MW")
        plotted = True
    if not plotted:
        return None
    fig.suptitle("Uncovered Ramping Need by Season and Hour of Day", fontsize=14, fontweight="bold")
    return _save(fig, img_dir / "fna15_01_ramping_heatmap.png", dpi=CHART_DPI)


def _plot_ramp_distribution(table: pd.DataFrame, img_dir: Path) -> Path | None:
    rows = _section(table, "ramp_distribution")
    rows = rows[rows["scope"] == "TOTAL"]
    if rows.empty:
        return None

    percentiles = [5, 25, 50, 75, 95]
    labels = ["ramp_up", "ramp_down", "uncovered_up", "uncovered_down"]
    colors = [COL["up"], COL["down"], COL["shortfall"], COL["flex_down"]]

    fig, ax = plt.subplots(figsize=(9, 5))
    plotted = False
    for label, color in zip(labels, colors):
        vals = [_value(table, "ramp_distribution", "TOTAL", f"{label}_p{p}_mw") for p in percentiles]
        if all(pd.isna(v) for v in vals):
            continue
        ax.plot(percentiles, vals, marker="o", color=color, label=label.replace("_", " ").title())
        plotted = True
    if not plotted:
        return None
    _style(ax, "Ramp / Uncovered-Ramp Percentile Distribution", "MW", "Percentile")
    ax.legend(frameon=False)
    return _save(fig, img_dir / "fna15_02_ramp_distribution.png", dpi=CHART_DPI)


def _plot_ramp_events(table: pd.DataFrame, img_dir: Path) -> Path | None:
    directions = ["up", "down"]
    mean_dur = [_value(table, "event_duration", "TOTAL", f"mean_event_duration_{d}_h") for d in directions]
    p95_dur = [_value(table, "event_duration", "TOTAL", f"p95_event_duration_{d}_h") for d in directions]
    max_dur = [_value(table, "event_duration", "TOTAL", f"max_event_duration_{d}_h") for d in directions]
    mean_int = [_value(table, "interval_between_events", "TOTAL", f"mean_interval_{d}_h") for d in directions]
    if all(pd.isna(v) for v in mean_dur):
        return None

    x = np.arange(len(directions))
    width = 0.2
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(x - 1.5 * width, mean_dur, width, label="Mean duration (h)")
    ax.bar(x - 0.5 * width, p95_dur, width, label="P95 duration (h)")
    ax.bar(x + 0.5 * width, max_dur, width, label="Max duration (h)")
    ax.bar(x + 1.5 * width, mean_int, width, label="Mean interval between events (h)")
    ax.set_xticks(x)
    ax.set_xticklabels(["Upward", "Downward"])
    _style(ax, "Uncovered Ramping Event Duration & Frequency", "Hours", "")
    ax.legend(frameon=False, fontsize=8)
    return _save(fig, img_dir / "fna15_03_ramp_events.png", dpi=CHART_DPI)


def _plot_uncovered_ramping_need(table: pd.DataFrame, img_dir: Path) -> Path | None:
    rows = _section(table, "uncovered_ramping_need")
    if rows.empty:
        return None
    seasonal = rows[rows["scope"] != "TOTAL"]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    if not seasonal.empty:
        pivot = seasonal.pivot_table(index="scope", columns="metric", values="value", aggfunc="first")
        pivot = pivot.reindex(_sort_seasons(list(pivot.index)))
        cols = [c for c in ["annual_uncovered_up_mwh", "annual_uncovered_down_mwh"] if c in pivot.columns]
        if not cols:
            return None
        colors = [COL["up"], COL["down"]][: len(cols)]
        pivot[cols].plot.bar(ax=ax, color=colors)
        ax.legend(["Upward", "Downward"][: len(cols)], frameon=False)
        _style(ax, "Annual Uncovered Ramping Need by Season", "MWh", "Season")
        ax.tick_params(axis="x", rotation=0)
    else:
        up = _value(table, "uncovered_ramping_need", "TOTAL", "annual_uncovered_up_mwh")
        dn = _value(table, "uncovered_ramping_need", "TOTAL", "annual_uncovered_down_mwh")
        if pd.isna(up) and pd.isna(dn):
            return None
        ax.bar(["Upward", "Downward"], [up, dn], color=[COL["up"], COL["down"]])
        _style(ax, "Annual Uncovered Ramping Need", "MWh", "")
    return _save(fig, img_dir / "fna15_04_uncovered_ramping_need.png", dpi=CHART_DPI)


def plot_ramping_charts(table: pd.DataFrame, img_dir: Path) -> dict[str, Path]:
    if table is None or table.empty:
        return {}
    charts: dict[str, Path] = {}
    for key, fn in [
        ("ramping_heatmap", _plot_ramping_heatmaps),
        ("ramp_distribution", _plot_ramp_distribution),
        ("ramp_events", _plot_ramp_events),
        ("uncovered_ramping_need", _plot_uncovered_ramping_need),
    ]:
        try:
            path = fn(table, img_dir)
        except Exception as exc:
            log.warning("Ramping chart '%s' failed: %s", key, exc)
            path = None
        if path is not None:
            charts[key] = path
    return charts


# ------------------------------------------------------------------------------
# 16_FNA_ShortTerm charts
# ------------------------------------------------------------------------------
def _plot_shortterm_need(table: pd.DataFrame, img_dir: Path) -> Path | None:
    up = _value(table, "shortterm_need", "TOTAL", "upward_need_mw")
    dn = _value(table, "shortterm_need", "TOTAL", "downward_need_mw")
    if pd.isna(up) and pd.isna(dn):
        return None
    fig, ax = plt.subplots(figsize=(6, 4.5))
    ax.bar(["Upward", "Downward"], [up, dn], color=[COL["up"], COL["down"]])
    _style(ax, "Short-Term Flexibility Need", "MW", "")
    return _save(fig, img_dir / "fna16_01_shortterm_need.png", dpi=CHART_DPI)


def _plot_shortterm_need_by_speed(table: pd.DataFrame, img_dir: Path) -> Path | None:
    rows = _section(table, "shortterm_need_by_speed")
    if rows.empty:
        return None
    bands = [b for b in ["very_fast", "fast", "slow"] if b in set(rows["scope"])]
    if not bands:
        return None

    up = [_value(table, "shortterm_need_by_speed", b, "upward_need_mw", 0.0) for b in bands]
    dn = [_value(table, "shortterm_need_by_speed", b, "downward_need_mw", 0.0) for b in bands]

    x = np.arange(len(bands))
    width = 0.35
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.bar(x - width / 2, up, width, label="Upward", color=COL["up"])
    ax.bar(x + width / 2, dn, width, label="Downward", color=COL["down"])
    ax.set_xticks(x)
    ax.set_xticklabels([b.replace("_", " ").title() for b in bands])
    _style(ax, "Short-Term Need by Response Speed Band", "MW", "Speed band")
    ax.legend(frameon=False)
    return _save(fig, img_dir / "fna16_02_shortterm_need_by_speed.png", dpi=CHART_DPI)


def _plot_uncovered_shortterm_by_hour(table: pd.DataFrame, img_dir: Path) -> Path | None:
    rows = _section(table, "uncovered_shortterm_need")
    rows = rows[rows["scope"].astype(str).str.match(r"^hour_H\d+$")]
    if rows.empty:
        return None
    df = rows.copy()
    df["hour"] = df["scope"].astype(str).str.extract(r"^hour_H(\d+)$").astype(int)
    pivot = df.pivot_table(index="hour", columns="metric", values="value", aggfunc="first").sort_index()

    fig, ax = plt.subplots(figsize=(9, 4.5))
    plotted = False
    for col, color, label in [
        ("mean_uncovered_up_mw", COL["up"], "Upward"),
        ("mean_uncovered_down_mw", COL["down"], "Downward"),
    ]:
        if col in pivot.columns:
            ax.plot(pivot.index, pd.to_numeric(pivot[col], errors="coerce"), marker="o", color=color, label=label)
            plotted = True
    if not plotted:
        return None
    _style(ax, "Mean Uncovered Short-Term Need by Hour of Day", "MW", "Hour")
    ax.legend(frameon=False)
    return _save(fig, img_dir / "fna16_03_uncovered_by_hour.png", dpi=CHART_DPI)


def _plot_uncovered_shortterm_by_season(table: pd.DataFrame, img_dir: Path) -> Path | None:
    rows = _section(table, "uncovered_shortterm_need")
    rows = rows[(rows["scope"] != "TOTAL") & ~rows["scope"].astype(str).str.match(r"^hour_H\d+$")]
    if rows.empty:
        return None
    pivot = rows.pivot_table(index="scope", columns="metric", values="value", aggfunc="first")
    pivot = pivot.reindex(_sort_seasons(list(pivot.index)))
    cols = [c for c in ["annual_uncovered_up_mwh", "annual_uncovered_down_mwh"] if c in pivot.columns]
    if not cols:
        return None

    fig, ax = plt.subplots(figsize=(8, 4.5))
    colors = [COL["up"], COL["down"]][: len(cols)]
    pivot[cols].plot.bar(ax=ax, color=colors)
    ax.legend(["Upward", "Downward"][: len(cols)], frameon=False)
    _style(ax, "Annual Uncovered Short-Term Need by Season", "MWh", "Season")
    ax.tick_params(axis="x", rotation=0)
    return _save(fig, img_dir / "fna16_04_uncovered_by_season.png", dpi=CHART_DPI)


def _plot_uncovered_probability(table: pd.DataFrame, img_dir: Path) -> Path | None:
    rows = _section(table, "uncovered_probability")
    rows = rows[rows["scope"] == "TOTAL"]
    if rows.empty:
        return None

    percentiles = [50, 75, 90, 95, 99]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    plotted = False
    for direction, color in [("up", COL["up"]), ("down", COL["down"])]:
        vals = [_value(table, "uncovered_probability", "TOTAL", f"{direction}_p{p}_mw") for p in percentiles]
        if all(pd.isna(v) for v in vals):
            continue
        ax.plot(percentiles, vals, marker="o", color=color, label=direction.title())
        plotted = True
    if not plotted:
        return None
    _style(ax, "Uncovered Short-Term Need: Exceedance Percentiles", "MW", "Percentile")
    ax.legend(frameon=False)
    return _save(fig, img_dir / "fna16_05_uncovered_probability.png", dpi=CHART_DPI)


def _plot_outage_stress_margin(table: pd.DataFrame, img_dir: Path) -> Path | None:
    need_up = _value(table, "available_margin_comparison", "TOTAL", "upward_need_mw")
    avail_up = _value(table, "available_margin_comparison", "TOTAL", "mean_available_up_mw")
    need_dn = _value(table, "available_margin_comparison", "TOTAL", "downward_need_mw")
    avail_dn = _value(table, "available_margin_comparison", "TOTAL", "mean_available_down_mw")
    if all(pd.isna(v) for v in [need_up, avail_up, need_dn, avail_dn]):
        return None

    labels = ["Upward need", "Upward available", "Downward need", "Downward available"]
    vals = [need_up, avail_up, need_dn, avail_dn]
    colors = [COL["shortfall"], COL["up"], COL["shortfall"], COL["down"]]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(labels, vals, color=colors)
    _style(ax, "Short-Term Need vs. Available Margin", "MW", "")
    ax.tick_params(axis="x", rotation=15)
    return _save(fig, img_dir / "fna16_06_need_vs_margin.png", dpi=CHART_DPI)


def plot_shortterm_charts(table: pd.DataFrame, img_dir: Path) -> dict[str, Path]:
    if table is None or table.empty:
        return {}
    charts: dict[str, Path] = {}
    for key, fn in [
        ("shortterm_need", _plot_shortterm_need),
        ("shortterm_need_by_speed", _plot_shortterm_need_by_speed),
        ("uncovered_by_hour", _plot_uncovered_shortterm_by_hour),
        ("uncovered_by_season", _plot_uncovered_shortterm_by_season),
        ("uncovered_probability", _plot_uncovered_probability),
        ("need_vs_margin", _plot_outage_stress_margin),
    ]:
        try:
            path = fn(table, img_dir)
        except Exception as exc:
            log.warning("Short-term chart '%s' failed: %s", key, exc)
            path = None
        if path is not None:
            charts[key] = path
    return charts


# ------------------------------------------------------------------------------
# Excel integration
# ------------------------------------------------------------------------------
def generate_fna_indicator_charts(
    extra_tables: dict[str, pd.DataFrame],
    img_dir: Path,
    wb: Any | None = None,
    sheet_suffix: str = "",
) -> dict[str, Path]:
    """Render charts for the RES integration / ramping / short-term tidy
    tables and insert them into ``40_Deterministic_Charts<suffix>``."""

    img_dir = Path(img_dir)
    img_dir.mkdir(parents=True, exist_ok=True)
    for old in img_dir.glob("fna1?_*.png"):
        try:
            old.unlink()
        except OSError:
            pass

    extra_tables = extra_tables or {}
    sections: list[tuple[str, dict[str, Path]]] = [
        ("RES Integration (Sheet 14, Art. 8)", plot_res_integration_charts(extra_tables.get("res_integration"), img_dir)),
        ("Ramping Needs (Sheet 15, Art. 9)", plot_ramping_charts(extra_tables.get("ramping"), img_dir)),
        ("Short-Term Flexibility Needs (Sheet 16, Art. 10)", plot_shortterm_charts(extra_tables.get("shortterm"), img_dir)),
    ]

    chart_paths: dict[str, Path] = {}
    for _, charts in sections:
        chart_paths.update(charts)
    if not chart_paths:
        return chart_paths

    if wb is not None:
        from fna.io.excel import DETERMINISTIC_CHART_SHEET, _add_image, _reset_chart_sheet, _set_cell

        sheet_name = f"{DETERMINISTIC_CHART_SHEET}{sheet_suffix}"
        sht = _reset_chart_sheet(wb, sheet_name)

        _set_cell(sht, "A1", "Deterministic And FNA Indicator Charts", bold=True, size=16)
        _set_cell(sht, "A2", "Sheet groups: FNA indicators first; dispatch, price, reserve and commitment charts below after the deterministic run charts are generated.", italic=True)

        row = 4
        for title, charts in sections:
            if not charts:
                continue
            _set_cell(sht, f"A{row}", title, bold=True)
            row += 1
            chart_row = row
            col_positions = ["A", "K"]
            for index, (key, img) in enumerate(charts.items()):
                column = col_positions[index % len(col_positions)]
                anchor_row = chart_row + (index // len(col_positions)) * 22
                try:
                    _add_image(sht, img, f"{column}{anchor_row}", width=480)
                except Exception as exc:
                    log.warning("Could not insert %s into Excel: %s", img, exc)
            n_rows = (len(charts) + len(col_positions) - 1) // len(col_positions)
            row = chart_row + n_rows * 22 + 1

        sort_xlwings_sheets(wb)

    log.info("Generated %d FNA indicator charts (%s%s)", len(chart_paths), DETERMINISTIC_CHART_SHEET, sheet_suffix)
    return chart_paths
