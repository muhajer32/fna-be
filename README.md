# Open Belgium Flexibility Needs Assessment Prototype

An open-source, reproducible prototype of a **Flexibility Needs Assessment (FNA)**
for the Belgian power system, inspired by the methodology developed under the
ACER framework for assessing flexibility needs (Reg. (EU) 2019/943, as amended
by the 2024 Electricity Market Design package). It combines a structured Excel
data model, a GAMS unit-commitment / economic-dispatch optimisation, an optional
PECD-based Monte Carlo weather-uncertainty layer, and an automated reporting
pipeline that produces ACER-shaped flexibility indicators, charts, and a
dedicated output workbook.

---

> **Before you run this**
>
> - **Research prototype only** — results are illustrative and not investment-grade or regulatory submissions.
> - **PECD climate/demand data is not included** (restricted redistribution). The repo ships `data/sample/` (14-day trimmed ENTSO-E extract) and `excel/demo_input.xlsx` so the pipeline runs end-to-end without any external downloads. See `data/pecd/README.md` to obtain full PECD data.
> - **GAMS licence required** for optimisation runs (`run-deterministic`, `run-monte-carlo`). Set `GAMS_EXE` in `.env`. Without it, `audit`, `validate`, `make-report`, and all post-processing commands still work.
> - **ENTSO-E API key required** only for `refresh-data`. Register free at [transparency.entsoe.eu](https://transparency.entsoe.eu) and set `ENTSOE_API_KEY` in `.env`.
> - **macOS + Excel desktop**: `xlwings` requires granting your terminal Automation permission for Microsoft Excel (*System Settings → Privacy & Security → Automation*).
> - Copy `.env.example` → `.env` and fill in your values before running anything.

---

## 1. What this project does

This project takes a structured set of input assumptions (demand, generation
portfolio, interconnectors, storage/demand-response resources, reserve
requirements, network constraints) for the Belgian electricity system and:

1. **Builds an hourly (or representative-day) unit-commitment / economic-dispatch
   model** in GAMS that minimises system cost subject to energy balance,
   reserve, ramping, storage, and network constraints.
2. **Runs the model deterministically** (a single "best guess" year) or as a
   **Monte Carlo ensemble** over many weather/demand scenarios drawn from
   PECD (Pan-European Climate Database) data.
3. **Post-processes the results into ACER-style flexibility indicators**
   covering RES integration, ramping needs, and short-term flexibility needs,
   plus an optional Article-14-style network fine-tuning layer.
4. **Generates a full chart and dashboard set** (deterministic operational
   charts plus ~20 Monte Carlo probabilistic charts) and writes everything to
   a separate, versioned output Excel workbook (`<input file>-output.xlsx`).

In short: it is a **transparent, scriptable sandbox** for exploring how much
flexibility (fast generation, storage, demand response, imports) a power
system like Belgium's needs under realistic operating and weather conditions
— and how that need evolves under uncertainty.

---

## 2. What is an ACER FNA?

The **Flexibility Needs Assessment (FNA)** is a methodology mandated by EU
electricity market legislation that requires Transmission System Operators
(TSOs), in coordination with Distribution System Operators (DSOs), to
periodically quantify the **flexibility needs of the power system** — i.e.
the ability of the system to respond to variability and uncertainty in supply
and demand across different time horizons.

The methodology, developed under ACER's (Agency for the Cooperation of Energy
Regulators) guidance, typically asks system operators to identify and quantify
needs arising from:

- **RES integration** — curtailment and surplus-renewable management needs.
- **Ramping** — how quickly residual load (demand minus renewables) changes,
  and the flexible capacity required to follow it.
- **Short-term flexibility** — needs driven by forecast errors close to
  real time (e.g. wind/solar/demand forecast deviations).
- **Network-related needs** — congestion-driven needs at TSO and DSO level,
  including Article 14 "fine-tuning" needs.
