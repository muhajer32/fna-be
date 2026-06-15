"""
report.py - Belgium FNA-ED/UC v3
=================================
Builds a single Markdown summary report bundling the key ACER FNA indicator
tables and chart images for a run, so results can be skimmed or shared
without opening the output workbook.

Used by the ``python -m fna_be make-report`` CLI command.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

# Indicator CSVs expected under <out_dir>/fna_tables/, written by
# fna_be.cli's compute-fna-indicators / make-report commands.
_INDICATOR_TABLES: dict[str, str] = {
    "res_integration": "RES Integration (Article 8)",
    "ramping": "Ramping Needs (Article 9)",
    "ramping_capacity": "Ramping Capacity Requirement",
    "short_term": "Short-Term Flexibility (Article 10)",
    "short_term_by_season": "Short-Term Flexibility by Season",
    "residual_duration": "Residual Load Duration",
    "unavailability": "Resource Unavailability Needs",
    "dso_needs": "DSO Network Needs (Article 11)",
    "tso_needs": "TSO Network Needs (Article 12)",
    "fine_tuning": "Article 14 Fine-Tuning Needs",
}

# Chart PNGs to embed, in display order, with section headings.
_CHART_GROUPS: dict[str, list[str]] = {
    "Deterministic charts": [
        "v2_01_generation_mix.png",
        "v2_02_price_curve.png",
        "v2_03_rldc.png",
        "v2_04_flex_needs.png",
        "v2_05_reserve_margin.png",
        "v2_06_storage_soc.png",
        "v2_07_ramp_matrix.png",
        "v2_08_gantt.png",
        "v2_10_stacked_dispatch_price.png",
    ],
    "Monte Carlo summary charts": [
        "mc_01_cost_distribution.png",
        "mc_02_load_input_bands.png",
        "mc_03_solar_input_bands.png",
        "mc_03b_wind_input_bands.png",
        "mc_04_residual_load_bands.png",
        "mc_05_price_bands.png",
        "mc_06_workflow.png",
    ],
    "Monte Carlo risk charts": [
        "mc_07_lole_histogram.png",
        "mc_08_ens_exceedance_curve.png",
        "mc_09_event_probabilities.png",
        "mc_10_efficiency_frontier.png",
        "mc_11_elcc_placeholder.png",
        "mc_12_ramp_matrix.png",
        "mc_13_dispatch_uncertainty.png",
        "mc_14_reserve_percentiles.png",
        "mc_15_reserve_shortfall_prob.png",
        "mc_16_flex_activation.png",
        "mc_17_price_boxplot.png",
        "mc_18_price_distribution.png",
        "mc_19_correlation_heatmap.png",
    ],
}


def build_markdown_report(
    report_path: Path,
    img_dir: Path,
    fna_tables: dict[str, pd.DataFrame] | None = None,
    target_year: int | None = None,
    max_rows: int = 25,
) -> Path:
    """Write a Markdown report to ``report_path`` and return its path.

    ``fna_tables`` is the dict of indicator DataFrames returned by
    ``build_fna_indicators`` / ``run_optimisation``/``run_postprocess``
    (``results["fna_tables"]``); missing or empty tables are skipped.
    ``img_dir`` is scanned for the chart PNGs listed in ``_CHART_GROUPS``;
    missing charts are skipped silently.
    """

    fna_tables = fna_tables or {}
    img_dir = Path(img_dir)
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Belgium FNA - Run Report")
    lines.append("")
    lines.append(f"- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if target_year is not None:
        lines.append(f"- Target year: {target_year}")
    lines.append(f"- Image directory: `{img_dir}`")
    lines.append("")
    lines.append(
        "> Prototype output - not an official Belgian FNA, not ACER/Elia "
        "endorsed. See the project README for scope and limitations."
    )
    lines.append("")

    lines.append("## ACER FNA Indicators")
    lines.append("")
    any_table = False
    for key, title in _INDICATOR_TABLES.items():
        table = fna_tables.get(key)
        if not isinstance(table, pd.DataFrame) or table.empty:
            continue
        any_table = True
        lines.append(f"### {title}")
        lines.append("")
        shown = table.head(max_rows)
        lines.append(_dataframe_to_markdown(shown))
        if len(table) > max_rows:
            lines.append("")
            lines.append(f"_... {len(table) - max_rows} more rows omitted ..._")
        lines.append("")
    if not any_table:
        lines.append("_No indicator tables available - run `compute-fna-indicators` first._")
        lines.append("")

    for group_title, filenames in _CHART_GROUPS.items():
        existing = [name for name in filenames if (img_dir / name).exists()]
        if not existing:
            continue
        lines.append(f"## {group_title}")
        lines.append("")
        for name in existing:
            rel = Path("..") / "outputs" / "images" / name if "outputs" not in str(img_dir) else (img_dir / name)
            # Prefer a path relative to the report file when possible.
            try:
                rel = Path(img_dir.resolve()) / name
                rel = rel.relative_to(report_path.parent.resolve())
            except ValueError:
                rel = img_dir / name
            title = name.rsplit(".", 1)[0].replace("_", " ").title()
            lines.append(f"### {title}")
            lines.append("")
            lines.append(f"![{title}]({rel.as_posix()})")
            lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote run report to %s", report_path)
    return report_path


def _dataframe_to_markdown(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except ImportError:
        # Fallback if the optional 'tabulate' dependency is unavailable.
        return df.to_csv(index=False)
