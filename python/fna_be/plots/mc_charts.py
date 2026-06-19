"""
mc_charts.py - Belgium FNA-ED/UC v3
====================================
Extra probabilistic Monte Carlo charts, layered on top of the summary charts
already built by ``plot_results.generate_mc_summary_charts`` (cost
distribution, PECD input bands, residual-load/price bands, workflow).

All functions here are read-only consumers of the per-scenario results dict
(``mc_results[scenario_id] = {"indicators", "residual", "reserve",
"dispatch", "price", ...}``) and of ``uncertainty_scenarios`` (the PECD
load/solar/wind multiplier scenarios). Each chart function returns the saved
``Path``, or ``None`` if the data needed for it is not available - callers
should treat ``None`` as "skip this chart", not an error.

``generate_extra_mc_charts()`` is the single entry point: it builds a tidy
per-scenario summary table once, then calls each chart function, catching and
logging any failure so one bad chart never aborts the whole run.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

from fna_be.plots.base import COL, _day_to_month, _indicator_value, _save, _set_period_xticks, _style, _time_sort

log = logging.getLogger(__name__)

_PERIOD_RE = re.compile(r"^D(\d+)_H(\d+)$")


# ---------------------------------------------------------------------------
# Per-scenario summary table
# ---------------------------------------------------------------------------

def _build_scenario_summary(mc_results: dict[int, dict]) -> pd.DataFrame:
    """One row per scenario: total cost, ENS/curtailment/shortfall energy and
    event counts, flexibility activation. Missing columns -> 0 / NaN."""

    rows: list[dict] = []
    for scenario_id, result in sorted(mc_results.items()):
        row: dict[str, float] = {"scenario_id": scenario_id, "total_cost": _indicator_value(result, "total_cost")}

        residual = result.get("residual")
        if isinstance(residual, pd.DataFrame) and not residual.empty:
            ens = pd.to_numeric(residual.get("ens_mw", 0.0), errors="coerce").fillna(0.0)
            curt = pd.to_numeric(residual.get("curtailment_mw", 0.0), errors="coerce").fillna(0.0)
            flex_up = pd.to_numeric(residual.get("flex_up_mw", 0.0), errors="coerce").fillna(0.0)
            flex_dn = pd.to_numeric(residual.get("flex_down_mw", 0.0), errors="coerce").fillna(0.0)
            row["ens_mwh"] = float(ens.sum())
            row["n_ens_periods"] = int((ens > 1e-6).sum())
            row["curtailment_mwh"] = float(curt.sum())
            row["n_curtailment_periods"] = int((curt > 1e-6).sum())
            row["flex_up_mwh"] = float(flex_up.sum())
            row["flex_down_mwh"] = float(flex_dn.sum())

        reserve = result.get("reserve")
        if isinstance(reserve, pd.DataFrame) and not reserve.empty:
            up_short = pd.to_numeric(reserve.get("up_shortfall_mw", 0.0), errors="coerce").fillna(0.0)
            dn_short = pd.to_numeric(reserve.get("down_shortfall_mw", 0.0), errors="coerce").fillna(0.0)
            row["up_shortfall_mwh"] = float(up_short.sum())
            row["down_shortfall_mwh"] = float(dn_short.sum())
            row["n_up_shortfall_periods"] = int((up_short > 1e-6).sum())
            row["n_down_shortfall_periods"] = int((dn_short > 1e-6).sum())

        rows.append(row)
    return pd.DataFrame(rows)


def _scenario_input_means(uncertainty_scenarios: dict[int, pd.DataFrame]) -> pd.DataFrame:
    """Per-scenario mean load multiplier, solar CF and wind CF (for the
    correlation heatmap)."""

    solar_pattern = re.compile(r"(?:solar|pv)", re.IGNORECASE)
    wind_pattern = re.compile(r"(?:wind|onshore|offshore)", re.IGNORECASE)

    rows: list[dict] = []
    for scenario_id, scenario in uncertainty_scenarios.items():
        if not isinstance(scenario, pd.DataFrame) or scenario.empty:
            continue
        row: dict[str, float] = {"scenario_id": scenario_id}

        demand_col = f"demand_multiplier_scenario_{scenario_id}"
        if demand_col in scenario.columns:
            row["load_multiplier_mean"] = float(pd.to_numeric(scenario[demand_col], errors="coerce").mean())

        cf_col = f"cf_scenario_{scenario_id}"
        if cf_col in scenario.columns and "res_id" in scenario.columns:
            res_id = scenario["res_id"].astype(str)
            cf = pd.to_numeric(scenario[cf_col], errors="coerce")
            solar_mask = res_id.str.contains(solar_pattern, na=False)
            wind_mask = res_id.str.contains(wind_pattern, na=False)
            if solar_mask.any():
                row["solar_cf_mean"] = float(cf[solar_mask].mean())
            if wind_mask.any():
                row["wind_cf_mean"] = float(cf[wind_mask].mean())

        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 1. Loss-of-load risk (LOLE / LOLP)
# ---------------------------------------------------------------------------

def _plot_lole_histogram(summary: pd.DataFrame, path: Path) -> Path | None:
    if "ens_mwh" not in summary.columns:
        return None
    ens = summary["ens_mwh"].dropna()
    if ens.empty:
        return None

    lolp = float((ens > 1e-6).mean())
    fig, ax = plt.subplots(figsize=(9, 5))
    bins = min(30, max(5, len(ens) // 3))
    ax.hist(ens, bins=bins, color=COL["ens"], alpha=0.75, edgecolor="white")
    ax.axvline(float(ens.mean()), color=COL["price"], linewidth=2, label=f"Mean: {ens.mean():,.1f} MWh/run")
    _style(
        ax,
        f"Loss-of-Load Risk Across Scenarios  (LOLP = {lolp:.1%})",
        "Number of scenarios",
        "Energy not served per scenario (MWh)",
    )
    ax.legend(frameon=False)
    return _save(fig, path)


def _plot_ens_exceedance_curve(summary: pd.DataFrame, path: Path) -> Path | None:
    if "ens_mwh" not in summary.columns:
        return None
    ens = summary["ens_mwh"].dropna()
    if ens.empty:
        return None

    thresholds = sorted({0.0, 100.0, 500.0, 1000.0, 2000.0, float(ens.max())})
    probs = [float((ens > t).mean()) for t in thresholds]

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.step(thresholds, probs, where="post", color=COL["shortfall"], linewidth=2)
    ax.scatter(thresholds, probs, color=COL["shortfall"], zorder=3)
    for x, p in zip(thresholds, probs):
        ax.annotate(f"{p:.0%}", (x, p), textcoords="offset points", xytext=(5, 6), fontsize=8)
    ax.set_ylim(0, 1.05)
    _style(ax, "Probability of Exceeding an ENS Threshold", "P(ENS per run > threshold)", "ENS threshold (MWh per run)")
    return _save(fig, path)


# ---------------------------------------------------------------------------
# 2. ELCC - methodology placeholder (needs a second MC run with a candidate
#    resource added; not computable from a single run)
# ---------------------------------------------------------------------------

def _plot_elcc_placeholder(path: Path) -> Path | None:
    log.warning(
        "ELCC (Effective Load Carrying Capability) requires comparing LOLE between "
        "two Monte Carlo runs (base system vs. base + candidate resource). This run "
        "covers a single system configuration, so only the methodology is shown."
    )

    steps = [
        "Run 1: Base system\nMonte Carlo\n-> LOLE_base",
        "Run 2: Base system\n+ candidate resource\n-> LOLE_new",
        "Add firm load to Run 2\nuntil LOLE_new = LOLE_base",
        "ELCC = firm load added\n(MW)",
    ]
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.axis("off")
    x = np.linspace(0.13, 0.87, len(steps))
    box_w, box_h = 0.21, 0.30
    for i, (label, xpos) in enumerate(zip(steps, x)):
        ax.add_patch(Rectangle((xpos - box_w / 2, 0.34), box_w, box_h, facecolor="#F3F4F6", edgecolor="#9CA3AF"))
        ax.text(xpos, 0.49, label, ha="center", va="center", fontsize=9)
        if i < len(steps) - 1:
            ax.annotate(
                "",
                xy=(x[i + 1] - box_w / 2 - 0.015, 0.49),
                xytext=(xpos + box_w / 2 + 0.015, 0.49),
                arrowprops=dict(arrowstyle="->", color="#4B5563"),
            )
    ax.text(0.5, 0.90, "Effective Load Carrying Capability (ELCC) - methodology", ha="center", fontsize=13, fontweight="bold")
    ax.text(
        0.5, 0.08,
        "This run is a single system configuration, so ELCC cannot be computed here.\n"
        "Re-run the Monte Carlo with the candidate resource added and compare LOLE between the two runs.",
        ha="center", fontsize=9, color="#6B7280",
    )
    return _save(fig, path)


# ---------------------------------------------------------------------------
# 3. Probability of stress events (ENS, curtailment, reserve shortfall)
# ---------------------------------------------------------------------------

def _plot_event_probabilities(summary: pd.DataFrame, path: Path) -> Path | None:
    if summary.empty:
        return None

    n = len(summary)
    events = {
        "ENS (loss of load)": float((summary.get("ens_mwh", pd.Series(dtype=float)).fillna(0.0) > 1e-6).mean()),
        "RES curtailment": float((summary.get("curtailment_mwh", pd.Series(dtype=float)).fillna(0.0) > 1e-6).mean()),
        "Upward reserve shortfall": float((summary.get("up_shortfall_mwh", pd.Series(dtype=float)).fillna(0.0) > 1e-6).mean()),
        "Downward reserve shortfall": float((summary.get("down_shortfall_mwh", pd.Series(dtype=float)).fillna(0.0) > 1e-6).mean()),
    }
    names = list(events.keys())
    probs = [events[name] for name in names]
    colors = [COL["ens"], COL["curtailment"], COL["up"], COL["down"]]

    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.barh(names, probs, color=colors)
    for bar, p in zip(bars, probs):
        ax.text(min(p + 0.02, 0.97), bar.get_y() + bar.get_height() / 2, f"{p:.0%}", va="center", fontsize=10)
    ax.set_xlim(0, 1.05)
    ax.invert_yaxis()
    _style(ax, f"Probability of System-Stress Events  ({n} scenarios)", "", "Fraction of scenarios")
    return _save(fig, path)


# ---------------------------------------------------------------------------
# 4. Cost-risk efficient frontier (cost vs. LOLE)
# ---------------------------------------------------------------------------

def _pareto_front(points: np.ndarray) -> np.ndarray:
    """Lower-left (min cost, min risk) non-dominated points, sorted by x."""
    order = points[np.argsort(points[:, 0])]
    front, best_y = [], np.inf
    for p in order:
        if p[1] < best_y:
            front.append(p)
            best_y = p[1]
    return np.array(front)


def _plot_efficiency_frontier(summary: pd.DataFrame, path: Path) -> Path | None:
    if "total_cost" not in summary.columns or "n_ens_periods" not in summary.columns:
        return None
    df = summary.dropna(subset=["total_cost"]).copy()
    if df.empty:
        return None
    df["lole_hours"] = df["n_ens_periods"].astype(float)

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(df["total_cost"], df["lole_hours"], color=COL["residual"], alpha=0.7, edgecolor="white", zorder=2)

    points = df[["total_cost", "lole_hours"]].to_numpy()
    if len(points) > 1:
        pareto = _pareto_front(points)
        if len(pareto) > 1:
            ax.plot(pareto[:, 0], pareto[:, 1], color=COL["shortfall"], linewidth=2, marker="o", label="Efficient frontier", zorder=3)
            ax.legend(frameon=False)

    _style(ax, "Cost-Risk Trade-off Across Scenarios", "LOLE (representative hours with ENS > 0)", "Total system cost (EUR)")
    return _save(fig, path)


# ---------------------------------------------------------------------------
# 5. Price distribution (boxplot per month/period + density)
# ---------------------------------------------------------------------------

def _plot_price_boxplot(price_df: pd.DataFrame, path: Path) -> Path | None:
    if price_df.empty or "price_eur_mwh" not in price_df.columns:
        return None
    df = price_df.copy()
    df["price_eur_mwh"] = pd.to_numeric(df["price_eur_mwh"], errors="coerce")
    df = df.dropna(subset=["price_eur_mwh"])
    if df.empty:
        return None

    matches = df["period"].astype(str).str.extract(_PERIOD_RE)
    if matches[0].notna().all() and matches[0].astype(int).nunique() > 14:
        df["_group"] = matches[0].astype(int).map(_day_to_month)
        month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        groups = [m for m in month_order if m in set(df["_group"])]
        xlabel, rotation, align = "Month", 0, "center"
    else:
        df["_group"] = df["period"].astype(str)
        groups = _time_sort(df["_group"].unique())
        xlabel, rotation, align = "Representative period", 45, "right"

    data = [df.loc[df["_group"] == g, "price_eur_mwh"].to_numpy() for g in groups]
    groups = [g for g, d in zip(groups, data) if len(d)]
    data = [d for d in data if len(d)]
    if not data:
        return None

    fig, ax = plt.subplots(figsize=(12, 5.5))
    bp = ax.boxplot(data, tick_labels=groups, showfliers=False, patch_artist=True)
    for patch in bp["boxes"]:
        patch.set_facecolor(COL["price"])
        patch.set_alpha(0.5)
    for median in bp["medians"]:
        median.set_color("black")
    plt.setp(ax.get_xticklabels(), rotation=rotation, ha=align, fontsize=8)
    _style(ax, "Price Distribution Across Monte Carlo Scenarios", "EUR/MWh", xlabel)
    return _save(fig, path)


def _plot_price_distribution(price_df: pd.DataFrame, path: Path) -> Path | None:
    if price_df.empty or "price_eur_mwh" not in price_df.columns:
        return None
    prices = pd.to_numeric(price_df["price_eur_mwh"], errors="coerce").dropna()
    if prices.empty:
        return None

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(prices, bins=60, density=True, color=COL["price"], alpha=0.55, edgecolor="white", label="All scenarios x hours")

    try:
        from scipy import stats as scipy_stats

        xs = np.linspace(float(prices.min()), float(prices.max()), 200)
        kde = scipy_stats.gaussian_kde(prices)
        ax.plot(xs, kde(xs), color="black", linewidth=2, label="Kernel density")
        try:
            a, loc, scale = scipy_stats.skewnorm.fit(prices)
            ax.plot(xs, scipy_stats.skewnorm.pdf(xs, a, loc, scale), color=COL["shortfall"], linestyle="--", linewidth=2, label="Skew-normal fit")
        except Exception as exc:
            log.info("Skew-normal fit failed for price distribution: %s", exc)
    except ImportError:
        log.info("scipy not installed; price distribution chart shows the histogram only (pip install scipy for KDE/skew-normal fit).")

    _style(ax, "Probability Density of Electricity Prices (All Scenarios)", "Density", "EUR/MWh")
    ax.legend(frameon=False)
    return _save(fig, path)


# ---------------------------------------------------------------------------
# 6. Monte Carlo ramp-event probability matrix
# ---------------------------------------------------------------------------

def _plot_mc_ramp_matrix(mc_results: dict[int, dict], path: Path) -> Path | None:
    frames = [r for r in (result.get("residual") for result in mc_results.values()) if isinstance(r, pd.DataFrame) and not r.empty]
    if not frames:
        return None
    all_df = pd.concat(frames, ignore_index=True)
    if "ramp_up_mw" not in all_df.columns or "ramp_down_mw" not in all_df.columns:
        return None

    ramp_up = pd.to_numeric(all_df["ramp_up_mw"], errors="coerce").fillna(0.0)
    ramp_down = pd.to_numeric(all_df["ramp_down_mw"], errors="coerce").fillna(0.0)

    fig, ax = plt.subplots(figsize=(8, 6))
    h = ax.hist2d(ramp_up, ramp_down, bins=50, cmap="YlOrRd", norm=matplotlib.colors.LogNorm())
    ax.set_xlabel("Ramp-up (MW/h)", fontsize=12)
    ax.set_ylabel("Ramp-down (MW/h)", fontsize=12)
    ax.set_title("Monte Carlo Ramp Event Probability Matrix", fontsize=14, fontweight="bold")
    cbar = plt.colorbar(h[3], ax=ax)
    cbar.set_label("Events across all scenarios (log scale)", fontsize=10)
    return _save(fig, path)


# ---------------------------------------------------------------------------
# 7. Dispatch uncertainty (median mix + total generation P5-P95 band)
# ---------------------------------------------------------------------------

def _plot_dispatch_uncertainty(mc_results: dict[int, dict], path: Path) -> Path | None:
    frames = []
    for scenario_id, result in mc_results.items():
        dispatch = result.get("dispatch")
        if not isinstance(dispatch, pd.DataFrame) or dispatch.empty:
            continue
        d = dispatch.copy()
        d["dispatch_mw"] = pd.to_numeric(d["dispatch_mw"], errors="coerce").fillna(0.0)
        supply = d[~d["category"].isin(["curtailment", "export", "flex_down"])]
        pivot = supply.pivot_table(index="period", columns="category", values="dispatch_mw", aggfunc="sum", fill_value=0.0)
        pivot["total"] = pivot.sum(axis=1)
        pivot["scenario_id"] = scenario_id
        frames.append(pivot.reset_index())
    if not frames:
        return None

    all_df = pd.concat(frames, ignore_index=True)
    cat_cols = [c for c in all_df.columns if c not in ("period", "scenario_id", "total")]
    periods = _time_sort(all_df["period"].unique())

    median = all_df.groupby("period")[cat_cols].median().loc[periods]
    total_stats = all_df.groupby("period")["total"].agg(p05=lambda x: x.quantile(0.05), p95=lambda x: x.quantile(0.95)).loc[periods]

    order = [c for c in ["dispatchable", "res_used", "import", "flex_up", "ens"] if c in median.columns]
    order += [c for c in median.columns if c not in order]
    median = median[order]

    x = np.arange(len(median))
    fig, ax = plt.subplots(figsize=(14, 5.5))
    bottom = np.zeros(len(median))
    for col in median.columns:
        vals = median[col].to_numpy()
        ax.bar(x, vals, bottom=bottom, label=col.replace("_", " ").title(), color=COL.get(col, "#9CA3AF"), width=0.95, linewidth=0)
        bottom += vals
    ax.plot(x, total_stats["p05"], color="black", linewidth=1.2, linestyle="--", label="Total generation P5-P95")
    ax.plot(x, total_stats["p95"], color="black", linewidth=1.2, linestyle="--")
    ax.fill_between(x, total_stats["p05"], total_stats["p95"], color="black", alpha=0.08)

    _set_period_xticks(ax, median.index)
    _style(ax, "Monte Carlo Generation Mix - Median Dispatch with Total Range (P5-P95)", "Power (MW)")
    ax.legend(ncol=4, fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.20))
    return _save(fig, path)


# ---------------------------------------------------------------------------
# 8. Reserve percentiles + shortfall probability
# ---------------------------------------------------------------------------

def _plot_reserve_percentiles(mc_results: dict[int, dict], path: Path) -> Path | None:
    frames = [r for r in (result.get("reserve") for result in mc_results.values()) if isinstance(r, pd.DataFrame) and not r.empty]
    if not frames:
        return None
    all_df = pd.concat(frames, ignore_index=True)
    needed = ["up_available_mw", "down_available_mw", "up_requirement_mw", "down_requirement_mw"]
    if not all(c in all_df.columns for c in needed):
        return None
    for c in needed:
        all_df[c] = pd.to_numeric(all_df[c], errors="coerce").fillna(0.0)

    periods = _time_sort(all_df["period"].unique())
    req = all_df.groupby("period")[["up_requirement_mw", "down_requirement_mw"]].median().loc[periods]

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    x = np.arange(len(periods))
    panels = [
        (axes[0], "up_available_mw", "up_requirement_mw", "Upward", COL["up"]),
        (axes[1], "down_available_mw", "down_requirement_mw", "Downward", COL["down"]),
    ]
    for ax, avail_col, req_col, label, color in panels:
        g = all_df.groupby("period")[avail_col]
        stats = pd.DataFrame({"p05": g.quantile(0.05), "p50": g.median(), "p95": g.quantile(0.95)}).loc[periods]
        ax.fill_between(x, stats["p05"], stats["p95"], color=color, alpha=0.2, label="Available reserve P5-P95")
        ax.plot(x, stats["p50"], color=color, linewidth=2, label="Median available reserve")
        ax.plot(x, req[req_col], color="black", linestyle="--", linewidth=1.5, label="Median requirement")
        ax.set_ylabel("MW", fontsize=12)
        ax.set_title(f"{label} Reserve - Monte Carlo Range", fontsize=13, fontweight="bold")
        ax.legend(frameon=False)
        ax.grid(axis="y", linestyle="--", alpha=0.3)

    _set_period_xticks(axes[1], periods)
    fig.suptitle("Reserve Availability Percentiles Across Scenarios", fontsize=14, fontweight="bold", y=1.02)
    return _save(fig, path)


def _plot_reserve_shortfall_probability(summary: pd.DataFrame, path: Path) -> Path | None:
    if summary.empty or "n_up_shortfall_periods" not in summary.columns:
        return None
    n = len(summary)
    up_p = float((summary["n_up_shortfall_periods"] > 0).mean())
    dn_p = float((summary["n_down_shortfall_periods"] > 0).mean())

    fig, ax = plt.subplots(figsize=(7, 5))
    bars = ax.bar(["Upward shortfall", "Downward shortfall"], [up_p, dn_p], color=[COL["up"], COL["down"]])
    for bar, p in zip(bars, [up_p, dn_p]):
        ax.text(bar.get_x() + bar.get_width() / 2, p + 0.02, f"{p:.0%}", ha="center", fontsize=11, fontweight="bold")
    ax.set_ylim(0, 1.05)
    _style(ax, f"Probability of Reserve Shortfall  ({n} scenarios)", "Fraction of scenarios with >=1 shortfall hour", "")
    return _save(fig, path)


# ---------------------------------------------------------------------------
# 9. Flexibility activation distribution
# ---------------------------------------------------------------------------

def _plot_flex_activation(summary: pd.DataFrame, path: Path) -> Path | None:
    cols = [c for c in ["flex_up_mwh", "flex_down_mwh"] if c in summary.columns]
    if not cols:
        return None
    data = [summary[c].dropna().to_numpy() for c in cols]
    data = [d for d in data if len(d)]
    if not data:
        return None
    labels = [c.replace("_mwh", "").replace("_", " ").title() for c in cols if len(summary[c].dropna())]
    colors = {"Flex Up": COL["flex_up"], "Flex Down": COL["flex_down"]}

    fig, ax = plt.subplots(figsize=(8, 5))
    bp = ax.boxplot(data, tick_labels=labels, patch_artist=True)
    for patch, label in zip(bp["boxes"], labels):
        patch.set_facecolor(colors.get(label, "#9CA3AF"))
        patch.set_alpha(0.6)
    for median in bp["medians"]:
        median.set_color("black")
    _style(ax, "Flexibility Activation Across Scenarios", "Energy per run (MWh)", "")
    return _save(fig, path)


# ---------------------------------------------------------------------------
# 10. Correlation heatmap of key risk metrics
# ---------------------------------------------------------------------------

def _plot_correlation_heatmap(summary: pd.DataFrame, input_means: pd.DataFrame, path: Path) -> Path | None:
    df = summary.merge(input_means, on="scenario_id", how="left") if not input_means.empty else summary.copy()
    candidate_cols = [
        "total_cost", "ens_mwh", "curtailment_mwh", "up_shortfall_mwh", "down_shortfall_mwh",
        "load_multiplier_mean", "solar_cf_mean", "wind_cf_mean",
    ]
    cols = [c for c in candidate_cols if c in df.columns]
    if len(cols) < 2 or len(df) < 3:
        return None

    numeric = df[cols].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.loc[:, numeric.std(numeric_only=True).fillna(0.0) > 1e-12]
    if numeric.shape[1] < 2:
        return None
    corr = numeric.corr()

    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(corr.to_numpy(), cmap="RdBu_r", vmin=-1, vmax=1)
    labels = [c.replace("_mwh", "").replace("_", " ").title() for c in corr.columns]
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    for i in range(len(labels)):
        for j in range(len(labels)):
            value = corr.to_numpy()[i, j]
            ax.text(j, i, f"{value:.2f}", ha="center", va="center", fontsize=8, color="white" if abs(value) > 0.5 else "black")
    fig.colorbar(im, ax=ax, label="Pearson correlation")
    ax.set_title("Cross-Scenario Correlation of Key Risk Metrics", fontsize=14, fontweight="bold", pad=12)
    return _save(fig, path)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def generate_extra_mc_charts(
    mc_results: dict[int, dict],
    uncertainty_scenarios: dict[int, pd.DataFrame],
    price_rows: pd.DataFrame,
    img_dir: Path,
) -> dict[str, Path]:
    """Build the additional probabilistic MC charts (risk, dispatch/reserve
    uncertainty, price distribution, correlations). Returns ``{key: path}``
    for whichever charts could be built; failures are logged and skipped."""

    img_dir = Path(img_dir)
    charts: dict[str, Path] = {}
    summary = _build_scenario_summary(mc_results)

    def add(key: str, builder, *args) -> None:
        try:
            result = builder(*args)
        except Exception as exc:
            log.warning("Could not build Monte Carlo chart '%s': %s", key, exc)
            return
        if result is not None:
            charts[key] = result

    add("mc_lole_histogram", _plot_lole_histogram, summary, img_dir / "mc_07_lole_histogram.png")
    add("mc_ens_exceedance_curve", _plot_ens_exceedance_curve, summary, img_dir / "mc_08_ens_exceedance_curve.png")
    add("mc_event_probabilities", _plot_event_probabilities, summary, img_dir / "mc_09_event_probabilities.png")
    add("mc_efficiency_frontier", _plot_efficiency_frontier, summary, img_dir / "mc_10_efficiency_frontier.png")
    add("mc_elcc_placeholder", _plot_elcc_placeholder, img_dir / "mc_11_elcc_placeholder.png")
    add("mc_ramp_matrix", _plot_mc_ramp_matrix, mc_results, img_dir / "mc_12_ramp_matrix.png")
    add("mc_dispatch_uncertainty", _plot_dispatch_uncertainty, mc_results, img_dir / "mc_13_dispatch_uncertainty.png")
    add("mc_reserve_percentiles", _plot_reserve_percentiles, mc_results, img_dir / "mc_14_reserve_percentiles.png")
    add("mc_reserve_shortfall_prob", _plot_reserve_shortfall_probability, summary, img_dir / "mc_15_reserve_shortfall_prob.png")
    add("mc_flex_activation", _plot_flex_activation, summary, img_dir / "mc_16_flex_activation.png")
    add("mc_price_boxplot", _plot_price_boxplot, price_rows, img_dir / "mc_17_price_boxplot.png")
    add("mc_price_distribution", _plot_price_distribution, price_rows, img_dir / "mc_18_price_distribution.png")

    input_means = _scenario_input_means(uncertainty_scenarios)
    add("mc_correlation_heatmap", _plot_correlation_heatmap, summary, input_means, img_dir / "mc_19_correlation_heatmap.png")

    return charts