- **Resource unavailability** — needs created by outages, prequalification
  failures, or temporary derating of flexibility providers.
- **Barriers and digitalisation gaps** — qualitative/quantitative obstacles
  preventing flexibility resources from being used effectively.

FNAs are intended to feed into investment planning, market design discussions,
and procurement of flexibility services (e.g. balancing capacity, demand
response programmes, storage). This project re-implements the **quantitative
core** of that methodology — the dispatch/optimisation model and the
indicator calculations — as an open, code-first prototype.

---

## 3. Current scope

- **Geography**: single-node Belgium, with aggregated cross-border
  interconnector flows to neighbouring zones (FR, NL, DE, LU, GB).
- **Time resolution**: hourly. Either a **representative-day** compression
  (a small number of clustered days, each with a weight) or a **full
  8760-hour** benchmark year.
- **Target years**: the model's "current" target year (default 2025) plus
  optional future years (2030, 2035) when `*_2030` / `*_2035` capacity
  columns are present in the input sheets — run via `multi_year.py`.
- **System needs covered**: RES integration (Art. 8), ramping (Art. 9), and
  short-term flexibility (Art. 10), plus a dormant Article-14 network
  fine-tuning layer (`13_NetworkNeeds`, `use_network` switch).
- **Uncertainty**: Monte Carlo scenarios driven by PECD climate-year data for
  wind/solar capacity factors and demand multipliers.

This is a **prototype for analysis, learning, and methodology testing** — not
a substitute for the official Belgian FNA published by Elia, nor a
certified regulatory deliverable.

---

## 4. What is already implemented

- ✅ **Structured Excel input model** (`excel/*.xlsx`) — one workbook per
  scenario set, with sheets for control parameters, representative
  hours/days, interconnectors, generation portfolio, RES capacity-factor
  profiles, flexibility/storage resources, availability/outages, reserve
  forecast-error parameters, and network needs.
- ✅ **GAMS UC/ED core model** (`gams/uc_ed_model_v3.gms`) — unit commitment
  or pure economic dispatch (`useUC` switch), explicit storage formulation
  with round-trip efficiency and optional cyclic state-of-charge, reserve
  (up/down) requirements with soft-constraint slack penalties, and an
  Article-14-style network layer.
- ✅ **Excel ⇄ GAMS bridge** (`io_excel.py`) — reads all input sheets, writes
  GAMS `.inc` include files, parses GAMS CSV outputs, and writes results back
  to a dedicated output workbook.
- ✅ **ACER-style indicator post-processing** (`fna_indicators.py`):
  - RES integration / curtailment (annual, seasonal, day-type, hourly).
  - Ramping statistics (max / P95 / mean-abs ramps, seasonal breakdown).
  - Short-term flexibility (empirical residual-load forecast-error
    percentile bands, P0.1 / P99.9 by default — configurable).
  - Weighted residual-load duration curve.
- ✅ **Monte Carlo engine** (`monte_carlo.py`) — PECD-based weather-year
  sampling of wind/solar capacity factors and demand multipliers, run in
  parallel across worker processes (`main.py` / `run_monte_carlo`).
- ✅ **Full chart suite** (`plot_results.py`, `mc_charts.py`,
  `fna_charts.py`):
  - Deterministic operational charts: generation mix, price curve, residual
    load duration curve, flexibility needs, reserve margin, storage
    state-of-charge, ramp probability matrix, unit-commitment Gantt chart,
    Sankey energy-flow diagram, stacked dispatch with price overlay.
  - Monte Carlo summary charts: cost distribution, PECD load/solar/wind
    input bands, residual-load and price uncertainty bands, workflow
    diagram.
  - Extended Monte Carlo risk charts: LOLE/LOLP histogram and ENS exceedance
    curve, event-probability chart (ENS / curtailment / reserve shortfall),
    cost-risk efficiency frontier, ELCC methodology placeholder, Monte Carlo
    ramp-event probability matrix, dispatch uncertainty bands, reserve
    percentile bands and shortfall probabilities, flexibility-activation
    distributions, and a cross-scenario correlation heatmap.
