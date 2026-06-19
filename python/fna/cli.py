"""
fna.cli
==========
Command-line entry point for the Belgium FNA-ED/UC v3 prototype.

Each command is a thin wrapper over the package's stage modules:
``fna.inputs`` (data fetching + rep-days), ``fna.io`` (Excel I/O +
indicators), ``fna.model`` (deterministic / Monte Carlo run + GAMS) and
``fna.plots`` (charts + report). Heavy modules (GAMS, matplotlib) are
imported lazily inside each command so ``fna --help`` stays fast and
environment-variable overrides (e.g. ``--country`` / ``--year``) are picked up
by ``fna.config`` before it is first imported.

    fna --help                              # or:  python -m fna --help
    fna audit
    fna run -y 2030
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from fna.logging_setup import configure_logging


app = typer.Typer(
    name="fna",
    help="Belgium Flexibility Needs Assessment (FNA) prototype - command-line workflow.",
    no_args_is_help=True,
    add_completion=False,
)

log = logging.getLogger("fna")

# A reusable --workbook/-w option; declared once and shared by every command.
WorkbookOpt = typer.Option(
    None, "--workbook", "-w",
    help="Input workbook filename in data/inputs/excel/ (overrides the EXCEL_FILENAME default for this run).",
)


def _apply_workbook(workbook: Optional[str]) -> None:
    """Select the input workbook for this run, before fna.config is imported.

    Sets EXCEL_FILENAME so ``config`` (and everything reading it) picks up the
    chosen workbook; also patches an already-imported config defensively. Pass
    a bare filename that lives under ``data/inputs/excel/``."""

    if not workbook:
        return
    name = Path(workbook).name
    os.environ["EXCEL_FILENAME"] = name
    if "fna.config" in sys.modules:
        sys.modules["fna.config"].EXCEL_FILENAME = name


# ---------------------------------------------------------------------------
# 1. audit
# ---------------------------------------------------------------------------

@app.command()
def audit(
    save_csv: bool = typer.Option(True, help="Save the data-quality report as a CSV under data/outputs/audit/."),
    workbook: Optional[str] = WorkbookOpt,
) -> None:
    """Run configuration validation plus a data-quality/granularity audit of the input workbook."""

    _apply_workbook(workbook)
    from fna.config import OUTPUTS_DIR, PROJECT_ROOT
    log_file = configure_logging("audit", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    ok = _run_validation_checks(verbose=True)

    from fna.io.indicators.quality import build_granularity_report
    from fna.io.excel import OPTIONAL_SHEETS, SHEETS, read_inputs
    from fna.model.run import _open_workbook

    wb = _open_workbook()
    inputs = read_inputs(wb)
    report = build_granularity_report(inputs["frames"], {**SHEETS, **OPTIONAL_SHEETS})

    typer.echo("\nData-quality / granularity report:")
    typer.echo(report.to_string(index=False))

    if save_csv:
        out_dir = OUTPUTS_DIR / "audit"
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "data_quality_report.csv"
        report.to_csv(csv_path, index=False)
        typer.echo(f"\nSaved data-quality report: {csv_path}")

    typer.echo(
        "\nSee docs/METHODOLOGY.md for the article-by-article compliance "
        "self-assessment and the per-field data source / proxy status."
    )

    if not ok:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# 2. fetch
# ---------------------------------------------------------------------------

@app.command(name="fetch")
def fetch_data(
    country: str = typer.Option(..., "--country", help="ENTSO-E country code, e.g. BE, FR, NL."),
    year: int = typer.Option(..., "--year", help="Calendar year to fetch from ENTSO-E."),
) -> None:
    """Fetch a country/year of hourly ENTSO-E data into data/inputs/raw_<CC><year>/ (needs ENTSOE_API_KEY)."""

    from fna.config import PROJECT_ROOT, raw_data_dir
    log_file = configure_logging("fetch", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    from fna.inputs.fetch import fetch_country_year

    cc = country.upper()
    out_dir = raw_data_dir(cc, year)
    fetch_country_year(cc, year, out_dir)
    typer.echo(f"Done: fetched {cc} {year} into {out_dir}.")


# ---------------------------------------------------------------------------
# 3. build-rep-days
# ---------------------------------------------------------------------------

@app.command(name="build-full-year")
def build_full_year(
    country: str = typer.Option(..., "--country", help="Country code, e.g. BE, FR, NL."),
    year: int = typer.Option(..., "--year", help="Calendar data year to pull from ENTSO-E."),
    template: Optional[str] = typer.Option(None, "--template", help="Workbook in data/inputs/excel/ providing the structural sheets (fleet, flex, interconnectors). Defaults to the current input workbook."),
    fetch: bool = typer.Option(True, "--fetch/--no-fetch", help="Fetch ENTSO-E data if the local cache is missing (needs ENTSOE_API_KEY)."),
) -> None:
    """Generate a full-year (8760 h) input workbook data/inputs/excel/<CC>_FullYear<year>.xlsx.

    Hourly time-series come from ENTSO-E for COUNTRY/YEAR; structural sheets
    (thermal fleet, flex/storage, interconnectors) come from --template. The
    output's assumption sheets/rows are colour-coded amber for you to review.
    """

    from fna.config import EXCEL_DIR, PROJECT_ROOT
    log_file = configure_logging("build-full-year", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    from pathlib import Path as _Path
    from fna.inputs.full_year import build_full_year_workbook

    template_path = (EXCEL_DIR / _Path(template).name) if template else None
    out = build_full_year_workbook(country=country, year=year, template=template_path, do_fetch=fetch)
    typer.echo(f"Done: wrote {out}. Review the amber-flagged sheets, then run "
               f"`build-rep-days --country {country.upper()} --year {year}`.")


@app.command(name="build-rep-days")
def build_rep_days(
    country: str = typer.Option(..., "--country", help="Country code, e.g. BE, FR, NL."),
    year: int = typer.Option(..., "--year", help="Year of the full-year workbook to compress."),
    days: Optional[int] = typer.Option(None, "--days", min=1, help="Number of representative days. Omit to let the elbow method pick the optimal count."),
    seed: int = typer.Option(42, "--seed", help="Random seed for k-means clustering."),
) -> None:
    """Compress data/inputs/excel/<CC>_FullYear<year>.xlsx into a representative-day workbook
    data/inputs/excel/<CC>_RepDays<year>.xlsx.

    Clusters the 365 days on demand + wind + solar shapes into --days
    representative days (each weighted by its cluster size); with --days omitted,
    the optimal count is chosen by the elbow method on clustering inertia.
    """

    from fna.config import PROJECT_ROOT
    log_file = configure_logging("build-rep-days", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    from fna.inputs.rep_days import build_rep_days_workbook

    out = build_rep_days_workbook(country=country, year=year, n_days=days, seed=seed)
    typer.echo(f"Done: wrote {out}. Use it for a run with: "
               f"fna run -w {out.name}")


# ---------------------------------------------------------------------------
# 4. run / run-deterministic
# ---------------------------------------------------------------------------

@app.command(name="run")
@app.command(name="run-deterministic")
def run_deterministic(
    target_year: Optional[int] = typer.Option(None, "--target-year", "-y", help="Target year (e.g. 2025, 2030, 2035). Defaults to 01_Control.target_year."),
    workbook: Optional[str] = WorkbookOpt,
) -> None:
    """Run a single deterministic UC/ED optimisation and write the FNA output workbook."""

    _apply_workbook(workbook)
    from fna.config import PROJECT_ROOT
    log_file = configure_logging("run-deterministic", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    import fna.model.run as main
    from fna.io.excel import read_inputs

    started_at = time.perf_counter()
    wb = main._open_workbook()
    inputs = read_inputs(wb)
    inputs, paths, sheet_suffix = _resolve_year(inputs, target_year)

    # paths is None for the base target year: run_optimisation then mints an
    # isolated data/outputs/runs/<run_id>/ folder + provenance manifest. For an
    # explicit non-base year we keep the per-year folder layout.
    if paths is not None:
        main._ensure_directories(paths)
        main._clean_generated_outputs(paths)

    main.run_optimisation(wb, started_at=started_at, inputs=inputs, paths=paths, sheet_suffix=sheet_suffix, save=True)
    typer.echo(f"Done: deterministic run complete for target year {inputs['target_year']}.")


# ---------------------------------------------------------------------------
# 5. mc / run-monte-carlo
# ---------------------------------------------------------------------------

@app.command(name="mc")
@app.command(name="run-monte-carlo")
def run_monte_carlo_cmd(
    target_year: Optional[int] = typer.Option(None, "--target-year", "-y", help="Target year (e.g. 2025, 2030, 2035). Defaults to 01_Control.target_year."),
    scenarios: Optional[int] = typer.Option(None, "--scenarios", "-n", min=1, help="Number of Monte Carlo scenarios. Defaults to 01_Control.n_mc_scenarios."),
    workbook: Optional[str] = WorkbookOpt,
) -> None:
    """Run the Monte Carlo ensemble (PECD weather scenarios) and write the MC summary sheets/charts."""

    _apply_workbook(workbook)
    from fna.config import PROJECT_ROOT
    log_file = configure_logging("run-monte-carlo", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    import fna.model.run as main
    from fna.io.excel import read_inputs, read_uncertainty_params

    started_at = time.perf_counter()
    wb = main._open_workbook()
    inputs = read_inputs(wb)
    inputs, paths, sheet_suffix = _resolve_year(inputs, target_year)

    mc_params = read_uncertainty_params(wb)
    mc_params["run_monte_carlo"] = True
    if target_year is not None:
        mc_params["pecd_target_year"] = int(inputs["target_year"])
    if scenarios is not None:
        mc_params["n_mc_scenarios"] = scenarios

    # paths is None for the base target year: run_monte_carlo mints an isolated
    # run folder (with per-scenario sub-folders) + manifest. Non-base years keep
    # the per-year folder layout.
    if paths is not None:
        main._ensure_directories(paths)
        main._clean_generated_outputs(paths)

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
    workbook: Optional[str] = WorkbookOpt,
) -> None:
    """Recompute the ACER FNA indicator sheets (40-43, 46) from existing CSV outputs, without re-running GAMS."""

    _apply_workbook(workbook)
    from fna.config import PROJECT_ROOT
    log_file = configure_logging("compute-fna-indicators", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    import fna.model.run as main
    from fna.io.excel import read_inputs

    started_at = time.perf_counter()
    wb = main._open_workbook()
    inputs = read_inputs(wb)
    inputs, paths, sheet_suffix = _resolve_postprocess_paths(inputs, target_year)

    results = main.run_postprocess(wb, started_at=started_at, inputs=inputs, paths=paths, sheet_suffix=sheet_suffix, save=True)
    _export_indicator_tables(results.get("fna_tables", {}), Path(paths["out_dir"]))
    typer.echo(f"Done: FNA indicators recomputed for target year {inputs['target_year']} (run {paths['run_id']}).")


# ---------------------------------------------------------------------------
# 7. fine-tune
# ---------------------------------------------------------------------------

@app.command(name="fine-tune")
def fine_tune(
    target_year: Optional[int] = typer.Option(None, "--target-year", help="Target year matching a previous run. Defaults to 01_Control.target_year."),
    workbook: Optional[str] = WorkbookOpt,
) -> None:
    """Recompute the Article-14 / DSO / TSO network-needs sheets (44, 45, 47) from existing CSV outputs."""

    _apply_workbook(workbook)
    from fna.config import PROJECT_ROOT
    log_file = configure_logging("fine-tune", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    import fna.model.run as main
    from fna.io.excel import read_inputs

    started_at = time.perf_counter()
    wb = main._open_workbook()
    inputs = read_inputs(wb)
    inputs, paths, sheet_suffix = _resolve_postprocess_paths(inputs, target_year)

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
def validate(
    workbook: Optional[str] = WorkbookOpt,
) -> None:
    """Validate configuration, the input workbook schema, and the GAMS/PECD setup (no run)."""

    _apply_workbook(workbook)
    from fna.config import PROJECT_ROOT
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
    workbook: Optional[str] = WorkbookOpt,
) -> None:
    """Build a Markdown summary report bundling FNA indicator tables and charts for a run."""

    _apply_workbook(workbook)
    from fna.config import PROJECT_ROOT
    log_file = configure_logging("make-report", PROJECT_ROOT)
    log.info("Logging to %s", log_file)

    import fna.model.run as main
    from fna.io.excel import read_inputs
    from fna.plots.report import build_markdown_report

    wb = main._open_workbook()
    inputs = read_inputs(wb)
    inputs, paths, sheet_suffix = _resolve_postprocess_paths(inputs, target_year)

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
    """Return ``(inputs, paths, sheet_suffix)`` for a *run* (solve) command.

    For the base target year ``paths`` is None, signalling the run function to
    mint an isolated ``data/outputs/runs/<run_id>/`` folder + provenance
    manifest. For an explicit non-base year we mirror multi_year.py: derive
    per-year inputs/paths (the ``year_<year>`` layout) and suffix output sheets
    with ``_<year>``.
    """

    from fna.config import paths_for_year
    from fna.model.multi_year import _inputs_for_year

    base_year = inputs["target_year"]
    if target_year is None or int(target_year) == int(base_year):
        return inputs, None, ""

    year_inputs = _inputs_for_year(inputs, int(target_year))
    return year_inputs, paths_for_year(int(target_year)), f"_{target_year}"


def _resolve_postprocess_paths(inputs: dict, target_year: Optional[int]):
    """Resolve ``(inputs, paths, sheet_suffix)`` for a *post-process* command
    (compute-fna-indicators / fine-tune / make-report), which reads a previous
    run's outputs rather than solving.

    For the base year this points at the most recent isolated run directory;
    for an explicit year it uses that year's folder."""

    from fna.config import most_recent_run_dir, paths_from_run_dir
    from fna.model.multi_year import _inputs_for_year

    base_year = inputs["target_year"]
    if target_year is None or int(target_year) == int(base_year):
        run_dir = most_recent_run_dir()
        if run_dir is None:
            raise typer.BadParameter(
                "No previous run found under data/outputs/runs/. Run "
                "`run` or `mc` first."
            )
        return inputs, paths_from_run_dir(run_dir), ""

    from fna.config import paths_for_year

    year_inputs = _inputs_for_year(inputs, int(target_year))
    return year_inputs, paths_for_year(int(target_year)), f"_{target_year}"


def _run_validation_checks(verbose: bool) -> bool:
    """Run config + workbook-schema + GAMS/PECD checks. Returns True if all
    *hard* checks pass (config validity, workbook present, required sheets
    present); GAMS/PECD issues are reported as warnings only, since
    `validate`/`audit` should work without a GAMS licence."""

    import fna.config as config
    from fna.config import EXCEL_FILENAME, EXPECTED_INPUT_SHEETS, OPTIONAL_INPUT_SHEETS, MC_DEFAULTS, PROJECT_ROOT, resolve_input_workbook

    ok = True

    cfg_ok, cfg_message = config.validate_config()
    if verbose:
        typer.echo(cfg_message)
    ok = ok and cfg_ok

    wb_path = resolve_input_workbook(EXCEL_FILENAME)
    if not wb_path.exists():
        typer.echo(f"ERROR: input workbook not found: {wb_path}")
        return False
    if verbose:
        typer.echo(f"Input workbook: {wb_path}")

    try:
        from fna.model.run import _open_workbook

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
        from fna.model.gams import _resolve_gams_exe

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
