# User guide — running the Belgium FNA prototype

How to install, configure and run the model, and where results land. For the
ACER methodology / compliance side, see [METHODOLOGY.md](METHODOLOGY.md).

---

## 1. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .          # installs the `fna-be` command + dependencies
```

Requirements: Python ≥ 3.10, **GAMS** (licensed) for solves (`GAMS_EXE` in
`.venv/.env` or on PATH), and — for `refresh-data` / Monte Carlo — an
**ENTSO-E API key** (`ENTSOE_API_KEY`) and **PECD data** (`data/pecd/`, not
shipped). The model is fully headless (openpyxl, no Excel install needed).

> **pyenv note:** if `python3 -m fna_be` says "No module named fna_be", your
> shell's `python3` is a pyenv shim, not the venv. Use `fna-be …`, or run
> `hash -r` after activating the venv.

## 2. The pipeline (4 stages)

The package mirrors the four stages of an FNA, one subpackage each:

| Stage | Package | What it does |
|---|---|---|
| 1. Input fetching | `fna_be.inputs` | Pull a country-year of ENTSO-E/Elia data into a full-year hourly workbook (`full_year.py`); compress it into weighted representative days (`rep_days.py`) |
| 2. I/O + indicators | `fna_be.io` | Excel ⇄ GAMS bridge (`excel.py`), ACER indicator computation (`io/indicators/`), workbook migration (`migrate.py`) |
| 3. Model run | `fna_be.model` | Deterministic or Monte Carlo run (`run.py`), GAMS subprocess (`gams.py`), multi-year orchestration (`multi_year.py`) |
| 4. Plots | `fna_be.plots` | Deterministic + Monte Carlo charts (`base.py`, `fna_charts.py`, `mc_charts.py`), Markdown report (`report.py`) |

Shared: `fna_be.config` (settings/paths) and `fna_be.run_metadata` (provenance + compute capture).

## 3. CLI commands

Run as `fna-be <command>` (or `python -m fna_be <command>`):

| Command | Stage | What it does |
|---|---|---|
| `refresh-data --country BE --year 2024` | 1 | Pull ENTSO-E data → rebuild rep-day input sheets (needs API key) |
| `build-rep-days --clusters 20` | 1 | K-means representative-day compression |
| `validate` | — | Config + workbook schema + GAMS/PECD sanity checks (no solve) |
| `audit` | 2 | Data-quality / granularity report + ACER compliance pointers |
| `run-deterministic [--target-year YYYY]` | 3 | Single UC/ED solve → isolated run folder |
| `run-monte-carlo [--target-year YYYY] [--scenarios N]` | 3 | PECD weather-uncertainty ensemble |
| `compute-fna-indicators` | 2/4 | Recompute indicator sheets from the latest run (no re-solve) |
| `fine-tune` | 2/4 | Recompute DSO/TSO/Article-14 network-need sheets from the latest run |
| `make-report` | 4 | Markdown report bundling tables + charts for the latest run |

`compute-fna-indicators` / `fine-tune` / `make-report` operate on
`data/outputs/runs/latest` by default; pass `--target-year` to target a
per-year folder.

## 4. Modifying run controls (`01_Control`)

All run behaviour is driven by the `01_Control` sheet of the input workbook —
edit the `value` column, save, re-run (no code changes, no rebuild). The model
reads **every** coefficient from Excel; nothing is hard-coded.

**Run mode & scenario**
| parameter | meaning |
|---|---|
| `scenario_id` | free-text label recorded in outputs |
| `target_year` | which `*_<year>` input columns to read (e.g. 2025) |
| `future_year` | secondary year for multi-year/cross-year comparison (e.g. 2030) |
| `run_monte_carlo` | `0` = deterministic single solve, `1` = Monte Carlo ensemble |

**Model switches**
| parameter | meaning |
|---|---|
| `useUC` | `1` = integer unit commitment, `0` = economic dispatch only |
| `use_storage_cyclic_SOC` | `1` = storage returns to start-of-day energy each rep day |
| `use_network` | `1` = activate the optional Article-14 network layer (`13_NetworkNeeds`); `0` = ignore it (default — see METHODOLOGY §3) |

**Economics / penalties** (all EUR; tune for your study, document chosen values)
| parameter | meaning |
|---|---|
| `VOLL_EUR_per_MWh` | value of lost load (ENS price) |
| `curtailment_penalty_EUR_per_MWh` | RES curtailment penalty |
| `CO2_price_EUR_t` | CO₂ price applied to thermal emissions |
| `reserve_slack_penalty_EUR_per_MW` / `network_slack_penalty_EUR_per_MW` | uncovered-need penalties |
| `reserve_duration_h` | energy duration behind the reserve requirement |

**Short-term / ramping (ACER percentile method)**
| parameter | meaning |
|---|---|
| `shortterm_up_percentile` / `shortterm_dn_percentile` | forecast-error percentiles (e.g. 99.9 / 0.1) |
| `market_time_unit_minutes` | MTU for translating ramp stats into MW-per-MTU (sheet `41b`) |

**Monte Carlo**
| parameter | meaning |
|---|---|
| `n_mc_scenarios` | number of weather scenarios |
| `max_parallel_workers` | parallel GAMS worker processes |
| `seed_random` | RNG seed (reproducibility) |
| `use_pecd_data` / `pecd_data_dir` / `pecd_target_year` | PECD climate-data source |
| `wind_dist_type` / `wind_temporal_correlation` | wind sampling controls |

Several of these can also be overridden without touching Excel via environment
variables (e.g. `EXCEL_FILENAME`, `MAX_PARALLEL_WORKERS`, `GAMS_EXE`,
`ENTSOE_API_KEY`) — useful for Docker `-e` flags.

## 5. Output structure (run isolation)

Every solve writes to its **own** timestamped folder, so deterministic runs,
Monte Carlo runs, and re-runs with different settings never collide:

```
data/outputs/runs/
  <run_id>/                       # <UTC>__<mode>__y<year>__<input-stem>
    <stem>-output.xlsx            # results workbook for THIS run
    run_metadata.json             # provenance + compute + timing
    dispatch.csv residual.csv price.csv reserve.csv storage.csv network.csv
    images/  inc/  gams_run.{log,lst}
    scenario_0/ scenario_1/ ...   # Monte Carlo only
  latest -> <run_id>              # pointer to the most recent run
  runs_index.csv                  # one row per run: mode, year, timing, host, status