- ✅ **Multi-year workflow** (`multi_year.py`) — runs the full pipeline once
  per available target year and builds a cross-year comparison sheet/charts.
- ✅ **Representative-day refresh** (`rep_days.py`) and **full-year
  benchmark builder** (`build_full_year.py`) for quantifying the
  representative-day compression error.
- ✅ **Clean-run guarantees** — every successful `main.py` / `multi_year.py`
  run deletes the previous output workbook, scenario folders, images, and
  logs before starting, so results are never accidentally mixed across runs.
- ✅ **Data lineage documentation** (`docs/`) — mapping of every input field
  to its source / proxy status, and a self-assessed compliance gap matrix
  against the ACER methodology article-by-article.

---

## 5. What is not yet implemented

- ❌ **DSO-level flexibility needs** — `13b_DSO_Zones` exists as a schema but
  no congestion/hosting-capacity-driven need calculation runs against it yet.
- ❌ **Barriers & digitalisation indicators** — `17_Barriers_Digitalisation`
  is a placeholder sheet with no quantitative analysis behind it.
- 🟡 **TSO network needs** — the Article-14 layer is functional but dormant
  by default (`active_in_run=0`), produces only a single summary indicator,
  and has no time-resolved or per-network-element output sheet yet.
- 🟡 **Resource unavailability needs** — outages derate dispatchable capacity
  in the optimisation, but there is no standalone "unavailability-driven
  flexibility need" indicator (the gap between nameplate and available
  capacity, weighted by how binding it is).
- 🟡 **Short-term flexibility forecast errors** — currently parametric
  (assumption-based standard deviations in `12_Reserve_ForecastError`), not
  yet derived from an empirical historical forecast-error dataset.
- ❌ **Per-technology RES curtailment breakdown** (solar vs. onshore vs.
  offshore wind) — currently aggregated.
- ❌ **Full Effective Load Carrying Capability (ELCC)** — requires two
  paired Monte Carlo runs (base system vs. base + candidate resource); the
  current output is a methodology explainer chart only
  (`mc_11_elcc_placeholder.png`).
- ❌ **Zonal/nodal network representation** — the model is single-node by
  design; congestion is represented only via the coarse Article-14 layer.
- ❌ **Automated publication-ready report export** (PDF/structured summary
  bundling all indicator sheets with run metadata).

See `docs/ACER_FNA_COMPLIANCE_GAP_MATRIX.md` for a full article-by-article
self-assessment against the ACER methodology, including proposed next steps
for each gap.

---

## 6. Data sources

- **ENTSO-E Transparency Platform** (`entsoe-py`) — historical Belgian and
  cross-border load, generation, day-ahead prices, and scheduled flows, used
  by `rep_days.py` / `fetch_be_2023_data.py` to build representative
  days/hours and interconnector profiles.
- **PECD (Pan-European Climate Database)** — multi-decade climate-year wind
  and solar capacity-factor data, used by `monte_carlo.py` to generate
  weather-uncertainty scenarios (`data/pecd/`).
- **Elia / public planning data** — used as the basis for generation
  portfolio capacities, reserve dimensioning assumptions, and availability /
  outage profiles, documented per field in
  `docs/BELGIUM_FNA_DATA_SOURCE_MAP.md`.
- **Transparent proxies / assumptions** — where official data is not
  (yet) available, inputs are marked with a `source_id` / `data_quality`
  column in the workbook (sheet `20_Sources`) so every assumption is
  traceable and replaceable. See `docs/ANNEX1_MAPPING.md` for the full
  input-to-source mapping against ACER Annex 1 field families.

> All data handling respects ENTSO-E API usage terms; no API key is bundled
> with this repository — see "How to run" below.

---

## 7. Model workflow

