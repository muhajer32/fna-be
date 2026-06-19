"""
run_metadata.py - Belgium FNA-ED/UC v3
=======================================
Run provenance + compute-resource capture for every model run.

Two problems this module solves:

1. **Reproducibility / provenance.** Every run records *what* was run (mode,
   target year, input workbook, key 01_Control settings, git commit) and *where*
   (CPU / GPU / OS / RAM / Python / GAMS versions), plus *when* and *how long*
   (start/end timestamps and wall-clock seconds, for the whole run and for each
   Monte Carlo scenario, including GAMS solve time).

2. **Output isolation.** Each run gets a unique ``run_id`` so its CSVs, charts,
   logs and output workbook land in their own ``data/outputs/runs/<run_id>/``
   folder instead of overwriting the previous run. See ``config.make_run_paths``.

The manifest is written three ways:
    - ``run_metadata.json`` inside the run folder (machine-readable, full detail);
    - one row appended to ``data/outputs/runs/runs_index.csv`` (cross-run registry);
    - a ``99_Run_Metadata`` sheet in the output workbook (human-readable).

Nothing here imports GAMS or Excel at module load; heavy/optional probes
(GPU, GAMS version) degrade gracefully so a run is never aborted by metadata.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Timezone-aware 'now' (local time, with offset)."""
    return datetime.now().astimezone()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt is not None else None


