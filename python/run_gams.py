"""
run_gams.py - Belgium FNA-ED/UC v2
===================================
Runs GAMS 25.1 using subprocess, moves CSV outputs to data/outputs,
and returns parsed results.
"""
from __future__ import annotations
import logging
import os
import shutil
import subprocess
from pathlib import Path

from config import GAMS_EXE, GAMS_TIMEOUT, EXPECTED_CSV_OUTPUTS

log = logging.getLogger(__name__)


def run_model(inputs: dict, paths: dict) -> dict:
    from io_excel import write_inc_files, parse_csv_results

    gams_exe = _resolve_gams_exe()
    gms_file = Path(paths["gms_file"]).resolve()
    inc_dir = Path(paths["inc_dir"]).resolve()
    out_dir = Path(paths["out_dir"]).resolve()
    work_dir = out_dir / "_gams_work"
    log_file = Path(paths.get("log_file") or (out_dir / "gams_run.log")).resolve()
    if log_file == Path(paths["log_file"]).resolve() and out_dir.name.startswith("scenario_"):
        log_file = out_dir / "gams_run.log"
    lst_file = log_file.with_suffix(".lst")
    project_root = gms_file.parent.parent

    if not gms_file.exists():
        raise RuntimeError(f"GAMS model not found: {gms_file}")

    for folder in [inc_dir, out_dir, work_dir, log_file.parent]:
        folder.mkdir(parents=True, exist_ok=True)

    for p in [log_file, lst_file]:
        _delete(p)
    for name in EXPECTED_CSV_OUTPUTS:
        _delete(out_dir / name)
        _delete(work_dir / name)

    write_inc_files(inputs, inc_dir, out_dir)

    data_inc = inc_dir / "data.inc"
    cmd = [
        gams_exe,
        str(gms_file),
        f"--DATA_INC={data_inc}",
        f"o={lst_file}",
        "lo=2",
        f"lf={log_file}",
    ]
    log.info("Running GAMS: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(work_dir), capture_output=True, text=True, timeout=GAMS_TIMEOUT, env=os.environ.copy())
    log.info("GAMS return code: %s", proc.returncode)
    if proc.stdout.strip():
        log.info("GAMS stdout:\n%s", proc.stdout.strip())
    if proc.stderr.strip():
        log.error("GAMS stderr:\n%s", proc.stderr.strip())
    if proc.returncode != 0:
        _dump_listing(lst_file)
        raise RuntimeError(f"GAMS failed with code {proc.returncode}. Listing: {lst_file}")

    moved = []
    for name in EXPECTED_CSV_OUTPUTS:
        src = work_dir / name
        dst = out_dir / name
        if src.exists():
            shutil.move(str(src), str(dst))
            moved.append(name)

    missing = [n for n in EXPECTED_CSV_OUTPUTS if not (out_dir / n).exists()]
    if missing:
        _dump_listing(lst_file)
        raise RuntimeError(f"GAMS solved but missing CSV outputs: {missing}. Moved: {moved}")

    results = parse_csv_results(out_dir)
    log.info("Parsed v2 result CSV files from %s", out_dir)
    return results


def _resolve_gams_exe() -> str:
    """Resolve GAMS_EXE as either an absolute path or a command on PATH."""

    configured = str(GAMS_EXE).strip()
    if not configured:
        raise RuntimeError("GAMS_EXE is empty. Set it to the GAMS executable path or command name.")

    path = Path(configured).expanduser()
    if path.is_absolute() or path.parent != Path("."):
        if path.exists():
            return str(path)
        raise RuntimeError(f"GAMS executable not found: {configured}")

    resolved = shutil.which(configured)
    if resolved:
        return resolved

    raise RuntimeError(
        f"GAMS executable not found: {configured}. Install GAMS or set GAMS_EXE to the full executable path."
    )


def _delete(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception as exc:
        log.warning("Could not delete %s: %s", path, exc)


def _dump_listing(lst_file: Path) -> None:
    if not lst_file.exists():
        log.error("Listing file not found: %s", lst_file)
        return
    lines = lst_file.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = [i for i, line in enumerate(lines) if "****" in line or "Error" in line or "error" in line]
    if not hits:
        hits = list(range(max(0, len(lines)-80), len(lines)))
    context = set()
    for i in hits:
        context.update(range(max(0, i-4), min(len(lines), i+5)))
    for i in sorted(context):
        log.error("%5d | %s", i+1, lines[i])
