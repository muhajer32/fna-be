"""
config.py - Belgium FNA-ED/UC v3
================================
Single configuration file for the three workflows:
    1. Representative-day refresh from ENTSO-E (rep_days.py).
    2. Deterministic Excel -> GAMS -> Excel runs (main.py).
    3. Monte Carlo scenario runs (main.py + monte_carlo.py).

Principle: nothing project-specific is hard-coded as a magic number here.
Run choices (target year, switches, MC options) live in 01_Control in Excel;
secrets and machine paths come from environment variables or a local .env.
Values below are only fallbacks used when Excel/env do not supply them.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# config.py lives at python/fna/config.py, so the repo root is three levels up.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent

def _load_local_env(path: Path) -> None:
    """Load simple KEY=value entries without requiring python-dotenv."""

    try:
        from dotenv import load_dotenv

        load_dotenv(path)
        return
    except Exception:
        pass

    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# Load local settings from .venv/.env. Keeps secrets and machine paths inside
# the local virtual-environment folder.
_load_local_env(PROJECT_ROOT / ".venv" / ".env")


def _env_int(name: str, default: int | None = None) -> int | None:
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def _env_float(name: str, default: float | None = None) -> float | None:
    value = os.environ.get(name)
    return float(value) if value not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _env_list(name: str) -> list[str] | None:
    value = os.environ.get(name)
    if not value:
        return None
    return [item.strip().upper() for item in value.split(",") if item.strip()]


# ============================================================================
# Global project settings
# ============================================================================

# Main workbook used by both the representative-day refresh and model runs.
#EXCEL_FILENAME: str = os.environ.get("EXCEL_FILENAME", "Belgium_FNA_ED_v2_input_data.xlsx")
EXCEL_FILENAME: str = os.environ.get("EXCEL_FILENAME", "BE_FullYear2023.xlsx")


# Workbook input sheets read by the deterministic model.
EXPECTED_INPUT_SHEETS: list[str] = [
    "01_Control",
    "02_RepHours",
    "03_RepDays",
    "04_Interconnectors",
    "05_IntercoProfiles",
    "06_DispatchableBlocks",
    "07_RES_Portfolios",
    "08_RES_CF_Profiles",
    "09_FlexStorage",
    "10_FlexAvailability",
    "11_Availability_Outages",
    "12_Reserve_ForecastError",
    "13_NetworkNeeds",
]

# Optional v3.1 input sheets. Read as empty frames when absent (older
# workbooks); enable the DSO/unavailability/barriers indicator sheets when
# present. See io_excel.OPTIONAL_SHEETS and migrate_v3_1_workbook.py.
OPTIONAL_INPUT_SHEETS: list[str] = [
    "13b_DSO_Zones",
    "10b_Prequalification_Log",
    "17_Barriers_Digitalisation",
]


# ============================================================================
# Paths
# ============================================================================

DATA_DIR: Path = PROJECT_ROOT / "data"
INPUTS_DIR: Path = DATA_DIR / "inputs"
OUTPUTS_DIR: Path = DATA_DIR / "outputs"
EXCEL_DIR: Path = INPUTS_DIR / "excel"
SAMPLE_DATA_DIR: Path = INPUTS_DIR / "sample"
PECD_DATA_DIR: Path = INPUTS_DIR / "pecd"
CSV_OUTPUT_DIR_NAME: str = "csv"


def raw_data_dir(country: str, year: int) -> Path:
    """Canonical cache directory for raw ENTSO-E country/year CSVs."""

    return INPUTS_DIR / f"raw_{str(country).strip().lower()}{int(year)}"


def csv_output_dir(run_dir: str | Path) -> Path:
    """Folder containing GAMS CSV outputs for a run directory."""

    return Path(run_dir) / CSV_OUTPUT_DIR_NAME


def resolve_input_workbook(filename: str | Path | None = None) -> Path:
    """Resolve an input workbook under ``data/inputs/excel`` unless absolute."""

    name = filename or EXCEL_FILENAME
    path = Path(name).expanduser()
    return path if path.is_absolute() else EXCEL_DIR / path.name


PATHS: dict[str, Path] = {
    "gms_file": PROJECT_ROOT / "gams" / "uc_ed_model_v3.gms",
    "inc_dir": INPUTS_DIR,
    "out_dir": OUTPUTS_DIR,
    "log_file": PROJECT_ROOT / "logs" / "gams_run.log",
    "img_dir": OUTPUTS_DIR / "images",
}


def output_excel_path(input_filename: str | None = None) -> Path:
    """Path of the separate output workbook for ``input_filename`` (default
    EXCEL_FILENAME): ``data/outputs/<input stem>-output.xlsx``.

    All sheets written by main.py / multi_year.py (results, FNA indicators,
    charts, MC summaries, cross-year comparison) go into this workbook, kept
    separate from the (read-only) input workbook to avoid macOS Excel issues
    with very large multi-sheet files.
    """

    stem = Path(input_filename or EXCEL_FILENAME).stem
    return OUTPUTS_DIR / f"{stem}-output.xlsx"


def resolve_gms_path(control: dict | None = None) -> Path:
    """Resolve which GAMS model file to run.

    Selectable per run via ``01_Control.gams_model_file`` (a bare filename
    resolved under ``gams/``, or an absolute path). Falls back to the
    ``GAMS_MODEL_FILE`` env var, then the default ``uc_ed_model_v3.gms``.
    """

    name = ((control or {}).get("gams_model_file")
            or os.environ.get("GAMS_MODEL_FILE")
            or "uc_ed_model_v3.gms")
    p = Path(str(name).strip()).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / "gams" / p.name


def runs_root() -> Path:
    """Root folder under which every isolated run directory lives."""
    return OUTPUTS_DIR / "runs"


def make_run_paths(
    run_id: str,
    input_filename: str | None = None,
) -> dict[str, Path]:
    """Per-run copy of PATHS, rooted under ``data/outputs/runs/<run_id>/``.

    Every artefact a run produces - GAMS include files, CSV outputs, per-scenario
    folders, charts, logs and the output workbook - lands inside this single
    folder, so deterministic runs, Monte Carlo runs and re-runs with different
    settings never overwrite each other. ``run_id`` is minted by
    ``run_metadata.new_run_id`` (short timestamp + mode code + short year +
    input stem).

    Returns the same keys as ``PATHS`` plus ``run_dir``, ``run_id`` and
    ``output_xlsx`` (the per-run output workbook path). Monte Carlo scenario
    folders nest under ``<run_dir>/scenarios/scenario_<n>/``.
    """

    return paths_from_run_dir(runs_root() / run_id, input_filename)


def paths_from_run_dir(run_dir: Path, input_filename: str | None = None) -> dict[str, Path]:
    """Build the run-rooted path dict for an *existing* run folder. Used to
    re-run post-processing (compute-fna-indicators / fine-tune / make-report)
    against a previous run's outputs without re-solving."""

    run_dir = Path(run_dir)
    return {
        "gms_file": PATHS["gms_file"],
        "inc_dir": run_dir / "inc",
        "out_dir": run_dir,
        "log_file": run_dir / "gams_run.log",
        "img_dir": run_dir / "images",
        "run_dir": run_dir,
        "run_id": run_dir.name,
        "output_xlsx": run_dir / f"{run_dir.name}-output.xlsx",
    }


