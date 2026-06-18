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
cp .env.example .env          # fill in GAMS_EXE and ENTSOE_API_KEY
python -m fna_be --help
```

## CLI commands

| Command | What it does |
|---|---|
| `audit` | Data quality report + ACER compliance check |
| `refresh-data --country BE --year 2024` | Pull ENTSO-E data (requires API key) |
| `build-rep-days --clusters 20` | K-means representative-day compression |
| `run-deterministic --target-year 2030` | Single UC/ED run (requires GAMS) |
| `run-monte-carlo --target-year 2030 --scenarios 200` | MC weather-uncertainty run |
| `compute-fna-indicators` | Post-process indicators from existing results |
| `fine-tune` | Article-14 network fine-tuning pass |
| `validate` | Config + workbook + GAMS sanity checks |
| `make-report` | Write Markdown report with tables and charts |

## Requirements

- Python 3.10+, dependencies in `requirements.txt`
- **GAMS** (licensed) for optimisation runs — set `GAMS_EXE` in `.env`
- **ENTSO-E API key** for `refresh-data` — set `ENTSOE_API_KEY` in `.env`
- **PECD data** (not included — restricted redistribution): see `data/pecd/README.md`

The model is **fully headless** — Excel I/O uses `openpyxl`, so no Microsoft
Excel install (and no xlwings/COM automation) is required. It runs on Linux,
macOS, or Windows, and in containers / cloud VMs.

## Run in Docker (headless / cloud)

```bash
docker build -t fna .
# audit / validate need no GAMS:
docker run --rm -v "$PWD/excel:/app/excel" -v "$PWD/data:/app/data" fna validate
# optimisation runs mount your licensed GAMS install:
docker run --rm \
  -v "$PWD/excel:/app/excel" -v "$PWD/data:/app/data" \
  -v "/opt/gams:/opt/gams:ro" -e GAMS_EXE=/opt/gams/gams \
  fna run-deterministic --target-year 2030
```

## Repo layout

```
gams/               GAMS UC/ED model
python/             Model modules + fna_be CLI package
excel/demo_input.xlsx  Demo workbook (~1 MB)
data/sample/        14-day trimmed ENTSO-E CSVs
data/pecd/README.md PECD download instructions
docs/               ACER compliance gap matrix, data source map, Annex I mapping
.env.example        Required environment variables
```

## Data sources

ENTSO-E Transparency Platform · PECD 2021.3 (JRC/ENTSO-E) · Elia open data · Public Belgian generation planning data

## Disclaimer

This project is an independent research prototype. It is not an official Belgian Flexibility Needs Assessment and has not been validated or endorsed by ACER, Elia, or any Belgian regulatory authority. Results are illustrative only.
