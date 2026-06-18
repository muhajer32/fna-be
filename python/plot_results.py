"""
plot_results.py - Belgium FNA-ED/UC v2
=======================================
Creates professional FNA dashboards and user-friendly output charts.

Charts created (v2)
-------------------
1. Generation mix by representative hour (stacked bar)
2. Electricity price diagnostic curve
3. Residual load duration curve (RLDC) with flexibility zones
4. Upward and downward flexibility needs (bar summary)
5. Reserve stack with shortfall highlight (improved)
6. Storage/flex state of charge (stacked area)
7. Ramp probability matrix (2D histogram of residual load ramps)
8. Gantt chart of unit commitment (only thermal, first 4 days)
9. Sankey diagram of annual energy flows (weighted)
10. Stacked hourly dispatch with flexibility activation + price curve (twin‑axis)

All charts are saved as PNG and placed into the Excel sheet `40_Charts`.
"""
from __future__ import annotations
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D

# Optional: for Sankey (requires plotly)
HAS_PLOTLY = False
go = None
try:
    import importlib
    go = importlib.import_module("plotly.graph_objects")
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False
    print("Plotly not installed. Sankey diagram will be skipped.")

log = logging.getLogger(__name__)

# Professional color palette (colorblind-friendly)
COL = {
    "nuclear": "#7C3AED",
    "ccgt": "#EF4444",
    "ccgt_new": "#F97316",
    "ocgt": "#B91C1C",
    "chp": "#92400E",
    "biomass_waste": "#65A30D",
    "hydro": "#2563EB",
    "res_used": "#22C55E",
    "curtailment": "#DC2626",
    "flex_up": "#14B8A6",
    "flex_down": "#0EA5E9",
    "import": "#38BDF8",
    "export": "#FB7185",
    "ens": "#111827",
    "demand": "#111827",
    "residual": "#4F46E5",
    "price": "#D97706",
    "up": "#2563EB",
    "down": "#059669",
    "shortfall": "#DC2626",
    "upward_flex_cap": "#60A5FA",
    "downward_flex_cap": "#34D399",
}

# ------------------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------------------
def generate_all(results: dict[str, Any], img_dir: Path, wb: Any | None = None) -> dict[str, Path]:
    """
    Generate all v2 charts from GAMS results.
    """
    img_dir = Path(img_dir)
    img_dir.mkdir(parents=True, exist_ok=True)
    # Clean previous charts
    for old in img_dir.glob("v2_*.png"):
        try:
            old.unlink()
        except OSError:
            pass

    data = _normalize_results(results)

    chart_paths = {
        "stacked_dispatch_price": _plot_stacked_dispatch_with_price(data, img_dir / "v2_10_stacked_dispatch_price.png"),
        "generation_mix": _plot_generation_mix(data, img_dir / "v2_01_generation_mix.png"),
        "price_curve": _plot_price_curve(data, img_dir / "v2_02_price_curve.png"),
        "rldc": _plot_rldc(data, img_dir / "v2_03_rldc.png"),
        "flex_needs": _plot_flex_needs(data, img_dir / "v2_04_flex_needs.png"),
        "reserve_margin": _plot_reserve_margin(data, img_dir / "v2_05_reserve_margin.png"),
        "storage_soc": _plot_storage_soc_stacked(data, img_dir / "v2_06_storage_soc.png"),
        "ramp_matrix": _plot_ramp_matrix(data, img_dir / "v2_07_ramp_matrix.png"),
        "gantt": _plot_gantt_thermal(data, img_dir / "v2_08_gantt.png"),
        "sankey": _plot_sankey(data, img_dir / "v2_09_sankey.png"),
        
    }
    if wb is not None:
        write_charts_sheet(wb, data, chart_paths)
    log.info("Generated v2 FNA chart sheet with %d charts", len(chart_paths))
    return chart_paths


