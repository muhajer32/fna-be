"""
fna.logging_setup
=====================
One consistent logging setup for every CLI command: a per-command log file
under ``logs/cli/<command>.log`` plus a concise console stream. Re-uses the
same format as the existing ``main.py`` / ``multi_year.py`` scripts so log
files can be diffed/grepped together.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def configure_logging(command: str, project_root: Path) -> Path:
    """Configure root logging for ``command`` and return the log file path.

    Each command gets its own log file (overwritten on each run) under
    ``logs/cli/``, in addition to the existing per-workflow logs written by
    ``main.py`` / ``rep_days.py`` etc.
    """

    log_dir = project_root / "logs" / "cli"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{command}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="w", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    return log_file
