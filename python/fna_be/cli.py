"""
fna_be.cli
==========
Command-line workflow for the Belgium FNA-ED/UC v3 prototype.

Every command is a thin wrapper around the existing scripts/modules in
``python/`` (``main.py``, ``multi_year.py``, ``rep_days.py``,
``data_quality_report.py``, ``report.py``, ...). Heavy modules (xlwings,
GAMS, matplotlib) are imported lazily inside each command so
``python -m fna_be --help`` stays fast and so environment-variable overrides
(e.g. ``--country`` / ``--year``) are picked up by ``config.py`` before it is
first imported.

Run from the ``python/`` directory:

    python -m fna_be --help
    python -m fna_be audit
    python -m fna_be run-deterministic --target-year 2030
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from fna_be.logging_setup import configure_logging

# python/ (parent of this package) must be importable so we can do
# `import main`, `import config`, etc. - the existing scripts already assume
# python/ is on sys.path when run directly.
_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

app = typer.Typer(
    name="fna_be",
    help="Belgium Flexibility Needs Assessment (FNA) prototype - command-line workflow.",
    no_args_is_help=True,
    add_completion=False,
)

log = logging.getLogger("fna_be")


# ---------------------------------------------------------------------------
# 1. audit
# ---------------------------------------------------------------------------

@app.command()
def audit(
    save_csv: bool = typer.Option(True, help="Save the data-quality report as a CSV under data/outputs/audit/."),
) -> None:
    """Run configuration validation plus a data-quality/granularity audit of the input workbook."""

    from config import PROJECT_ROOT
    log_file = configure_logging("audit", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    ok = _run_validation_checks(verbose=True)

    from data_quality_report import build_granularity_report
    from io_excel import OPTIONAL_SHEETS, SHEETS, read_inputs
    from main import _open_workbook

    wb = _open_workbook()
    inputs = read_inputs(wb)
    report = build_granularity_report(inputs["frames"], {**SHEETS, **OPTIONAL_SHEETS})

    typer.echo("\nData-quality / granularity report:")
    typer.echo(report.to_string(index=False))

    if save_csv:
        out_dir = PROJECT_ROOT / "data" / "outputs" / "audit"
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "data_quality_report.csv"
        report.to_csv(csv_path, index=False)
        typer.echo(f"\nSaved data-quality report: {csv_path}")

    typer.echo(
        "\nSee docs/ACER_FNA_COMPLIANCE_GAP_MATRIX.md for the article-by-article "
        "compliance self-assessment, and docs/BELGIUM_FNA_DATA_SOURCE_MAP.md for "
        "per-field data source / proxy status."
    )

    if not ok:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# 2. refresh-data
# ---------------------------------------------------------------------------

@app.command(name="refresh-data")
def refresh_data(
    country: str = typer.Option("BE", help="ENTSO-E country code to fetch (e.g. BE, FR, NL)."),
    year: int = typer.Option(..., help="Calendar year to fetch from ENTSO-E."),
) -> None:
    """Refresh representative-day inputs from ENTSO-E for COUNTRY / YEAR.

    Sets ENTSOE_COUNTRY_CODE / ENTSOE_DATA_YEAR before running rep_days.py's
    refresh pipeline, so 02_RepHours, 03_RepDays, 05_IntercoProfiles,
    07_RES_Portfolios and 08_RES_CF_Profiles are rebuilt from live data.
    """

    os.environ["ENTSOE_COUNTRY_CODE"] = country.upper()
    os.environ["ENTSOE_DATA_YEAR"] = str(year)

    from config import PROJECT_ROOT
    log_file = configure_logging("refresh-data", PROJECT_ROOT)
    log.info("Logging to %s", log_file)
    log.info("Refreshing representative-day inputs for %s, %s", country.upper(), year)

    import rep_days

    rep_days.main()
    typer.echo(f"Done: refreshed representative-day inputs for {country.upper()} {year}.")


# ---------------------------------------------------------------------------
# 3. build-rep-days
# ---------------------------------------------------------------------------

@app.command(name="build-rep-days")
def build_rep_days(
    clusters: int = typer.Option(..., min=1, help="Number of representative days (k-means clusters) to build."),
) -> None:
    """Rebuild 02_RepHours / 03_RepDays with CLUSTERS representative days.

    Re-runs the same ENTSO-E-based pipeline as refresh-data, with
    REPRESENTATIVE_DAY_CLUSTERS overridden so the k-means clustering produces
    exactly CLUSTERS representative days (uses the country/year already
    configured via ENTSOE_COUNTRY_CODE / ENTSOE_DATA_YEAR / .env unless
    overridden by a prior `refresh-data` call in the same process).
    """

    os.environ["REPRESENTATIVE_DAY_CLUSTERS"] = str(clusters)

    from config import PROJECT_ROOT
    log_file = configure_logging("build-rep-days", PROJECT_ROOT)
    log.info("Logging to %s", log_file)
    log.info("Rebuilding representative days with %d clusters", clusters)

    import rep_days

    rep_days.main()
    typer.echo(f"Done: rebuilt representative days with {clusters} clusters.")


# ---------------------------------------------------------------------------
# 4. run-deterministic
# ---------------------------------------------------------------------------

@app.command(name="run-deterministic")
def run_deterministic(
    target_year: Optional[int] = typer.Option(None, "--target-year", help="Target year (e.g. 2025, 2030, 2035). Defaults to 01_Control.target_year."),
) -> None:
    """Run a single deterministic UC/ED optimisation and write the FNA output workbook."""

    from config import PROJECT_ROOT
    log_file = configure_logging("run-deterministic", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    import main
    from io_excel import read_inputs

    started_at = time.perf_counter()
    wb = main._open_workbook()
    inputs = read_inputs(wb)
    inputs, paths, sheet_suffix = _resolve_year(inputs, target_year)

    main._ensure_directories(paths)
    main._clean_generated_outputs(paths)
    main._reset_output_workbook(wb)

    main.run_optimisation(wb, started_at=started_at, inputs=inputs, paths=paths, sheet_suffix=sheet_suffix, save=True)
    typer.echo(f"Done: deterministic run complete for target year {inputs['target_year']}.")


# ---------------------------------------------------------------------------
# 5. run-monte-carlo
# ---------------------------------------------------------------------------

@app.command(name="run-monte-carlo")
def run_monte_carlo_cmd(
    target_year: Optional[int] = typer.Option(None, "--target-year", help="Target year (e.g. 2025, 2030, 2035). Defaults to 01_Control.target_year."),
    scenarios: Optional[int] = typer.Option(None, "--scenarios", min=1, help="Number of Monte Carlo scenarios. Defaults to 01_Control.n_mc_scenarios."),
) -> None:
    """Run the Monte Carlo ensemble (PECD weather scenarios) and write the MC summary sheets/charts."""

    from config import PROJECT_ROOT
    log_file = configure_logging("run-monte-carlo", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    import main
    from io_excel import read_inputs, read_uncertainty_params

    started_at = time.perf_counter()
    wb = main._open_workbook()
    inputs = read_inputs(wb)
    inputs, paths, sheet_suffix = _resolve_year(inputs, target_year)

    mc_params = read_uncertainty_params(wb)
    mc_params["run_monte_carlo"] = True
    if scenarios is not None:
        mc_params["n_mc_scenarios"] = scenarios

    main._ensure_directories(paths)
    main._clean_generated_outputs(paths)
    main._reset_output_workbook(wb)

    main.run_monte_carlo(
        wb, mc_params=mc_params, started_at=started_at,
        inputs_base=inputs, paths=paths, sheet_suffix=sheet_suffix, save=True,
    )
    typer.echo(
        f"Done: Monte Carlo run complete for target year {inputs['target_year']} "
        f"with {mc_params['n_mc_scenarios']} scenarios."
    )


# ---------------------------------------------------------------------------
# 6. compute-fna-indicators
# ---------------------------------------------------------------------------

@app.command(name="compute-fna-indicators")
def compute_fna_indicators(
    target_year: Optional[int] = typer.Option(None, "--target-year", help="Target year matching a previous run. Defaults to 01_Control.target_year."),
) -> None:
    """Recompute the ACER FNA indicator sheets (40-43, 46) from existing CSV outputs, without re-running GAMS."""

    from config import PROJECT_ROOT
    log_file = configure_logging("compute-fna-indicators", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    import main
    from io_excel import read_inputs

    started_at = time.perf_counter()
    wb = main._open_workbook()
    inputs = read_inputs(wb)
    inputs, paths, sheet_suffix = _resolve_year(inputs, target_year)

    results = main.run_postprocess(wb, started_at=started_at, inputs=inputs, paths=paths, sheet_suffix=sheet_suffix, save=True)
    _export_indicator_tables(results.get("fna_tables", {}), Path(paths["out_dir"]))
    typer.echo(f"Done: FNA indicators recomputed for target year {inputs['target_year']}.")


# ---------------------------------------------------------------------------
# 7. fine-tune
# ---------------------------------------------------------------------------

@app.command(name="fine-tune")
def fine_tune(
    target_year: Optional[int] = typer.Option(None, "--target-year", help="Target year matching a previous run. Defaults to 01_Control.target_year."),
) -> None:
    """Recompute the Article-14 / DSO / TSO network-needs sheets (44, 45, 47) from existing CSV outputs."""

    from config import PROJECT_ROOT
    log_file = configure_logging("fine-tune", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    import main
    from io_excel import read_inputs

    started_at = time.perf_counter()
    wb = main._open_workbook()
    inputs = read_inputs(wb)
    inputs, paths, sheet_suffix = _resolve_year(inputs, target_year)

    if not int(inputs.get("control", {}).get("use_network", 0) or 0):
        log.warning(
            "use_network=0 in 01_Control: the Article-14 network constraints were "
            "not active in the optimisation, so the GAMS network.csv output may be "
            "empty. DSO needs (sheet 44) are still computed from residual load."
        )

    results = main.run_postprocess(wb, started_at=started_at, inputs=inputs, paths=paths, sheet_suffix=sheet_suffix, save=True)
    fna_tables = results.get("fna_tables", {})
    _export_indicator_tables(fna_tables, Path(paths["out_dir"]))

    for key, sheet in [("dso_needs", "44_FNA_DSO_Needs"), ("tso_needs", "45_FNA_TSO_Needs"), ("fine_tuning", "47_FNA_FineTuning_Art14")]:
        table = fna_tables.get(key)
        n_rows = 0 if table is None else len(table)
        typer.echo(f"  {sheet}{sheet_suffix}: {n_rows} rows")

    typer.echo(f"Done: Article-14 / network fine-tuning needs recomputed for target year {inputs['target_year']}.")


# ---------------------------------------------------------------------------
# 8. validate
# ---------------------------------------------------------------------------

@app.command()
def validate() -> None:
    """Validate configuration, the input workbook schema, and the GAMS/PECD setup (no run)."""

    from config import PROJECT_ROOT
    log_file = configure_logging("validate", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    ok = _run_validation_checks(verbose=True)
    if ok:
        typer.echo("\nValidation passed.")
    else:
        typer.echo("\nValidation failed - see errors above.")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# 9. make-report
# ---------------------------------------------------------------------------

@app.command(name="make-report")
def make_report(
    target_year: Optional[int] = typer.Option(None, "--target-year", help="Target year matching a previous run. Defaults to 01_Control.target_year."),
) -> None:
    """Build a Markdown summary report bundling FNA indicator tables and charts for a run."""

    from config import PROJECT_ROOT
    log_file = configure_logging("make-report", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    import main
    from io_excel import read_inputs
    from report import build_markdown_report

    wb = main._open_workbook()
    inputs = read_inputs(wb)
    inputs, paths, sheet_suffix = _resolve_year(inputs, target_year)

    out_dir = Path(paths["out_dir"])
    fna_tables = _load_indicator_tables(out_dir)
    if not fna_tables:
        log.warning("No exported FNA indicator tables found under %s; run `compute-fna-indicators` first for a fuller report.", out_dir / "fna_tables")

    report_path = out_dir / f"FNA_Report{sheet_suffix}.md"
    build_markdown_report(report_path, img_dir=Path(paths["img_dir"]), fna_tables=fna_tables, target_year=inputs["target_year"])
    typer.echo(f"Done: report written to {report_path}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _resolve_year(inputs: dict, target_year: Optional[int]):
    """Return (inputs, paths, sheet_suffix) for `target_year`.

    If `target_year` is None or equal to the workbook's base target year,
    runs against the base PATHS with no sheet suffix. Otherwise mirrors
    multi_year.py: derives per-year inputs/paths and suffixes output sheets
    with `_<year>`.
    """

    from config import PATHS, paths_for_year
    from multi_year import _inputs_for_year

    base_year = inputs["target_year"]
    if target_year is None or int(target_year) == int(base_year):
        return inputs, PATHS, ""

    year_inputs = _inputs_for_year(inputs, int(target_year))
    return year_inputs, paths_for_year(int(target_year)), f"_{target_year}"


def _run_validation_checks(verbose: bool) -> bool:
    """Run config + workbook-schema + GAMS/PECD checks. Returns True if all
    *hard* checks pass (config validity, workbook present, required sheets
    present); GAMS/PECD issues are reported as warnings only, since
    `validate`/`audit` should work without a GAMS licence."""

    import config
    from config import EXCEL_FILENAME, EXPECTED_INPUT_SHEETS, OPTIONAL_INPUT_SHEETS, MC_DEFAULTS, PROJECT_ROOT

    ok = True

    cfg_ok, cfg_message = config.validate_config()
    if verbose:
        typer.echo(cfg_message)
    ok = ok and cfg_ok

    wb_path = PROJECT_ROOT / "excel" / EXCEL_FILENAME
    if not wb_path.exists():
        typer.echo(f"ERROR: input workbook not found: {wb_path}")
        return False
    if verbose:
        typer.echo(f"Input workbook: {wb_path}")

    try:
        from main import _open_workbook

        wb = _open_workbook()
        sheet_names = set(wb.sheetnames)
    except Exception as exc:
        typer.echo(f"ERROR: could not open input workbook: {exc}")
        return False

    missing_required = [name for name in EXPECTED_INPUT_SHEETS if name not in sheet_names]
    if missing_required:
        typer.echo(f"ERROR: missing required input sheets: {missing_required}")
        ok = False
    elif verbose:
        typer.echo(f"All {len(EXPECTED_INPUT_SHEETS)} required input sheets present.")

    missing_optional = [name for name in OPTIONAL_INPUT_SHEETS if name not in sheet_names]
    if missing_optional and verbose:
        typer.echo(f"Note: optional sheets not present (will be treated as empty): {missing_optional}")

    try:
        from run_gams import _resolve_gams_exe

        gams_exe = _resolve_gams_exe()
        if verbose:
            typer.echo(f"GAMS executable: {gams_exe}")
    except Exception as exc:
        typer.echo(f"WARNING: GAMS executable not resolvable ({exc}); model runs will fail.")

    if MC_DEFAULTS.get("use_pecd_data"):
        pecd_dir = Path(MC_DEFAULTS["pecd_data_dir"])
        if not pecd_dir.exists() or not any(pecd_dir.iterdir()):
            typer.echo(f"WARNING: PECD data directory missing or empty: {pecd_dir} (Monte Carlo runs will fail).")
        elif verbose:
            typer.echo(f"PECD data directory: {pecd_dir}")

    return ok


def _export_indicator_tables(fna_tables: dict, out_dir: Path) -> Path:
    """Dump every FNA indicator/network-need table to CSV under
    `<out_dir>/fna_tables/`, so `make-report` can pick them up without
    re-running the model."""

    export_dir = out_dir / "fna_tables"
    export_dir.mkdir(parents=True, exist_ok=True)
    for key, table in fna_tables.items():
        if table is None or table.empty:
            continue
        table.to_csv(export_dir / f"{key}.csv", index=False)
    log.info("Exported %d FNA indicator tables to %s", len(fna_tables), export_dir)
    return export_dir


def _load_indicator_tables(out_dir: Path) -> dict:
    import pandas as pd

    export_dir = out_dir / "fna_tables"
    tables: dict[str, "pd.DataFrame"] = {}
    if not export_dir.exists():
        return tables
    for csv_path in export_dir.glob("*.csv"):
        try:
            tables[csv_path.stem] = pd.read_csv(csv_path)
        except Exception as exc:
            log.warning("Could not read %s: %s", csv_path, exc)
    return tables


if __name__ == "__main__":
    app()