def generate_mc_summary_charts(
    mc_results: dict[int, dict[str, Any]],
    uncertainty_scenarios: dict[int, pd.DataFrame],
    n_scenarios: int,
    max_workers: int,
    img_dir: Path,
) -> dict[str, Path]:
    """Generate compact Monte Carlo charts from aggregated scenario results."""

    img_dir = Path(img_dir)
    img_dir.mkdir(parents=True, exist_ok=True)

    for old in img_dir.glob("mc_*.png"):
        try:
            old.unlink()
        except OSError:
            pass

    charts: dict[str, Path] = {}
    costs = []
    residual_rows = []
    price_rows = []
    load_rows = []
    solar_rows = []
    wind_rows = []
    solar_id_pattern = re.compile(r"(?:solar|pv)", re.IGNORECASE)
    wind_id_pattern = re.compile(r"(?:wind|onshore|offshore)", re.IGNORECASE)
    for scenario_id, result in sorted(mc_results.items()):
        cost = _indicator_value(result, "total_cost")
        if cost is not None and not pd.isna(cost):
            costs.append(float(cost))
        residual = result.get("residual")
        if isinstance(residual, pd.DataFrame) and "residual_load_mw" in residual.columns:
            r = residual.copy()
            r["scenario_id"] = scenario_id
            residual_rows.append(r)
        price = result.get("price")
        if isinstance(price, pd.DataFrame) and "price_eur_mwh" in price.columns:
            p = price.copy()
            p["scenario_id"] = scenario_id
            price_rows.append(p)

    for scenario_id, scenario in sorted(uncertainty_scenarios.items()):
        if not isinstance(scenario, pd.DataFrame):
            continue
        demand_col = f"demand_multiplier_scenario_{scenario_id}"
        cf_col = f"cf_scenario_{scenario_id}"
        if demand_col in scenario.columns:
            demand = scenario.loc[scenario[demand_col].notna(), ["time_id", demand_col]].copy()
            if not demand.empty:
                demand = demand.rename(columns={demand_col: "demand_multiplier"})
                demand["scenario_id"] = scenario_id
                load_rows.append(demand)
        if cf_col in scenario.columns and "res_id" in scenario.columns:
            is_solar = scenario["res_id"].astype(str).str.contains(solar_id_pattern, na=False)
            solar = scenario.loc[is_solar & scenario[cf_col].notna(), ["time_id", "res_id", cf_col]].copy()
            if not solar.empty:
                solar = solar.rename(columns={cf_col: "capacity_factor"})
                solar["scenario_id"] = scenario_id
                solar_rows.append(solar)

            is_wind = scenario["res_id"].astype(str).str.contains(wind_id_pattern, na=False)
            wind = scenario.loc[is_wind & scenario[cf_col].notna(), ["time_id", "res_id", cf_col]].copy()
            if not wind.empty:
                wind = wind.rename(columns={cf_col: "capacity_factor"})
                wind["scenario_id"] = scenario_id
                wind_rows.append(wind)

    if costs:
        charts["mc_cost_distribution"] = _plot_mc_cost_distribution(costs, img_dir / "mc_01_cost_distribution.png")
    if load_rows:
        charts["mc_load_input_bands"] = _plot_mc_bands(
            pd.concat(load_rows, ignore_index=True),
            "demand_multiplier",
            "PECD Load Scenario Multipliers",
            "Multiplier",
            img_dir / "mc_02_load_input_bands.png",
            period_col="time_id",
        )
    if solar_rows:
        charts["mc_solar_input_bands"] = _plot_mc_bands(
            pd.concat(solar_rows, ignore_index=True),
            "capacity_factor",
            "PECD Solar Capacity Factor Scenarios",
            "Capacity factor",
            img_dir / "mc_03_solar_input_bands.png",
            period_col="time_id",
        )
    if wind_rows:
        charts["mc_wind_input_bands"] = _plot_mc_bands(
            pd.concat(wind_rows, ignore_index=True),
            "capacity_factor",
            "PECD Wind Capacity Factor Scenarios",
            "Capacity factor",
            img_dir / "mc_03b_wind_input_bands.png",
            period_col="time_id",
        )
    if residual_rows:
        charts["mc_residual_bands"] = _plot_mc_bands(
            pd.concat(residual_rows, ignore_index=True),
            "residual_load_mw",
            "Residual Load Uncertainty",
            "MW",
            img_dir / "mc_04_residual_load_bands.png",
        )
    if price_rows:
        charts["mc_price_bands"] = _plot_mc_bands(
            pd.concat(price_rows, ignore_index=True),
            "price_eur_mwh",
            "Price Uncertainty",
            "EUR/MWh",
            img_dir / "mc_05_price_bands.png",
        )
    charts["mc_workflow"] = _plot_mc_workflow(
        n_scenarios,
        max_workers,
        img_dir / "mc_06_workflow.png",
        completed_scenarios=len(mc_results),
    )

    try:
        from mc_charts import generate_extra_mc_charts

        price_df = pd.concat(price_rows, ignore_index=True) if price_rows else pd.DataFrame()
        extra_charts = generate_extra_mc_charts(mc_results, uncertainty_scenarios, price_df, img_dir)
        charts.update(extra_charts)
    except Exception as exc:
        log.warning("Could not generate extra Monte Carlo charts: %s", exc)

    return charts