```
                 ┌─────────────────────────┐
                 │  excel/<scenario>.xlsx    │   structured input workbook
                 │  (01_Control ... 20_*)    │
                 └────────────┬──────────────┘
                              │  io_excel.read_inputs()
                              ▼
                 ┌─────────────────────────┐
                 │  *.inc GAMS includes      │   data/inputs/
                 └────────────┬──────────────┘
                              │  run_gams.run_model()
                              ▼
                 ┌─────────────────────────┐
                 │  gams/uc_ed_model_v3.gms  │   UC/ED optimisation
                 │  (energy, reserve,        │
                 │   ramping, storage,       │
                 │   network constraints)    │
                 └────────────┬──────────────┘
                              │  CSV outputs (dispatch, price,
                              │  residual, reserve, storage, network)
                              ▼
                 ┌─────────────────────────┐
                 │  fna_indicators.py        │   ACER-style indicators
                 │  (RES integration,        │   (sheets 40-43)
                 │   ramping, short-term)     │
                 └────────────┬──────────────┘
                              │
        ┌─────────────────────┼─────────────────────────┐
        │                      │                          │
        ▼                      ▼                          ▼
┌───────────────┐   ┌───────────────────────┐   ┌─────────────────────┐
│ plot_results.py│   │ monte_carlo.py /        │   │ multi_year.py        │
│ deterministic  │   │ run_monte_carlo()       │   │ cross-year comparison│
│ charts (30-43) │   │  -> N parallel scenario │   │ (2025/2030/2035)      │
└───────┬────────┘   │  GAMS runs over PECD    │   └──────────┬───────────┘
        │            │  weather years          │              │
        │            └──────────┬──────────────┘              │
        │                       │ mc_charts.py + plot_results   │
        │                       │ (50-54: MC summaries +        │
        │                       │  probabilistic risk charts)   │
        ▼                       ▼                                ▼
                 ┌──────────────────────────────────┐
                 │  <input file>-output.xlsx          │
                 │  (results, indicators, charts,     │
                 │   MC summaries, cross-year)        │
                 └──────────────────────────────────┘
```

Each successful run of `main.py` (or `multi_year.py`) first clears the
previous run's scenario folders, images, logs, and output workbook, so the
output workbook always reflects exactly one, fully consistent run.

---

## 8. How to run deterministic mode

1. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

   You will also need a working **GAMS** installation (the model is written
   in GAMS syntax). Point `GAMS_EXE` to the executable, or ensure it is on
   `PATH`.

2. **Configure environment**

   Copy `.venv/.env.example` to `.venv/.env` and set at minimum:

   ```
   GAMS_EXE=/path/to/gams
   ENTSOE_API_KEY=...        # only needed for rep_days.py / data refresh
   ```

3. **(Optional) Refresh representative days / inputs**

   ```bash
   python python/rep_days.py
   ```

4. **Set control parameters** in the `01_Control` sheet of the input
   workbook (`excel/Belgium_FNA_v3.1_FullYear2023_input_data.xlsx` by
   default — override with `EXCEL_FILENAME`):

   - `target_year` — which `*_<year>` columns to read.
   - `useUC` — `1` for integer unit commitment, `0` for pure economic
     dispatch.
   - `use_storage_cyclic_SOC` — `1` to enforce same-day cyclic storage SOC.
   - `use_network` — `1` to activate the Article-14 network layer.
   - `run_monte_carlo` — leave at `0` for a deterministic run.

5. **Run the model**

   ```bash
   python python/main.py
   ```

6. **Inspect the results** in
   `excel/<input file>-output.xlsx`, sheets **30–35** (raw dispatch / price /
   reserve / storage / network results), **40–43** (ACER FNA indicators),
   and the deterministic chart sheet (generation mix, price curve, RLDC,
   reserve margin, etc.).

---

## 9. How to run Monte Carlo mode

1. Follow steps 1–2 above (dependencies + `.env`).

