# Belgium FNA Prototype

Open-source prototype of an **ACER Flexibility Needs Assessment** for the Belgian power system (Reg. EU 2019/943). Combines a GAMS unit-commitment / economic-dispatch optimisation with a PECD-based Monte Carlo weather-uncertainty layer and an automated ACER-indicator reporting pipeline.

> **Research prototype — not an official Belgian FNA, not endorsed by ACER or Elia.**

---

## What it does

- Solves a UC/ED optimisation over representative days or a full 8760-hour year
- Derives ACER FNA indicators (Articles 8–14): RES integration, ramping, short-term flexibility, network needs, DSO/TSO needs, Article-14 fine-tuning
- Runs Monte Carlo scenarios from PECD climate-year data to quantify weather uncertainty
- Generates 19 charts and a structured output workbook
- Multi-year runs (2025 / 2030 / 2035) with cross-year comparison

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .              # installs the fna CLI package (editable)
cp .env.example .env          # fill in GAMS_EXE and ENTSOE_API_KEY
python -m fna --help       # or: fna --help
```

## CLI commands

| Command | What it does |
|---|---|
| `fetch --country BE --year 2023` | Pull a country/year of hourly ENTSO-E data into `data/inputs/raw_<CC><year>/` (requires API key) |
| `build-full-year --country BE --year 2023` | Build `BE_FullYear2023.xlsx` (8760 h) from fetched data + a `--template` |
| `build-rep-days --country BE --year 2023 [--days N]` | Compress to `BE_RepDays2023.xlsx` (elbow-optimal day count if `--days` omitted) |
| `audit` | Data quality report + ACER compliance check |
| `run -y 2030` | Single UC/ED run (requires GAMS); alias for `run-deterministic` |
| `mc -y 2030 -n 200` | MC weather-uncertainty run; alias for `run-monte-carlo` |
| `compute-fna-indicators` | Post-process indicators from existing results |
| `fine-tune` | Article-14 network fine-tuning pass |
| `validate` | Config + workbook + GAMS sanity checks |
| `make-report` | Write Markdown report with tables and charts |

## Output structure (run isolation)

Every solve writes into its **own** timestamped folder, so deterministic runs,
Monte Carlo runs, and re-runs with different settings never overwrite each
other's CSVs, charts or workbooks:

```
data/outputs/runs/
  <run_id>/                         # run_id = <YYMMDD-HHMMSSZ>__<det|mc>__y<YY>__<input stem>
    <run_id>-output.xlsx            # results workbook for THIS run
    run_metadata.json               # provenance + compute + timing (see below)
    csv/                            # GAMS CSV outputs: dispatch, residual, price, reserve, ...
    images/                         # all charts for this run
    inc/                            # exact GAMS include files used
    gams_run.log  gams_run.lst
    scenarios/                      # Monte Carlo only
      scenario_0/
        inc/  csv/  gams_run.log  gams_run.lst
  index/runs_index.csv              # one row per run: mode, year, timing, host, status
```

- **`run_metadata.json`** and the **`99_Run_Metadata`** / **`99b_Scenario_Timings`**
  sheets in the output workbook record *what* ran (mode, target year, input
  workbook, key `01_Control` settings, git commit), *where* (CPU model, cores,
  GPU, OS, RAM, Python + GAMS versions), and *when / how long* (start/end
  timestamps and wall-clock seconds for the whole run and each MC scenario,
  including GAMS solve time).
- Post-process commands (`compute-fna-indicators`, `fine-tune`, `make-report`)
  default to the newest run folder; pass `--target-year <year>` to target a
  per-year folder instead.

## Requirements

- Python 3.10+, dependencies in `requirements.txt`
- **GAMS** (licensed) for optimisation runs — set `GAMS_EXE` in `.env`
- **ENTSO-E API key** for `fetch`/Monte Carlo — set `ENTSOE_API_KEY` in `.env`
- **PECD data** (not included — restricted redistribution): see `data/inputs/pecd/README.md`

The model is **fully headless** — Excel I/O uses `openpyxl`, so no Microsoft
Excel install (and no xlwings/COM automation) is required. It runs on Linux,
macOS, or Windows, and in containers / cloud VMs.

## Run in Docker (headless / cloud)

```bash
docker build -t fna .
# audit / validate need no GAMS:
docker run --rm -v "$PWD/data:/app/data" fna validate
# optimisation runs mount your licensed GAMS install:
docker run --rm \
  -v "$PWD/data:/app/data" \
  -v "/opt/gams:/opt/gams:ro" -e GAMS_EXE=/opt/gams/gams \
  fna run -y 2030