def _normalize_results(results: dict[str, Any]) -> dict[str, pd.DataFrame]:
    out = {}
    for k, df in results.items():
        if isinstance(df, pd.DataFrame):
            temp = df.copy()
            temp.columns = [str(c).strip().replace(" ", "_").replace("-", "_").lower() for c in temp.columns]
            out[k] = temp
    return out


def _time_sort(values):
    def key(x):
        s = str(x)
        digits = "".join(ch for ch in s if ch.isdigit())
        return (int(digits) if digits else 999999, s)
    return sorted(list(values), key=key)


def _style(ax, title, ylabel="", xlabel="Representative hour"):
    ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
    ax.set_ylabel(ylabel, fontsize=12)
    ax.set_xlabel(xlabel, fontsize=12)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=10)


def _tick_step(n: int, target: int = 24) -> int:
    """Tick spacing so roughly `target` labels are shown, regardless of how
    many representative periods (e.g. 168 vs 8760) the run produced."""
    return max(1, n // target)


_PERIOD_RE = re.compile(r"^D(\d+)_H(\d+)$")
_DAY_RE = re.compile(r"^D(\d+)$")


def _day_to_month(day: int) -> str:
    """1-365 day-of-year -> month abbreviation (non-leap calendar)."""
    try:
        return (date(2001, 1, 1) + timedelta(days=int(day) - 1)).strftime("%b")
    except Exception:
        return f"D{day}"


def _set_period_xticks(ax, periods) -> None:
    """Standard x-axis labelling for a categorical period axis.

    - Full-year runs (``D###_H##``, >180 distinct days): one tick per month,
      labelled Jan-Dec.
    - Representative-day runs (``D###_H##`` with <=14 distinct days): one
      tick per representative day, labelled "RD<n>".
    - Anything else: thinned raw period labels (via `_tick_step`).
    """
    periods = [str(p) for p in periods]
    n = len(periods)
    matches = [_PERIOD_RE.match(p) for p in periods]
    if n and all(matches):
        days = [int(m.group(1)) for m in matches]
        n_days = len(set(days))
        if n_days > 180:
            ticks, labels, seen = [], [], set()
            for i, day in enumerate(days):
                month = _day_to_month(day)
                if month not in seen:
                    seen.add(month)
                    ticks.append(i)
                    labels.append(month)
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels, rotation=0, ha="center", fontsize=9)
            return
        if n_days <= 14:
            ticks, labels = [], []
            for day in sorted(set(days)):
                idxs = [i for i, d in enumerate(days) if d == day]
                ticks.append(idxs[len(idxs) // 2])
                labels.append(f"RD{day}")
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels, rotation=0, ha="center", fontsize=9)
            return

    day_matches = [_DAY_RE.match(p) for p in periods]
    if n and all(day_matches) and len(set(periods)) > 60:
        days = [int(m.group(1)) for m in day_matches]
        ticks, labels, seen = [], [], set()
        for i, day in enumerate(days):
            month = _day_to_month(day)
            if month not in seen:
                seen.add(month)
                ticks.append(i)
                labels.append(month)
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels, rotation=0, ha="center", fontsize=9)
        return

    step = _tick_step(n)
    ax.set_xticks(np.arange(n)[::step])
    ax.set_xticklabels(periods[::step], rotation=45, ha="right", fontsize=8)


def _save(fig, path: Path, dpi=200) -> Path:
    fig.tight_layout(pad=1.5)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _indicator_value(result: dict[str, Any], metric: str, default: Any = None) -> Any:
    indicators = result.get("indicators", {})
    if isinstance(indicators, dict):
        return indicators.get(metric, default)
    if isinstance(indicators, pd.DataFrame) and {"metric", "value"}.issubset(indicators.columns):
        rows = indicators[indicators["metric"].astype(str).str.strip().eq(metric)]
        if not rows.empty:
            return pd.to_numeric(rows.iloc[0]["value"], errors="coerce")
    return default