def utc_stamp() -> str:
    """Compact UTC timestamp used in run ids: ``20260618-173045Z``."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def new_run_id(mode: str, target_year: int | str | None, input_stem: str) -> str:
    """Mint a unique, human-readable run id.

    Example: ``20260618-173045Z__monte_carlo__y2030__BelgiumFNAv31FullYear2023``.
    Sortable by time (UTC), and self-describing (mode + year + input workbook).
    """
    safe_stem = re.sub(r"[^0-9A-Za-z]+", "", str(input_stem))[:40] or "input"
    year = f"y{target_year}" if target_year not in (None, "") else "yNA"
    safe_mode = re.sub(r"[^0-9a-z_]+", "", str(mode).lower()) or "run"
    return f"{utc_stamp()}__{safe_mode}__{year}__{safe_stem}"


# ---------------------------------------------------------------------------
# Environment / hardware capture
# ---------------------------------------------------------------------------

def _macos_cpu_brand() -> str | None:
    try:
        out = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=5,
        )
        brand = out.stdout.strip()
        return brand or None
    except Exception:
        return None


def _linux_cpu_brand() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def _cpu_brand() -> str:
    system = platform.system()
    brand = None
    if system == "Darwin":
        brand = _macos_cpu_brand()
    elif system == "Linux":
        brand = _linux_cpu_brand()
    elif system == "Windows":
        brand = os.environ.get("PROCESSOR_IDENTIFIER")
    return brand or platform.processor() or platform.machine() or "unknown"


def _gpu_info() -> str:
    """Best-effort GPU description. The FNA solve is CPU-bound (CPLEX MIP), so
    this is informational; we never fail a run if no GPU probe is available."""
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=15,
            )
            names = [
                line.split(":", 1)[1].strip()
                for line in out.stdout.splitlines()
                if line.strip().startswith("Chipset Model")
            ]
            if names:
                return "; ".join(names)
        elif system == "Linux":
            # Prefer nvidia-smi; fall back to lspci VGA lines.
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=10,
                )
                gpus = [g.strip() for g in out.stdout.splitlines() if g.strip()]
                if gpus:
                    return "; ".join(gpus)
            except Exception:
                pass
            out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
            vga = [l.split(":", 2)[-1].strip() for l in out.stdout.splitlines() if "VGA" in l or "3D controller" in l]
            if vga:
                return "; ".join(vga)
        elif system == "Windows":
            out = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "name"],
                capture_output=True, text=True, timeout=10,
            )
            names = [l.strip() for l in out.stdout.splitlines()[1:] if l.strip()]
            if names:
                return "; ".join(names)
    except Exception as exc:  # pragma: no cover - platform dependent
        log.debug("GPU probe failed: %s", exc)
    return "n/a (CPU solve)"


def _total_ram_gb() -> float | None:
    try:
        import psutil

        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        return None


def _cpu_counts() -> tuple[int | None, int | None]:
    try:
        import psutil

        return psutil.cpu_count(logical=False), psutil.cpu_count(logical=True)
    except Exception:
        return None, os.cpu_count()


def _git_commit(project_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(project_root), capture_output=True, text=True, timeout=5,
        )
        commit = out.stdout.strip()
        if not commit:
            return None
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root), capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return f"{commit}{'-dirty' if dirty else ''}"
    except Exception:
        return None


def gams_version(gams_exe: str | None = None) -> str | None:
    """Resolve the GAMS version string, best-effort. Returns None if GAMS is
    not installed/resolvable (e.g. audit/validate on a machine without GAMS)."""
    try:
        from fna_be.config import GAMS_EXE  # local import: config has no heavy deps

        exe = gams_exe or GAMS_EXE
        out = subprocess.run([exe, "audit"], capture_output=True, text=True, timeout=15)
        first = (out.stdout or out.stderr).splitlines()
        return first[0].strip() if first else None
    except Exception:
        return None


def capture_environment(project_root: Path) -> dict[str, Any]:
    """Snapshot the compute environment for the manifest. Pure data; safe to
    call once per run (the GPU/GAMS probes are the only slowish parts)."""
    phys, logical = _cpu_counts()
    return {
        "hostname": platform.node(),
        "os": platform.platform(),
        "os_system": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "cpu_model": _cpu_brand(),
        "cpu_cores_physical": phys,
        "cpu_cores_logical": logical,
        "gpu": _gpu_info(),
        "total_ram_gb": _total_ram_gb(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "gams_version": gams_version(),
        "git_commit": _git_commit(project_root),
    }


# ---------------------------------------------------------------------------
# Manifest data model
# ---------------------------------------------------------------------------

@dataclass
class ScenarioTiming:
    """Per-scenario (Monte Carlo) compute record."""
    scenario_id: int
    started_at: str | None = None
    ended_at: str | None = None
    wall_seconds: float | None = None
    gams_wall_seconds: float | None = None
    gams_resource_seconds: float | None = None
    status: str = "pending"        # pending | ok | failed
    error: str | None = None


@dataclass
class RunManifest:
    """Full provenance + timing record for one model run."""
    run_id: str
    mode: str                       # deterministic | monte_carlo | multi_year
    target_year: int | str | None
    input_workbook: str
    run_dir: str
    settings: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    ended_at: str | None = None
    wall_seconds: float | None = None
    n_scenarios_requested: int | None = None
    n_scenarios_succeeded: int | None = None
    gams_wall_seconds_total: float | None = None
    status: str = "running"         # running | completed | failed
    error: str | None = None
    scenarios: list[ScenarioTiming] = field(default_factory=list)
    # Non-serialised bookkeeping:
    _start_dt: datetime | None = field(default=None, repr=False, compare=False)

    # -- lifecycle -----------------------------------------------------------

    def mark_started(self) -> "RunManifest":
        self._start_dt = _now()
        self.started_at = _iso(self._start_dt)
        return self

    def mark_finished(self, status: str = "completed", error: str | None = None) -> "RunManifest":
        end = _now()
        self.ended_at = _iso(end)
        if self._start_dt is not None:
            self.wall_seconds = round((end - self._start_dt).total_seconds(), 3)
        self.status = status
        self.error = error
        self.gams_wall_seconds_total = round(
            sum(s.gams_wall_seconds or 0.0 for s in self.scenarios), 3
        ) or self.gams_wall_seconds_total
        return self

    def add_scenario(self, timing: ScenarioTiming) -> None:
        self.scenarios.append(timing)
        self.n_scenarios_succeeded = sum(1 for s in self.scenarios if s.status == "ok")

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_start_dt", None)
        return data


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_manifest_json(run_dir: Path, manifest: RunManifest) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run_metadata.json"
    path.write_text(json.dumps(manifest.to_dict(), indent=2, default=str), encoding="utf-8")
    return path


_INDEX_COLUMNS = [
    "run_id", "mode", "target_year", "status",
    "started_at", "ended_at", "wall_seconds",
    "n_scenarios_requested", "n_scenarios_succeeded", "gams_wall_seconds_total",
    "input_workbook", "cpu_model", "cpu_cores_logical", "total_ram_gb",
    "os_system", "gams_version", "git_commit", "run_dir",
]


def append_to_index(runs_root: Path, manifest: RunManifest) -> Path:
    """Append/refresh one row in the cross-run registry CSV. Idempotent on
    run_id: a re-write (e.g. start then finish) replaces the existing row."""
    import pandas as pd

    runs_root = Path(runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)
    index_path = runs_root / "runs_index.csv"

    env = manifest.environment or {}
    row = {
        "run_id": manifest.run_id,
        "mode": manifest.mode,
        "target_year": manifest.target_year,
        "status": manifest.status,
        "started_at": manifest.started_at,
        "ended_at": manifest.ended_at,
        "wall_seconds": manifest.wall_seconds,
        "n_scenarios_requested": manifest.n_scenarios_requested,
        "n_scenarios_succeeded": manifest.n_scenarios_succeeded,
        "gams_wall_seconds_total": manifest.gams_wall_seconds_total,
        "input_workbook": manifest.input_workbook,
        "cpu_model": env.get("cpu_model"),
        "cpu_cores_logical": env.get("cpu_cores_logical"),
        "total_ram_gb": env.get("total_ram_gb"),
        "os_system": env.get("os_system"),
        "gams_version": env.get("gams_version"),
        "git_commit": env.get("git_commit"),
        "run_dir": manifest.run_dir,
    }

    if index_path.exists():
        try:
            existing = pd.read_csv(index_path)
            existing = existing[existing["run_id"] != manifest.run_id]
        except Exception:
            existing = pd.DataFrame(columns=_INDEX_COLUMNS)
    else:
        existing = pd.DataFrame(columns=_INDEX_COLUMNS)

    out = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    out = out.reindex(columns=_INDEX_COLUMNS)
    out.to_csv(index_path, index=False)
    return index_path


def manifest_to_frame(manifest: RunManifest):
    """Flatten the manifest into a key/value DataFrame for the Excel sheet."""
    import pandas as pd

    env = manifest.environment or {}
    settings = manifest.settings or {}
    rows: list[tuple[str, Any]] = [
        ("run_id", manifest.run_id),
        ("mode", manifest.mode),
        ("target_year", manifest.target_year),
        ("status", manifest.status),
        ("input_workbook", manifest.input_workbook),
        ("run_dir", manifest.run_dir),
        ("started_at", manifest.started_at),
        ("ended_at", manifest.ended_at),
        ("wall_seconds", manifest.wall_seconds),
        ("wall_clock", _human_seconds(manifest.wall_seconds)),
        ("n_scenarios_requested", manifest.n_scenarios_requested),
        ("n_scenarios_succeeded", manifest.n_scenarios_succeeded),
        ("gams_wall_seconds_total", manifest.gams_wall_seconds_total),
        ("error", manifest.error),
    ]
    rows += [(f"env.{k}", v) for k, v in env.items()]
    rows += [(f"setting.{k}", v) for k, v in settings.items()]
    return pd.DataFrame(rows, columns=["field", "value"])


def scenario_timing_frame(manifest: RunManifest):
    """Per-scenario timing table for the Excel sheet / a CSV. Empty for
    deterministic runs (no scenarios)."""
    import pandas as pd

    if not manifest.scenarios:
        return pd.DataFrame()
    return pd.DataFrame([asdict(s) for s in manifest.scenarios])


def _human_seconds(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def write_metadata_sheets(wb: Any, manifest: RunManifest, sheet_suffix: str = "") -> None:
    """Write the run-metadata sheet(s) into the (openpyxl) output workbook.

    ``99_Run_Metadata`` holds the flat key/value manifest; for Monte Carlo
    runs, ``99b_Scenario_Timings`` holds the per-scenario compute table.
    Safe no-op on any error - metadata must never break a results workbook."""
    try:
        from fna_be.io.excel import _write_df

        _write_df(wb, f"99_Run_Metadata{sheet_suffix}", manifest_to_frame(manifest))
        scen = scenario_timing_frame(manifest)
        if not scen.empty:
            _write_df(wb, f"99b_Scenario_Timings{sheet_suffix}", scen)
    except Exception as exc:
        log.warning("Could not write run-metadata sheet: %s", exc)


def persist(manifest: RunManifest, runs_root: Path) -> None:
    """Write JSON manifest into the run dir and refresh the registry CSV."""
    try:
        write_manifest_json(Path(manifest.run_dir), manifest)
        append_to_index(Path(runs_root), manifest)
    except Exception as exc:
        log.warning("Could not persist run manifest: %s", exc)
