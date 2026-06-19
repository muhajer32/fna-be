# User guide — running the Belgium FNA prototype

How to install, configure and run the model, and where results land. For the
ACER methodology / compliance side, see [METHODOLOGY.md](METHODOLOGY.md).

---

## 1. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .          # installs the `fna` command + dependencies
```

Requirements: Python ≥ 3.10, **GAMS** (licensed) for solves (`GAMS_EXE` in
`.venv/.env` or on PATH), and — for `fetch` / Monte Carlo — an
**ENTSO-E API key** (`ENTSOE_API_KEY`) and **PECD data** (`data/inputs/pecd/`, not
shipped). The model is fully headless (openpyxl, no Excel install needed).

> **pyenv note:** if `python3 -m fna` says "No module named fna", your
> shell's `python3` is a pyenv shim, not the venv. Use `fna …`, or run
> `hash -r` after activating the venv.

## 2. The pipeline (4 stages)

The package mirrors the four stages of an FNA, one subpackage each:

| Stage | Package | What it does |
|---|---|---|
| 1. Input fetching | `fna.inputs` | Pull a country-year of ENTSO-E/Elia data into a full-year hourly workbook (`full_year.py`); compress it into weighted representative days (`rep_days.py`) |
| 2. I/O + indicators | `fna.io` | Excel ⇄ GAMS bridge (`excel.py`), ACER indicator computation (`io/indicators/`), workbook migration (`migrate.py`) |
| 3. Model run | `fna.model` | Deterministic or Monte Carlo run (`run.py`), GAMS subprocess (`gams.py`), multi-year orchestration (`multi_year.py`) |
| 4. Plots | `fna.plots` | Deterministic + Monte Carlo charts (`base.py`, `fna_charts.py`, `mc_charts.py`), Markdown report (`report.py`) |

Shared: `fna.config` (settings/paths) and `fna.run_metadata` (provenance + compute capture).

## 2b. Preparing inputs (3 ways)

You can feed the model in any of these ways:

1. **Use an existing workbook** — full-year or representative — with `-w`:
   ```bash
   fna run -w BE_RepDays2023.xlsx
   ```

2. **Generate a full-year workbook** for a country/year:
   ```bash
   fna build-full-year --country BE --year 2023 --template Belgium_FNA_ED_v2_input_data.xlsx
   # → data/inputs/excel/BE_FullYear2023.xlsx   (8760 h)
   ```
   ENTSO-E supplies the hourly series (demand/wind/solar/flows/prices) for the
   country/year; the **`--template`** workbook supplies the *structural* sheets
   that ENTSO-E cannot (thermal-fleet limits, flex/storage portfolios,
   interconnector definitions) and the installed wind/solar capacities used for
   capacity factors. `--no-fetch` reuses a local `data/inputs/raw_<CC><year>/` cache.

   > **Review the amber-flagged data.** The generated workbook is **colour-coded**:
   > amber sheet tabs / rows are scenario *assumptions* to confirm for your
   > country (Pmin/ramp/start-up, flex sizing, interconnector capacities); green
   > tabs are historical ENTSO-E series that need no review. The command prints
   > the list of sheets to check.

3. **Generate a representative-day workbook** from a full-year one:
   ```bash
   fna build-rep-days --country BE --year 2023            # elbow picks optimal day count
   fna build-rep-days --country BE --year 2023 --days 12  # exactly 12 days
   # → data/inputs/excel/BE_RepDays2023.xlsx
   ```
   Clusters the 365 days on demand + wind + solar shapes; each representative
   day is weighted by its cluster size so weighted annual energy still reflects a
   full year. Without `--days`, the count is chosen by the elbow method on
   clustering inertia. Assumption colour-coding carries over.

## 3. CLI commands

Run as `fna <command>` (or `python -m fna <command>`):

| Command | Stage | What it does |
|---|---|---|
| `build-full-year --country BE --year 2023` | 1 | Build a full-year (8760 h) workbook `BE_FullYear2023.xlsx` from ENTSO-E + a template (needs API key unless cached) |
| `build-rep-days --country BE --year 2023 [--days N]` | 1 | Compress `BE_FullYear2023.xlsx` → `BE_RepDays2023.xlsx` (N days, or elbow-optimal if omitted) |
| `fetch --country BE --year 2023` | 1 | Pull raw hourly ENTSO-E data into `data/inputs/raw_<CC><year>/` (build-full-year auto-fetches too) |
| `validate` | — | Config + workbook schema + GAMS/PECD sanity checks (no solve) |
| `audit` | 2 | Data-quality / granularity report + ACER compliance pointers |
| `run [-y YYYY]` | 3 | Single UC/ED solve → isolated run folder (`run-deterministic` still works) |
| `mc [-y YYYY] [-n N]` | 3 | PECD weather-uncertainty ensemble (`run-monte-carlo` still works) |
| `compute-fna-indicators` | 2/4 | Recompute indicator sheets from the newest run (no re-solve) |
| `fine-tune` | 2/4 | Recompute DSO/TSO/Article-14 network-need sheets from the newest run |
| `make-report` | 4 | Markdown report bundling tables + charts for the newest run |

`compute-fna-indicators` / `fine-tune` / `make-report` operate on
the newest timestamped folder under `data/outputs/runs/` by default; pass
`--target-year` to target a per-year folder.

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
| `gams_model_file` | which `.gms` under `gams/` to solve (default `uc_ed_model_v3.gms`) — swap to run a model variant |

**GAMS solver controls** (no longer hard-coded in the `.gms`; emitted from here into `data.inc` and applied as model attributes)
| parameter | meaning |
|---|---|
| `gams_optcr` | relative MIP gap (e.g. `0.1` = stop at 10 %; tighten to `~0.01` for final cost figures) |
| `gams_optca` | absolute MIP gap (`0` = use relative only) |
| `gams_reslim` | solver time limit per solve, seconds |
| `gams_iterlim` | iteration limit |
| `gams_limrow` / `gams_limcol` | equation/variable listing sizes (`0` = none) |
| `gams_threads` | CPLEX threads (`0` = all cores) |

> If your workbook predates these rows, run `python -m fna.io.migrate` (with
> the workbook closed) to add them to `01_Control`; until then the defaults
> above apply automatically.

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

**Picking the input workbook per run.** Every command takes `--workbook/-w`
to choose which workbook in `data/inputs/excel/` to use, without editing anything:
```bash
fna run -w Belgium_FNA_v3.1_FullYear2023_input_data.xlsx
fna mc  -w demo_input.xlsx -n 50
```
Several settings can also be overridden via environment variables (e.g.
`EXCEL_FILENAME`, `GAMS_MODEL_FILE`, `MAX_PARALLEL_WORKERS`, `GAMS_EXE`,
`ENTSOE_API_KEY`) — useful for Docker `-e` flags.

## 5. Output structure (run isolation)

Every solve writes to its **own** timestamped folder, so deterministic runs,
Monte Carlo runs, and re-runs with different settings never collide:

```
data/outputs/runs/
  <run_id>/                       # <UTC>__<mode>__y<year>__<input-stem>
    <stem>-output.xlsx            # results workbook for THIS run
    run_metadata.json             # provenance + compute + timing
    csv/                          # GAMS CSV outputs: dispatch, residual, price, reserve, ...
    images/  inc/  gams_run.{log,lst}
    scenarios/                    # Monte Carlo only
      scenario_0/
        inc/  csv/  gams_run.log  gams_run.lst
  index/runs_index.csv            # one row per run: mode, year, timing, host, status
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
rsync -avz data/inputs/excel/ root@YOUR_IP:/root/fna/data/inputs/excel/

# run on the VM (tmux keeps it alive after disconnect)
docker run --rm -v "$PWD/data:/app/data" \
  -v "/opt/gams:/opt/gams:ro" -e GAMS_EXE=/opt/gams/gams \
  -e MAX_PARALLEL_WORKERS=16 fna mc -y 2030 -n 100

# pull runs back WITHOUT mixing them (see scripts/pull_vm_results.sh)
scripts/pull_vm_results.sh root@YOUR_IP all         # or: newest | <run_id>
column -s, -t data/outputs/runs/index/runs_index.csv | less -S
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
python -m fna.inputs.full_year_lite          # writes a *_full_year.xlsx benchmark
EXCEL_FILENAME=..._full_year.xlsx fna run
```
Compare sheets 30 and 40–43 between the rep-day and full-year runs to quantify
the compression error.