def _plot_mc_cost_distribution(costs: list[float], path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(costs, bins=min(30, max(5, len(costs) // 4)), color=COL["residual"], alpha=0.8, edgecolor="white")
    mean = float(np.mean(costs))
    ax.axvline(mean, color=COL["price"], linewidth=2, label=f"Mean: {mean:,.0f}")
    _style(ax, "Monte Carlo Cost Distribution", "Scenarios", "Total cost (EUR)")
    ax.legend(frameon=False)
    return _save(fig, path)


def _plot_mc_bands(
    df: pd.DataFrame,
    value_col: str,
    title: str,
    ylabel: str,
    path: Path,
    period_col: str | None = None,
) -> Path:
    data = df.copy()
    data[value_col] = pd.to_numeric(data[value_col], errors="coerce")
    period_col = period_col or ("period" if "period" in data.columns else "time_id")
    data = data.dropna(subset=[period_col, value_col])
    data, period_col, xlabel, title = _prepare_mc_band_data(data, period_col, value_col, title)
    if "scenario_id" in data.columns:
        data = data.groupby([period_col, "scenario_id"], as_index=False)[value_col].mean()
    stats = (
        data.groupby(period_col)[value_col]
        .agg(
            p05=lambda x: x.quantile(0.05),
            p50="median",
            p95=lambda x: x.quantile(0.95),
        )
        .reset_index()
    )
    stats = stats.set_index(period_col).loc[_time_sort(stats[period_col])]
    x = np.arange(len(stats))
    fig, ax = plt.subplots(figsize=(12, 5.2))
    ax.fill_between(x, stats["p05"].astype(float), stats["p95"].astype(float), color=COL["residual"], alpha=0.20, label="P5-P95")
    ax.plot(x, stats["p50"].astype(float), color=COL["residual"], linewidth=2, label="Median")
    _set_period_xticks(ax, stats.index)
    _style(ax, title, ylabel, xlabel=xlabel)
    ax.legend(frameon=False)
    return _save(fig, path)


def _prepare_mc_band_data(
    data: pd.DataFrame,
    period_col: str,
    value_col: str,
    title: str,
) -> tuple[pd.DataFrame, str, str, str]:
    parsed = data[period_col].astype(str).str.extract(r"^D(?P<day>\d+)_H(?P<hour>\d+)$")
    if parsed["day"].notna().all() and data[period_col].nunique() > 1500:
        data = data.copy()
        data["_day"] = parsed["day"].astype(int).map(lambda day: f"D{day:03d}")
        group_cols = ["_day"]
        if "scenario_id" in data.columns:
            group_cols.append("scenario_id")
        data = data.groupby(group_cols, as_index=False)[value_col].mean()
        return data, "_day", "Month", f"{title} (daily mean)"
    return data, period_col, "Hour", title


def _plot_mc_workflow(
    n_scenarios: int,
    max_workers: int,
    path: Path,
    completed_scenarios: int | None = None,
) -> Path:
    fig, ax = plt.subplots(figsize=(11, 3.8))
    ax.axis("off")
    scenario_label = f"{completed_scenarios}/{n_scenarios} completed scenarios" if completed_scenarios is not None else f"{n_scenarios} scenarios"
    labels = [
        "Excel inputs",
        "PECD load, wind,\nand solar sampling",
        f"GAMS runs\n{scenario_label}",
        "Excel summaries",
    ]
    x = np.linspace(0.13, 0.87, len(labels))
    box_w = 0.19
    box_h = 0.24
    for i, (label, xpos) in enumerate(zip(labels, x)):
        ax.add_patch(Rectangle((xpos - box_w / 2, 0.40), box_w, box_h, facecolor="#F3F4F6", edgecolor="#9CA3AF"))
        ax.text(xpos, 0.52, label, ha="center", va="center", fontsize=9)
        if i < len(labels) - 1:
            ax.annotate(
                "",
                xy=(x[i + 1] - box_w / 2 - 0.015, 0.52),
                xytext=(xpos + box_w / 2 + 0.015, 0.52),
                arrowprops=dict(arrowstyle="->", color="#4B5563"),
            )
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title("Monte Carlo Workflow", fontsize=14, fontweight="bold")
    return _save(fig, path)


# ------------------------------------------------------------------------------
# Existing / unchanged charts (minor styling improvements)
# ------------------------------------------------------------------------------
def _plot_generation_mix(data, path):
    d = data["dispatch"].copy()
    d["dispatch_mw"] = pd.to_numeric(d["dispatch_mw"], errors="coerce").fillna(0)
    supply = d[~d["category"].isin(["curtailment", "export", "flex_down"])]
    pivot = supply.pivot_table(index="period", columns="category", values="dispatch_mw", aggfunc="sum", fill_value=0)
    pivot = pivot.loc[_time_sort(pivot.index)]
    order = [c for c in ["dispatchable", "res_used", "import", "flex_up", "ens"] if c in pivot.columns]
    order += [c for c in pivot.columns if c not in order]
    pivot = pivot[order]

    fig, ax = plt.subplots(figsize=(14, 5.5))
    bottom = np.zeros(len(pivot))
    x = np.arange(len(pivot))
    for col in pivot.columns:
        vals = pivot[col].values
        color = COL.get(col, "#9CA3AF")
        ax.bar(x, vals, bottom=bottom, label=col.replace("_", " ").title(), color=color, width=0.95, linewidth=0)
        bottom += vals
    _set_period_xticks(ax, pivot.index)
    _style(ax, "Generation Mix by Representative Hour", "Power (MW)")
    ax.legend(ncol=5, fontsize=9, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.15))
    return _save(fig, path)


def _plot_price_curve(data, path):
    p = data["price"].copy()
    p["price_eur_mwh"] = pd.to_numeric(p["price_eur_mwh"], errors="coerce").fillna(0)
    p = p.set_index("period").loc[_time_sort(p["period"])]
    x = np.arange(len(p))
    fig, ax = plt.subplots(figsize=(14, 4.5))
    ax.plot(x, p["price_eur_mwh"], color=COL["price"], linewidth=2.0, label="Diagnostic price")
    ax.fill_between(x, p["price_eur_mwh"], color=COL["price"], alpha=0.15)
    _set_period_xticks(ax, p.index)
    _style(ax, "Electricity Price Diagnostic Curve", "EUR/MWh")
    ax.text(0.01, -0.25, "Note: For MIP/UC, balance marginals are diagnostic, not market-clearing prices.",
            transform=ax.transAxes, fontsize=9, color="#6B7280")
    return _save(fig, path)


def _plot_storage_soc_stacked(data, path):
    """Stacked area chart for storage/flex state of charge."""
    s = data["storage"].copy()
    s["soc_mwh"] = pd.to_numeric(s["soc_mwh"], errors="coerce").fillna(0)
    s["period"] = pd.Categorical(s["period"], categories=_time_sort(s["period"].unique()), ordered=True)
    s = s.sort_values("period")
    # Pivot: periods as index, flex_id as columns
    pivot = s.pivot(index="period", columns="flex_id", values="soc_mwh").fillna(0)
    fig, ax = plt.subplots(figsize=(14, 5.5))
    # Stacked area
    pivot.plot.area(ax=ax, colormap="viridis", alpha=0.8, linewidth=0.5)
    ax.set_xlabel("Representative hour", fontsize=12)
    ax.set_ylabel("State of Charge (MWh)", fontsize=12)
    ax.set_title("Storage & Flexibility Energy State (Stacked Area)", fontsize=14, fontweight="bold")
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    ax.legend(title="Flex resource", frameon=False, bbox_to_anchor=(1.05, 1), loc="upper left")
    # Reduce x-ticks
    ticks = np.arange(0, len(pivot), 12)
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(pivot.index[i]) for i in ticks], rotation=45, ha="right", fontsize=8)
    return _save(fig, path)


def _plot_flex_needs(data, path):
    r = data["residual"].copy()
    resv = data["reserve"].copy()
    for col in ["ramp_up_mw", "ramp_down_mw", "curtailment_mw", "flex_up_mw", "flex_down_mw"]:
        r[col] = pd.to_numeric(r[col], errors="coerce").fillna(0)
    for col in ["up_shortfall_mw", "down_shortfall_mw"]:
        resv[col] = pd.to_numeric(resv[col], errors="coerce").fillna(0)
    df = pd.DataFrame({
        "Upward ramp need": [r["ramp_up_mw"].max()],
        "Downward ramp need": [r["ramp_down_mw"].max()],
        "RES curtailment": [r["curtailment_mw"].max()],
        "Up reserve shortfall": [resv["up_shortfall_mw"].max()],
        "Down reserve shortfall": [resv["down_shortfall_mw"].max()],
        "Flex up used": [r["flex_up_mw"].max()],
        "Flex down used": [r["flex_down_mw"].max()],
    }).T
    df.columns = ["MW"]
    colors = [COL["up"], COL["down"], COL["curtailment"], COL["shortfall"], COL["shortfall"], COL["flex_up"], COL["flex_down"]]
    fig, ax = plt.subplots(figsize=(11, 5.2))
    ax.barh(df.index, df["MW"], color=colors)
    _style(ax, "Flexibility Needs and Activation Summary", "MW", "")
    ax.set_xlabel("MW", fontsize=12)
    return _save(fig, path)


def _plot_reserve_margin(data, path):
    """Improved reserve stack with shortfall highlighted."""
    df = data["reserve"].copy()
    for col in ["up_requirement_mw", "down_requirement_mw", "up_available_mw", "down_available_mw", "up_shortfall_mw", "down_shortfall_mw"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df = df.set_index("period").loc[_time_sort(df["period"])]
    x = np.arange(len(df))

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    # Upward
    ax1 = axes[0]
    ax1.bar(x, df["up_available_mw"], label="Available Upward Reserve", color=COL["up"], alpha=0.7)
    ax1.bar(x, df["up_shortfall_mw"], bottom=df["up_available_mw"], label="Shortfall", color=COL["shortfall"], alpha=0.8)
    ax1.plot(x, df["up_requirement_mw"], color="black", linewidth=1.5, linestyle="--", label="Requirement")
    ax1.set_ylabel("MW", fontsize=12)
    ax1.set_title("Upward Reserve & Short-Term Flexibility", fontsize=13, fontweight="bold")
    ax1.legend(frameon=False)
    ax1.grid(axis="y", linestyle="--", alpha=0.3)

    # Downward
    ax2 = axes[1]
    ax2.bar(x, df["down_available_mw"], label="Available Downward Reserve", color=COL["down"], alpha=0.7)
    ax2.bar(x, df["down_shortfall_mw"], bottom=df["down_available_mw"], label="Shortfall", color=COL["shortfall"], alpha=0.8)
    ax2.plot(x, df["down_requirement_mw"], color="black", linewidth=1.5, linestyle="--", label="Requirement")
    ax2.set_ylabel("MW", fontsize=12)
    ax2.set_title("Downward Reserve & Short-Term Flexibility", fontsize=13, fontweight="bold")
    ax2.legend(frameon=False)
    ax2.grid(axis="y", linestyle="--", alpha=0.3)

    _set_period_xticks(ax2, df.index)
    fig.suptitle("Reserve Margins and Shortfalls", fontsize=14, fontweight="bold", y=1.02)
    fig.tight_layout()
    return _save(fig, path)


def _plot_rldc(data, path):
    """Residual Load Duration Curve with flexibility zones."""
    resid = data["residual"].copy()
    resid["residual_load_mw"] = pd.to_numeric(resid["residual_load_mw"], errors="coerce").fillna(0)
    rldc = resid["residual_load_mw"].sort_values(ascending=False).values
    hours = np.arange(1, len(rldc) + 1)

    flex = data["storage"].copy()
    upward_cap = pd.to_numeric(flex["flex_up_mw"], errors="coerce").max() if "flex_up_mw" in flex else 0
    downward_cap = pd.to_numeric(flex["flex_down_mw"], errors="coerce").max() if "flex_down_mw" in flex else 0

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(hours, rldc, color=COL["residual"], linewidth=2, label="Residual load")
    ax.axhline(y=upward_cap, color=COL["upward_flex_cap"], linestyle="--", label="Upward flexibility capacity")
    ax.axhline(y=-downward_cap, color=COL["downward_flex_cap"], linestyle="--", label="Downward flexibility capacity")
    ax.fill_between(hours, rldc, upward_cap, where=(rldc > upward_cap), color=COL["shortfall"], alpha=0.3, label="ENS region")
    ax.fill_between(hours, rldc, -downward_cap, where=(rldc < -downward_cap), color=COL["curtailment"], alpha=0.3, label="Curtailment region")
    ax.set_xlabel("Hours sorted by residual load", fontsize=12)
    ax.set_ylabel("Residual load (MW)", fontsize=12)
    ax.set_title("Residual Load Duration Curve with Flexibility Zones", fontsize=14, fontweight="bold")
    ax.legend(frameon=False)
    ax.grid(alpha=0.3)
    return _save(fig, path)


def _plot_ramp_matrix(data, path):
    """2D histogram of ramp-up vs ramp-down events."""
    resid = data["residual"].copy()
    ramp_up = pd.to_numeric(resid["ramp_up_mw"], errors="coerce").fillna(0)
    ramp_down = pd.to_numeric(resid["ramp_down_mw"], errors="coerce").fillna(0)
    fig, ax = plt.subplots(figsize=(8, 6))
    h = ax.hist2d(ramp_up, ramp_down, bins=50, cmap="YlOrRd", norm=matplotlib.colors.LogNorm())
    ax.set_xlabel("Ramp-up (MW/h)", fontsize=12)
    ax.set_ylabel("Ramp-down (MW/h)", fontsize=12)
    ax.set_title("Ramp Event Probability Matrix", fontsize=14, fontweight="bold")
    cbar = plt.colorbar(h[3], ax=ax)
    cbar.set_label("Number of events (log scale)", fontsize=10)
    return _save(fig, path)


def _plot_gantt_thermal(data, path):
    """Gantt chart for unit commitment, only thermal (dispatchable) generators."""
    dispatch = data["dispatch"].copy()
    dispatch["committed_blocks"] = pd.to_numeric(dispatch["committed_blocks"], errors="coerce").fillna(0)
    # Keep only thermal/dispatchable resources
    thermal = dispatch[dispatch["category"] == "dispatchable"].copy()
    if thermal.empty:
        return path
    periods = thermal["period"].unique()
    periods = _time_sort(periods)
    n_days = min(4, len(periods)//24)
    show_periods = periods[:n_days*24]
    if not show_periods:
        return path
    thermal_sub = thermal[thermal["period"].isin(show_periods)].copy()
    resources = thermal_sub["resource"].unique()
    resource_order = thermal_sub.groupby("resource")["committed_blocks"].sum().sort_values(ascending=False).index
    pivot = thermal_sub.pivot(index="resource", columns="period", values="committed_blocks").fillna(0)
    pivot = pivot.reindex(resource_order)
    fig, ax = plt.subplots(figsize=(14, max(6, len(resources)*0.3)))
    im = ax.imshow(pivot, aspect="auto", cmap="Blues", interpolation="nearest")
    ax.set_xticks(np.arange(len(show_periods))[::12])
    ax.set_xticklabels([str(p)[:10] for p in show_periods[::12]], rotation=45, ha="right", fontsize=8)
    ax.set_yticks(np.arange(len(pivot.index)))
    ax.set_yticklabels(pivot.index, fontsize=9)
    ax.set_xlabel("Representative hour", fontsize=12)
    ax.set_ylabel("Thermal generator group", fontsize=12)
    ax.set_title("Unit Commitment Gantt (first 4 days, thermal units only)\nColor = number of committed blocks", fontsize=14, fontweight="bold")
    cbar = plt.colorbar(im, ax=ax)
    cbar.set_label("Committed blocks", fontsize=10)
    return _save(fig, path)


def _plot_stacked_dispatch_with_price(data, path):
    """Stacked hourly dispatch with flexibility activation + price curve on secondary axis."""
    d = data["dispatch"].copy()
    d["dispatch_mw"] = pd.to_numeric(d["dispatch_mw"], errors="coerce").fillna(0)
    # Aggregate categories: group technologies creatively
    # We'll keep same categories: dispatchable (thermal), res_used, import, flex_up, ens
    supply = d[~d["category"].isin(["curtailment", "export", "flex_down"])]
    pivot = supply.pivot_table(index="period", columns="category", values="dispatch_mw", aggfunc="sum", fill_value=0)
    pivot = pivot.loc[_time_sort(pivot.index)]
    order = [c for c in ["dispatchable", "res_used", "import", "flex_up", "ens"] if c in pivot.columns]
    order += [c for c in pivot.columns if c not in order]
    pivot = pivot[order]

    # Price data
    price_df = data["price"].copy()
    price_df["price_eur_mwh"] = pd.to_numeric(price_df["price_eur_mwh"], errors="coerce").fillna(0)
    price_df = price_df.set_index("period").loc[pivot.index]

    fig, ax1 = plt.subplots(figsize=(14, 6))
    # Stacked bars on ax1
    bottom = np.zeros(len(pivot))
    x = np.arange(len(pivot))
    for col in pivot.columns:
        vals = pivot[col].values
        color = COL.get(col, "#9CA3AF")
        ax1.bar(x, vals, bottom=bottom, label=col.replace("_", " ").title(), color=color, width=0.95)
        bottom += vals
    ax1.set_xlabel("Representative hour", fontsize=12)
    ax1.set_ylabel("Power (MW)", fontsize=12)
    ax1.tick_params(axis="y", labelcolor="black")
    _set_period_xticks(ax1, pivot.index)
    ax1.grid(axis="y", linestyle="--", alpha=0.3)

    # Secondary axis for price
    ax2 = ax1.twinx()
    ax2.plot(x, price_df["price_eur_mwh"], color=COL["price"], linewidth=2.5, linestyle="-", marker="o", markersize=3, label="Price (EUR/MWh)")
    ax2.set_ylabel("Price (EUR/MWh)", fontsize=12, color=COL["price"])
    ax2.tick_params(axis="y", labelcolor=COL["price"])

    # Title and legend
    ax1.set_title("Stacked Hourly Dispatch with Flexibility Activation and Price Curve", fontsize=14, fontweight="bold")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, ncol=6, fontsize=9, frameon=False, loc="lower center", bbox_to_anchor=(0.5, -0.12))
    return _save(fig, path)


def _plot_sankey(data, path):
    """Sankey diagram of weighted annual energy flows (requires plotly)."""
    if not HAS_PLOTLY:
        log.warning("Plotly not installed; skipping Sankey diagram.")
        return path
    resid = data["residual"].copy()
    for col in [
        "demand_mw",
        "total_generation_mw",
        "curtailment_mw",
        "ens_mw",
        "netimport_mw",
        "flex_up_mw",
        "flex_down_mw",
    ]:
        resid[col] = pd.to_numeric(resid[col], errors="coerce").fillna(0)
    total_demand = resid["demand_mw"].sum()
    total_gen = resid["total_generation_mw"].sum()
    total_curtail = resid["curtailment_mw"].sum()
    total_ens = resid["ens_mw"].sum()
    total_import = resid["netimport_mw"].clip(lower=0).sum()
    total_export = resid["netimport_mw"].clip(upper=0).abs().sum()
    total_flex_up = resid["flex_up_mw"].sum()
    total_flex_down = resid["flex_down_mw"].sum()
    labels = ["Demand", "Generation", "Imports", "Exports", "Flex Up", "Flex Down", "Curtailment", "ENS"]
    sources = []
    targets = []
    values = []
    if total_gen > 0:
        sources.append(1); targets.append(0); values.append(total_gen)
    if total_import > 0:
        sources.append(2); targets.append(0); values.append(total_import)
    if total_flex_up > 0:
        sources.append(4); targets.append(0); values.append(total_flex_up)
    if total_export > 0:
        sources.append(0); targets.append(3); values.append(total_export)
    if total_flex_down > 0:
        sources.append(0); targets.append(5); values.append(total_flex_down)
    if total_curtail > 0:
        sources.append(1); targets.append(6); values.append(total_curtail)
    if total_ens > 0:
        sources.append(7); targets.append(0); values.append(total_ens)

    fig = go.Figure(data=[go.Sankey(
        node=dict(pad=15, thickness=20, line=dict(color="black", width=0.5), label=labels, color="lightblue"),
        link=dict(source=sources, target=targets, value=values)
    )])
    fig.update_layout(title_text="Annual Energy Flows (Weighted)", font_size=12, width=800, height=600)
    fig.write_image(str(path))
    return path


# ------------------------------------------------------------------------------
# Excel integration (updated positions)
# ------------------------------------------------------------------------------
def write_charts_sheet(wb: Any, data: dict[str, pd.DataFrame], chart_paths: dict[str, Path]) -> None:
    from io_excel import _add_image, _set_cell, _ws_get_or_create

    sht = _ws_get_or_create(wb, "40_Charts")
    _set_cell(sht, "A1", "Belgium FNA-ED/UC v2 Output Dashboard", bold=True, size=16)
    _set_cell(sht, "A2", "Charts are generated from GAMS CSV outputs. Values update after each model run.", italic=True)

    indicators = data.get("indicators")
    if indicators is not None and not indicators.empty:
        _set_cell(sht, "A4", "FNA indicator summary", bold=True)
        # Header + rows starting at A5.
        sht["A5"] = None
        for col_index, col in enumerate(indicators.columns):
            sht.cell(row=5, column=1 + col_index, value=str(col))
        safe = indicators.astype(object).where(pd.notna(indicators), None)
        for row_index, record in enumerate(safe.values.tolist()):
            for col_index, value in enumerate(record):
                sht.cell(row=6 + row_index, column=1 + col_index, value=value)

    positions = {
        "generation_mix": "H4",
        "price_curve": "H26",
        "rldc": "H48",
        "flex_needs": "A28",
        "reserve_margin": "A50",
        "storage_soc": "A72",
        "ramp_matrix": "H70",
        "gantt": "A94",
        "sankey": "H94",
        "stacked_dispatch_price": "H116",
    }
    for name, img in chart_paths.items():
        if img.exists() and name in positions:
            try:
                _add_image(sht, img, positions[name], width=620)
            except Exception as exc:
                log.warning("Could not insert %s into Excel: %s", img, exc)