```

## Repo layout

```
gams/                       GAMS UC/ED optimisation core
python/fna/              the fna package (4 pipeline stages):
  inputs/                     (1) data fetching + representative-day build
  io/  io/indicators/         (2) Excel<->GAMS bridge + ACER indicators
  model/                      (3) deterministic / Monte Carlo run + GAMS runner
  plots/                      (4) charts + Markdown report
  config.py  run_metadata.py  shared: settings/paths + provenance capture
  cli.py                      command-line entry point
scripts/pull_vm_results.sh  pull isolated runs back from a VM
data/inputs/excel/demo_input.xlsx  demo workbook (~1 MB)
data/inputs/sample/               14-day trimmed ENTSO-E CSVs
data/inputs/pecd/README.md        PECD download instructions
data/outputs/runs/          per-run isolated output folders (see USER_GUIDE §5)
docs/
  USER_GUIDE.md             install, CLI, 01_Control reference, output structure, VM
  METHODOLOGY.md            ACER compliance, GAMS alignment, data lineage & sources
```

**Documentation:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md) (how to run) ·
[docs/METHODOLOGY.md](docs/METHODOLOGY.md) (ACER methodology & data).

## Cloud VM workflow (Vultr / any Linux host)

### Upload files to the VM
```bash
# From your Mac — upload an Excel workbook:
scp ~/Downloads/Belgium_FNA_v3.1_FullYear2023_input_data.xlsx root@YOUR_IP:/root/fna/data/inputs/excel/

# Or sync the whole input workbook folder:
rsync -avz data/inputs/excel/ root@YOUR_IP:/root/fna/data/inputs/excel/
```

### Edit control values in Excel
All run controls live in the `01_Control` sheet of the input workbook (target year, number of MC scenarios, country code, flags, etc.).

1. **Download** the workbook to your Mac, edit in Excel.app, save.
2. **Upload** the edited file back to the VM (see above).
3. **Rerun** — the container picks up the new values immediately (no rebuild needed).

Key controls you can also override without touching Excel, by passing `-e` flags to `docker run`:

| What to change | `-e` flag | Example |
|---|---|---|
| Parallel GAMS workers | `MAX_PARALLEL_WORKERS` | `-e MAX_PARALLEL_WORKERS=16` |
| Input workbook | `EXCEL_FILENAME` | `-e EXCEL_FILENAME=FNA_RepDays.xlsx` |
| GAMS executable path | `GAMS_EXE` | `-e GAMS_EXE=/opt/gams/gams` |

### Run different scenarios

```bash
cd /root/fna

# Full-year run (16 CPUs):
docker run --rm \
  -v "$PWD/data:/app/data" \
  -v "/opt/gams:/opt/gams:ro" -e GAMS_EXE=/opt/gams/gams \
  -e MAX_PARALLEL_WORKERS=16 \
  -e EXCEL_FILENAME=FNA_FullYear.xlsx \
  fna mc -y 2030 -n 100

# Representative-day run (different input workbook, same command):
docker run --rm \
  -v "$PWD/data:/app/data" \
  -v "/opt/gams:/opt/gams:ro" -e GAMS_EXE=/opt/gams/gams \
  -e MAX_PARALLEL_WORKERS=16 \
  -e EXCEL_FILENAME=FNA_RepDays.xlsx \
  fna mc -y 2030 -n 100
```

Use `tmux` to keep runs alive after SSH disconnect:
```bash
tmux new -s fna      # start session
# ... run your docker command ...
# Ctrl+B, then D      → detach (run keeps going)
tmux attach -t fna   # reconnect later
```

### Download results to your Mac

Each VM run lands in its own `data/outputs/runs/<run_id>/` folder (see
[Output structure](#output-structure-run-isolation)). Use the helper to pull
runs back **without mixing them together**:

```bash
# From your Mac (repo root):
scripts/pull_vm_results.sh root@YOUR_IP            # pull ALL runs + registry
scripts/pull_vm_results.sh root@YOUR_IP all
scripts/pull_vm_results.sh root@YOUR_IP <run_id>   # pull one specific run

# Inspect what ran where, when, and for how long:
column -s, -t data/outputs/runs/index/runs_index.csv | less -S
```

Each pulled run carries its own `-output.xlsx`, charts, CSVs and
`run_metadata.json` (CPU/GPU/OS, start/end times, GAMS solve seconds). Override
the remote project root with `REMOTE_ROOT=/path scripts/pull_vm_results.sh ...`.

## Data sources

ENTSO-E Transparency Platform · PECD 2021.3 (JRC/ENTSO-E) · Elia open data · Public Belgian generation planning data

## Disclaimer

This project is an independent research prototype. It is not an official Belgian Flexibility Needs Assessment and has not been validated or endorsed by ACER, Elia, or any Belgian regulatory authority. Results are illustrative only.
