"""
multi_year.py - Belgium FNA-ED/UC v3
=====================================
Runs the full FNA workflow (deterministic + Monte Carlo) once per FNA target
year, then builds a cross-year comparison.

Target years
------------
- The "base" year = 01_Control.target_year (normally 2025, i.e. "current").
- 2030, if any input sheet (07_RES_Portfolios, 06_DispatchableBlocks,
  09_FlexStorage, 04_Interconnectors) has ``*_2030`` capacity columns.
- 2035, on the same condition - skipped if no ``*_2035`` columns exist yet.

For years where 02_RepHours has no ``gross_demand_mw_<year>`` column (e.g.
2030 in the current workbook, which only ships capacities), demand is
derived from the base year via 01_Control's
``demand_scaling_factor_<year>`` (default 1.0, i.e. flat demand). Add an
explicit ``gross_demand_mw_<year>`` column to 02_RepHours, or a
``demand_scaling_factor_<year>`` row to 01_Control, to override this.

Per-year outputs
-----------------
- GAMS include files / CSV outputs / images / logs: ``data/.../year_<year>/``
  (see config.paths_for_year).
- Excel sheets: every sheet written by write_results / write_mc_results_to_excel
  / write_mc_charts_to_excel gets a ``_<year>`` suffix, e.g.
  ``33_Residual_2030``, ``16_FNA_ShortTerm_2035``, ``50_MC_Summary_2030``.
- A cross-year comparison table (``60_CrossYear_Comparison``) and trend
  charts (``61_CrossYear_Charts``) are written once, after all years.
"""
from __future__ import annotations

import copy
import logging
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd


from fna_be.config import EXCEL_FILENAME, PATHS, PROJECT_ROOT, output_excel_path, paths_for_year
from fna_be.plots.cross_year import collect_year_metrics, write_cross_year_to_excel
from fna_be.io.excel import _num, read_inputs, read_uncertainty_params
from fna_be.model.run import (
    _clean_generated_outputs,
    _configure_logging,
    _ensure_directories,
    _open_output_workbook,
    _open_workbook,
    _reset_output_workbook,
    run_monte_carlo,
    run_optimisation,
)

log = logging.getLogger("multi_year")

CANDIDATE_FUTURE_YEARS: list[int] = [2030, 2035]


def main() -> None:
    started_at = time.perf_counter()
    wb = _open_workbook()
    _configure_logging(PROJECT_ROOT / "logs" / "run_multi_year.log")
    try:
        run_all_years(wb, started_at=started_at)
    finally:
        elapsed = time.perf_counter() - started_at
        log.info("Total multi_year.py runtime: %.0fs", elapsed)


def run_all_years(wb: Any | None = None, started_at: float | None = None) -> dict[int, dict[str, Any]]:
    """Run the deterministic + Monte Carlo workflow for every available target year.

    Returns ``{year: {"results": ..., "mc_results": ..., "metrics": ...}}``.
    """

    wb = wb or _open_workbook()
    base_inputs = read_inputs(wb)
    mc_params = read_uncertainty_params(wb)
    run_mc = bool(mc_params.get("run_monte_carlo", False))

    years = _available_target_years(base_inputs)
    log.info("Multi-year run: target years = %s (Monte Carlo %s)", years, "enabled" if run_mc else "disabled")

    _clean_generated_outputs(PATHS)
    for year in years:
        _clean_generated_outputs(paths_for_year(year))
    cross_year_img_dir = PROJECT_ROOT / "data" / "outputs" / "cross_year" / "images"
    if cross_year_img_dir.exists():
        for image in cross_year_img_dir.glob("*.png"):
            try:
                image.unlink()
            except OSError:
                pass
    _reset_output_workbook(wb)

    year_outputs: dict[int, dict[str, Any]] = {}
    for year in years:
        log.info("=" * 70)
        log.info("Target year %s", year)
        inputs = _inputs_for_year(base_inputs, year)
        paths = paths_for_year(year)
        _ensure_directories(paths)
        sheet_suffix = f"_{year}"

        det_results = run_optimisation(
            wb, started_at=started_at, inputs=inputs, paths=paths, sheet_suffix=sheet_suffix, save=False,
        )

        mc_results: dict[int, dict] | None = None
        if run_mc:
            mc_results = run_monte_carlo(
                wb, mc_params=mc_params, started_at=started_at,
                inputs_base=inputs, paths=paths, sheet_suffix=sheet_suffix, save=False,
            )

        metrics = collect_year_metrics(
            year, det_results, det_results.get("fna_tables"), inputs["frames"].get("hours"), mc_results,
        )
        year_outputs[year] = {"results": det_results, "mc_results": mc_results, "metrics": metrics}

    year_metrics = {year: out["metrics"] for year, out in year_outputs.items()}
    out_wb = _open_output_workbook(wb)
    write_cross_year_to_excel(out_wb, year_metrics, img_dir=PROJECT_ROOT / "data" / "outputs" / "cross_year" / "images")

    out_wb.save(output_excel_path())
    log.info("Multi-year run complete for years %s. Output workbook saved to %s.", years, output_excel_path())
    return year_outputs


def _available_target_years(base_inputs: dict[str, Any]) -> list[int]:
    """Base year plus any of CANDIDATE_FUTURE_YEARS that have capacity data."""

    base_year = int(base_inputs["target_year"])
    frames = base_inputs["frames"]
    capacity_sheets = ["res", "dispatchable", "flex", "borders"]

    years = [base_year]
    for year in CANDIDATE_FUTURE_YEARS:
        if year == base_year:
            continue
        suffix = f"_{year}"
        found = any(
            any(str(col).endswith(suffix) for col in frames[key].columns)
            for key in capacity_sheets
            if key in frames and isinstance(frames[key], pd.DataFrame) and not frames[key].empty
        )
        if found:
            years.append(year)
        else:
            log.info("Skipping target year %s: no '*%s' capacity columns found in input sheets.", year, suffix)
    return years


def _inputs_for_year(base_inputs: dict[str, Any], year: int) -> dict[str, Any]:
    """Deep-copy the base inputs, point target_year at `year`, and make sure
    02_RepHours has a gross_demand_mw_<year> column (deriving it by scaling
    the base year's demand if it is missing)."""

    inputs = copy.deepcopy(base_inputs)
    base_year = int(base_inputs["target_year"])
    inputs["target_year"] = year
    inputs["control"]["target_year"] = year

    hours = inputs["frames"]["hours"]
    demand_col = f"gross_demand_mw_{year}"
    if demand_col not in hours.columns:
        base_col = f"gross_demand_mw_{base_year}"
        if base_col not in hours.columns:
            raise KeyError(f"02_RepHours has neither {demand_col} nor {base_col}.")
        factor = _num(inputs["control"].get(f"demand_scaling_factor_{year}", 1.0)) or 1.0
        hours = hours.copy()
        hours[demand_col] = pd.to_numeric(hours[base_col], errors="coerce") * factor
        inputs["frames"]["hours"] = hours
        log.info(
            "Derived %s = %s x demand_scaling_factor_%s (%.4f). "
            "Add an explicit %s column to 02_RepHours or a demand_scaling_factor_%s row to "
            "01_Control to override.",
            demand_col, base_col, year, factor, demand_col, year,
        )
    return inputs


if __name__ == "__main__":
    main()