2. Ensure PECD climate data is available under `data/pecd/` (see
   `config.PECD_*` settings for country code / years / directory).

3. In `01_Control`, set:

   | parameter | meaning |
   |---|---|
   | `run_monte_carlo` | `1` to enable the Monte Carlo ensemble |
   | `n_mc_scenarios` | number of weather-year scenarios to sample |
   | `seed_random` | random seed for reproducibility (optional) |
   | `max_parallel_workers` | number of parallel GAMS scenario runs |
   | `voll_eur_per_mwh` | value of lost load used to cost energy-not-served |
   | `reserve_slack_penalty_eur_per_mw` | penalty for reserve shortfalls |

4. **Run**

   ```bash
   python python/main.py
   ```

   Each scenario runs in its own folder (`data/inputs/scenario_N`,
   `data/outputs/scenario_N`) via a process pool, then results are
   aggregated.

5. **Inspect the results** in `excel/<input file>-output.xlsx`:

   - Sheets **50–53** — Monte Carlo summary tables (per-scenario cost,
     indicator distributions).
   - Sheet **54_MC_Charts** — the full Monte Carlo chart set: cost
     distribution, PECD input bands, residual-load/price uncertainty bands,
     workflow diagram, LOLE/LOLP, event probabilities, efficiency frontier,
     ELCC placeholder, MC ramp matrix, dispatch and reserve uncertainty
     bands, flexibility-activation distributions, price distribution, and
     the cross-scenario correlation heatmap.

For multi-year runs (2025/2030/2035), use `python python/multi_year.py`
instead — it repeats the deterministic + Monte Carlo workflow per available
target year and adds a `60_CrossYear_Comparison` sheet and trend charts.

---

## 10. How to interpret results

- **Generation mix / dispatch charts** show how the system meets demand
  hour-by-hour: dispatchable thermal, RES, storage/flex, imports/exports,
  curtailment, and any energy-not-served (ENS).
- **Residual load duration curve (RLDC)** ranks residual load (demand minus
  RES) from highest to lowest — the steepness and tails indicate how much
  flexible capacity and ramping the system needs.
- **Reserve margin charts** compare available vs. required up/down reserve;
  any shortfall bars indicate hours where the system could not fully meet
  ACER short-term flexibility requirements.
- **Ramp probability matrix** is a 2D histogram of ramp-up vs. ramp-down
  events — a wide spread indicates a system that needs fast-responding
  capacity in both directions.
- **FNA indicator sheets (40–43)** translate the above into ACER-style
  metrics: curtailment shares by season/day-type/hour (RES integration need),
  ramp percentiles (ramping need), and forecast-error percentile bands
  (short-term flexibility need).
- **Monte Carlo charts (54_MC_Charts)**:
  - **LOLE/LOLP** — Loss-of-Load Expectation/Probability: how often, and by
    how much, the system fails to serve demand across scenarios. LOLP near
    zero with a tight ENS histogram indicates an adequate system.
  - **ENS exceedance curve** — the probability of exceeding a given energy
    shortfall threshold; use this to gauge tail risk.
  - **Event-probability chart** — fraction of scenarios with ENS,
    curtailment, or reserve shortfalls; a quick "stress dashboard".
  - **Cost-risk efficiency frontier** — scatter of total system cost vs.
    LOLE across scenarios with the efficient (Pareto) frontier highlighted;
    useful for comparing system configurations.
  - **Reserve percentile / shortfall charts** — P5/P50/P95 bands of
    available reserve vs. requirement, and the probability of shortfalls.
  - **Correlation heatmap** — how cost, ENS, curtailment, and reserve
    shortfalls relate to wind/solar capacity factors and demand multipliers
    — useful for identifying which weather drivers matter most.
  - **ELCC placeholder** — explains the two-run methodology needed to
    compute Effective Load Carrying Capability; not itself a result.