def most_recent_run_dir() -> Path | None:
    """Resolve the newest concrete run directory under ``data/outputs/runs``."""

    root = runs_root()
    if not root.exists():
        return None
    candidates = [
        path for path in root.iterdir()
        if path.is_dir() and path.name != "index" and not path.name.startswith(".")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def paths_for_year(year: int) -> dict[str, Path]:
    """Per-target-year copy of PATHS, rooted under a `year_<year>` subfolder.

    Used by the multi-year run (multi_year.py) so each target year's GAMS
    include files, CSV outputs, images and logs land in their own folder
    without disturbing the single-year PATHS layout.
    """

    out_dir = OUTPUTS_DIR / f"year_{year}"
    return {
        "gms_file": PATHS["gms_file"],
        "inc_dir": INPUTS_DIR / f"year_{year}",
        "out_dir": out_dir,
        "log_file": PROJECT_ROOT / "logs" / f"run_{year}.log",
        "img_dir": out_dir / "images",
    }


# ============================================================================
# GAMS runtime
# ============================================================================

GAMS_EXE: str = os.environ.get("GAMS_EXE", "gams")
GAMS_TIMEOUT: int = int(os.environ.get("GAMS_TIMEOUT", "1200"))

EXPECTED_CSV_OUTPUTS: list[str] = [
    "dispatch.csv",
    "fna_indicators.csv",
    "price.csv",
    "residual.csv",
    "storage.csv",
    "reserve.csv",
    "network.csv",
]


# ============================================================================
# Representative-day ENTSO-E refresh
# ============================================================================
# The API key is a secret: it must be provided via ENTSOE_API_KEY (env or .env).
# There is intentionally no default key in source.

ENTSOE_API_KEY: str = os.environ.get("ENTSOE_API_KEY", "")
ENTSOE_COUNTRY_CODE: str = os.environ.get("ENTSOE_COUNTRY_CODE", "BE")
ENTSOE_TIMEZONE: str = os.environ.get("ENTSOE_TIMEZONE", "Europe/Brussels")

# When None, rep_days.py reads target_year / entsoe_data_year from 01_Control.
ENTSOE_DATA_YEAR: int | None = _env_int("ENTSOE_DATA_YEAR")
ENTSOE_NEIGHBOURS: list[str] | None = _env_list("ENTSOE_NEIGHBOURS")

# Cluster count is env/Excel-driven again. When None, rep_days.py uses the
# number of rows already in 03_RepDays.
REPRESENTATIVE_DAY_CLUSTERS: int | None = _env_int("REPRESENTATIVE_DAY_CLUSTERS")

REPRESENTATIVE_DAY_RANDOM_SEED: int = int(os.environ.get("REPRESENTATIVE_DAY_RANDOM_SEED", "42"))
REPRESENTATIVE_DAY_AVAILABILITY_PCT: float = float(
    os.environ.get("REPRESENTATIVE_DAY_AVAILABILITY_PCT", "0.98")
)
ENTSOE_WIND_FALLBACK_SHARE: float = float(os.environ.get("ENTSOE_WIND_FALLBACK_SHARE", "0.70"))
REPRESENTATIVE_DAY_SOURCE_ID: str = os.environ.get("REPRESENTATIVE_DAY_SOURCE_ID", "ENTSOE_CLUSTER")

# Number of calendar days the representative days should add up to. Weights are
# rescaled to this so weighted annual demand reflects a full year (365), not the
# 362 complete days left after dropping daylight-saving-irregular days.
REPRESENTATIVE_DAY_TARGET_DAYS: float = float(
    os.environ.get("REPRESENTATIVE_DAY_TARGET_DAYS", "365")
)

REPRESENTATIVE_DAY_SHEETS: dict[str, str] = {
    "control": "01_Control",
    "hours": "02_RepHours",
    "days": "03_RepDays",
    "borders": "04_Interconnectors",
    "res": "07_RES_Portfolios",
    "res_profiles": "08_RES_CF_Profiles",
}

ENTSOE_BORDER_TO_COUNTRY: dict[str, str] = {
    "FR": "FR", "FRANCE": "FR",
    "NL": "NL", "NETHERLANDS": "NL",
    "DE": "DE", "GERMANY": "DE", "GERMANY_ALEGRO": "DE",
    "LU": "LU", "LUXEMBOURG": "LU",
    "GB": "GB", "UK": "GB", "UNITED_KINGDOM": "GB",
}


# ============================================================================
# Monte Carlo defaults (fallbacks; 01_Control overrides these)
# ============================================================================

MC_CONFIG_SHEET: str = "01_Control"

MC_DEFAULTS: dict[str, Any] = {
    "run_monte_carlo": _env_bool("RUN_MONTE_CARLO", False),
    "n_mc_scenarios": _env_int("N_MC_SCENARIOS", 100),
    "seed_random": _env_int("SEED_RANDOM"),
    "use_pecd_data": _env_bool("USE_PECD_DATA", True),
    "pecd_data_dir": os.environ.get("PECD_DATA_DIR") or PECD_DATA_DIR,
    "pecd_target_year": _env_int("PECD_TARGET_YEAR"),
    "wind_capacity_mw": _env_float("WIND_CAPACITY_MW"),
    "max_parallel_workers": _env_int("MAX_PARALLEL_WORKERS", 4),
    "voll_eur_per_mwh": _env_float("VOLL_EUR_PER_MWH", 10000.0),
    "reserve_slack_penalty_eur_per_mw": _env_float("RESERVE_SLACK_PENALTY_EUR_PER_MW", 5000.0),
}

WIND_RESOURCE_IDS: list[str] | None = _env_list("WIND_RESOURCE_IDS")
WIND_CF_MIN: float = float(os.environ.get("WIND_CF_MIN", "0.0"))
WIND_CF_MAX: float = float(os.environ.get("WIND_CF_MAX", "1.0"))

PECD_COUNTRY_CODE: str = os.environ.get("PECD_COUNTRY_CODE", ENTSOE_COUNTRY_CODE)
PECD_YEARS: list[int] = [
    int(year.strip())
    for year in os.environ.get("PECD_YEARS", "").split(",")
    if year.strip()
]
PECD_FORECAST_HOURS: list[int] = list(range(24))


# ============================================================================
# ACER short-term flexibility (percentile method)
# ============================================================================
# Short-term needs are derived from residual-load forecast-error percentiles.
# These percentiles and the historical-error std assumptions live in Excel
# (12_Reserve_ForecastError); the values here are only defaults.

SHORTTERM_UP_PERCENTILE: float = float(os.environ.get("SHORTTERM_UP_PERCENTILE", "99.9"))
SHORTTERM_DN_PERCENTILE: float = float(os.environ.get("SHORTTERM_DN_PERCENTILE", "0.1"))


# ============================================================================
# Plotting and logging
# ============================================================================

CHART_DPI: int = int(os.environ.get("CHART_DPI", "200"))
CHART_STYLE: str = os.environ.get("CHART_STYLE", "seaborn-v0_8")
COLOR_PALETTE: dict[str, str] = {
    "p5": "#1e40af",
    "p25": "#3b82f6",
    "p50": "#0ea5e9",
    "p75": "#06b6d4",
    "p95": "#ec4899",
    "mean": "#111827",
    "dispatch": "#059669",
    "price": "#d97706",
    "reserve": "#2563eb",
}

LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
LOG_TO_FILE: bool = _env_bool("LOG_TO_FILE", True)
LOG_TO_CONSOLE: bool = _env_bool("LOG_TO_CONSOLE", True)


# ============================================================================
# Run control / advanced
# ============================================================================

ENFORCE_RESERVE_SOFT_CONSTRAINT: bool = _env_bool("ENFORCE_RESERVE_SOFT_CONSTRAINT", True)
RAMP_UNCERTAINTY_SCALING: float = float(os.environ.get("RAMP_UNCERTAINTY_SCALING", "1.5"))

DEBUG_MODE: bool = _env_bool("DEBUG_MODE", False)
SKIP_GAMS: bool = _env_bool("SKIP_GAMS", False)
DRY_RUN: bool = _env_bool("DRY_RUN", False)


# ============================================================================
# Validation
# ============================================================================

def validate_config() -> tuple[bool, str]:
    """Check project-level settings that do not need Excel."""

    errors: list[str] = []

    if not (0 <= WIND_CF_MIN <= WIND_CF_MAX <= 1):
        errors.append(f"Invalid wind CF bounds: min={WIND_CF_MIN}, max={WIND_CF_MAX}")
    if int(MC_DEFAULTS["n_mc_scenarios"]) < 1:
        errors.append(f"n_mc_scenarios must be >= 1, got {MC_DEFAULTS['n_mc_scenarios']}")
    if int(MC_DEFAULTS["max_parallel_workers"]) < 1:
        errors.append("max_parallel_workers must be >= 1")
    if not (0 < SHORTTERM_DN_PERCENTILE < SHORTTERM_UP_PERCENTILE < 100):
        errors.append("Short-term percentiles must satisfy 0 < dn < up < 100.")

    if errors:
        return False, "\n".join(["Configuration errors:"] + errors)
    return True, "Configuration valid"


if __name__ == "__main__":
    ok, message = validate_config()
    print(message)
    if ok:
        print("\n=== Configuration summary ===")
        print(f"Project root: {PROJECT_ROOT}")
        print(f"Excel workbook: {PROJECT_ROOT / 'excel' / EXCEL_FILENAME}")
        print(f"GAMS model: {PATHS['gms_file']}")
        print(f"GAMS executable: {GAMS_EXE}")
        print(f"ENTSO-E key set: {'yes' if ENTSOE_API_KEY else 'no (set ENTSOE_API_KEY)'}")