```

`run_metadata.json` and the workbook's `99_Run_Metadata` /
`99b_Scenario_Timings` sheets record **what** ran (mode, year, input workbook,
key `01_Control` settings, git commit), **where** (CPU model, cores, GPU, OS,
RAM, Python + GAMS versions) and **when / how long** (start/end timestamps and
wall-clock seconds for the run and each scenario, incl. GAMS solve time).

## 6. Cloud VM workflow

Upload the workbook, run in Docker, pull results back preserving run isolation:

```bash
# upload
rsync -avz excel/ root@YOUR_IP:/root/fna/excel/

# run on the VM (tmux keeps it alive after disconnect)
docker run --rm -v "$PWD/excel:/app/excel" -v "$PWD/data:/app/data" \
  -v "/opt/gams:/opt/gams:ro" -e GAMS_EXE=/opt/gams/gams \
  -e MAX_PARALLEL_WORKERS=16 fna run-monte-carlo --target-year 2030 --scenarios 100

# pull runs back WITHOUT mixing them (see scripts/pull_vm_results.sh)
scripts/pull_vm_results.sh root@YOUR_IP latest      # or: all | <run_id>
column -s, -t data/outputs/runs/runs_index.csv | less -S
```

## 7. Interpreting results

The output workbook sheets (per run):
- `30_Summary_Tables` — headline indicators.
- `40/14_FNA_RES_Integration`, `41/15_FNA_Ramping`, `41b_Ramping_Capacity`,
  `42_FNA_ShortTerm`, `42b_ShortTerm_BySeason`, `43_Residual_Duration` — ACER
  need indicators (Art. 8/9/10).
- `44_FNA_DSO_Needs`, `45_FNA_TSO_Needs`, `47_FNA_FineTuning_Art14` — network
  needs (only populated when `13_NetworkNeeds` / `13b_DSO_Zones` are filled).
- `46_FNA_Unavailability_Needs`, `48_FNA_Barriers_Summary`,
  `19_DataQuality_Report` — unavailability, barriers, data-quality.
- `50–53` + `54_MC_Charts` — Monte Carlo distributions (MC runs only).
- `99_Run_Metadata` / `99b_Scenario_Timings` — provenance + compute.

## 8. Benchmarking representative-day compression

```bash
python -m fna_be.inputs.full_year_lite          # writes a *_full_year.xlsx benchmark
EXCEL_FILENAME=..._full_year.xlsx fna-be run-deterministic
```
Compare sheets 30 and 40–43 between the rep-day and full-year runs to quantify
the compression error.