In general: **deterministic charts** answer "what does a typical year look
like?", while **Monte Carlo charts** answer "how much does that change under
weather/demand uncertainty, and what is the residual risk?"

---

## 11. Limitations

- **Single-node representation** — no intra-Belgium network constraints;
  cross-border flows are modelled as aggregated interconnector capacities,
  not a full European grid model.
- **Representative-day compression** — unless run on the full 8760-hour
  benchmark (`build_full_year.py`), results carry a compression error
  relative to a full-year run; this can be quantified but is not eliminated.
- **Proxy / assumption-based inputs** — several inputs (notably reserve
  forecast-error parameters and network needs) are transparent placeholders,
  not validated operational data — see `docs/BELGIUM_FNA_DATA_SOURCE_MAP.md`.
- **No DSO-level or barriers/digitalisation analysis** (see §5).
- **Macro-level Monte Carlo** — scenarios sample weather-driven capacity
  factors and demand multipliers; they do not model correlated equipment
  failures, market-design changes, or policy scenarios.
- **GAMS dependency** — requires a licensed GAMS installation; the
  optimisation core itself is not pure-Python.
- **Single country focus** — the methodology and code are written generically
  but currently configured and validated only for Belgium.

---

## 12. Roadmap

- [ ] DSO-level flexibility need quantification (`network_needs.py` +
      `13b_DSO_Zones`).
- [ ] Empirical forecast-error distributions for short-term needs, replacing
      parametric assumptions in `12_Reserve_ForecastError`.
- [ ] Per-technology RES curtailment breakdown (solar / onshore wind /
      offshore wind).
- [ ] Time-resolved Article-14 network output sheet and activation by
      default.
- [ ] Two-run ELCC workflow (base vs. base + candidate resource) with a
      dedicated comparison chart.
- [ ] Resource-unavailability flexibility-need indicator.
- [ ] Barriers & digitalisation indicators (`17_Barriers_Digitalisation`).
- [ ] Automated, publication-ready report export bundling all indicator
      sheets and run metadata.
- [ ] Extend multi-year and Monte Carlo coverage to additional zones /
      interconnected countries.

Contributions and issue reports against any of the above are welcome.

---

## 13. Disclaimer

This repository is an **independent, open-source research prototype**. It is
**not** the official Belgian Flexibility Needs Assessment, and it is **not**
endorsed, reviewed, or affiliated with **ACER**, **Elia** (the Belgian TSO),
or any other regulatory body or system operator. Results produced by this
model should **not** be used for regulatory, investment, or operational
decision-making without independent validation against official data and
methodologies. All input data is either publicly sourced (ENTSO-E, PECD) or
explicitly marked as a transparent proxy/assumption — see `docs/` for full
traceability.

---

## 14. Screenshots

> Add representative screenshots of the generated dashboards here, e.g.:
>
> - Generation mix / stacked dispatch with price overlay
>   (`data/outputs/images/v2_10_stacked_dispatch_price.png`)
> - Residual load duration curve (`v2_03_rldc.png`)
> - Reserve margin chart (`v2_05_reserve_margin.png`)
> - Monte Carlo cost distribution (`mc_01_cost_distribution.png`)
> - LOLE/LOLP histogram and ENS exceedance curve
>   (`mc_07_lole_histogram.png`, `mc_08_ens_exceedance_curve.png`)
> - Cost-risk efficiency frontier (`mc_10_efficiency_frontier.png`)
> - Cross-scenario correlation heatmap (`mc_19_correlation_heatmap.png`)
>
> ```markdown
> ![Stacked dispatch with price overlay](docs/images/stacked_dispatch_price.png)
> ![Residual load duration curve](docs/images/rldc.png)
> ![Monte Carlo cost distribution](docs/images/mc_cost_distribution.png)
> ```
>
> All charts are regenerated automatically on every run under
> `data/outputs/images/` and embedded in the output workbook.

---

## 15. Skills demonstrated

This project showcases an end-to-end energy-systems modelling and data
engineering toolchain:

- **Power systems & flexibility analysis** — unit commitment / economic
  dispatch formulation, reserve dimensioning, ramping and residual-load
  analysis, ACER FNA methodology implementation.
- **Mathematical optimisation** — GAMS modelling (mixed-integer UC, storage
  with cyclic state-of-charge, soft-constrained reserve and network
  constraints).
- **Stochastic / Monte Carlo analysis** — climate-year (PECD) weather
  uncertainty sampling, parallel scenario execution, risk-metric computation
  (LOLE/LOLP, exceedance curves, efficiency frontiers, correlation analysis).
- **Scientific Python** — pandas/numpy data pipelines, matplotlib charting at
  scale (40+ automated chart types), structured handling of large simulation
  ensembles.
- **Data engineering & ETL** — ENTSO-E Transparency Platform integration,
  representative-day clustering (k-means), Excel-as-data-model design with
  full source/quality traceability.
- **Software architecture** — modular, configuration-driven pipeline
  (Excel ⇄ GAMS ⇄ Python), reproducible "clean-run" guarantees, multi-year
  and multi-scenario orchestration.
- **Documentation & methodology traceability** — article-by-article gap
  analysis against a regulatory methodology, full input-to-source mapping,
  and transparent assumption tracking.

---

## Repository layout

```
config.py                     project settings (no secrets, no magic numbers)
python/
  rep_days.py                 rebuild representative days from ENTSO-E
  build_full_year.py          build an 8760-hour benchmark copy of the workbook
  monte_carlo.py               PECD weather-year scenario generation
  io_excel.py                  Excel <-> GAMS bridge + result writing
  fna_indicators.py            ACER-native indicator post-processing (sheets 40-43)
  fna_charts.py                additional FNA chart helpers
  plot_results.py               deterministic + Monte Carlo chart generation
  mc_charts.py                  extended Monte Carlo probabilistic risk charts
  network_needs.py              Article-14 / network needs helpers
  run_gams.py                   GAMS subprocess runner
  main.py                       single-year entry point (deterministic or Monte Carlo)
  multi_year.py                 multi-year (2025/2030/2035) orchestration + cross-year comparison
gams/uc_ed_model_v3.gms        optimisation core
excel/demo_input.xlsx           demo workbook (minimal, ~1 MB)
data/sample/                    14-day trimmed ENTSO-E CSVs (shipped)
data/pecd/README.md             instructions to obtain PECD data (not shipped)
data/inputs/ data/outputs/      generated at runtime — gitignored
docs/
  ANNEX1_MAPPING.md             how each input maps to ACER Annex 1 (ERAA/NRAA lineage)
  BELGIUM_FNA_DATA_SOURCE_MAP.md  per-field data source / proxy status
  ACER_FNA_COMPLIANCE_GAP_MATRIX.md  article-by-article self-assessment
  GUIDING_CRITERIA_TRACEABILITY.md   guiding-criteria traceability
```

## Quick reference: key controls (`01_Control`)

| parameter | meaning |
|---|---|
| `target_year` | selects the `*_2025` / `*_2030` / `*_2035` input columns |
| `useUC` | `1` = integer unit commitment, `0` = economic dispatch only |
| `use_storage_cyclic_SOC` | `1` = storage returns to start-of-day energy each rep day |
| `use_network` | `1` = activate the Article-14 network layer from `13_NetworkNeeds` |
| `shortterm_up_percentile` / `shortterm_dn_percentile` | ACER short-term percentiles |
| `run_monte_carlo`, `n_mc_scenarios`, `seed_random` | Monte Carlo controls |

## Benchmarking the representative-day compression

```bash
python python/build_full_year.py                                   # writes *_full_year.xlsx
EXCEL_FILENAME=Belgium_FNA_ED_v2_full_year.xlsx python python/main.py
```

Then compare sheets 30 and 40–43 between the two runs to see the compression
error.
