# ACER FNA Methodology — Compliance Gap Matrix (Belgium prototype, v3)

> **Scope note on article numbering.** The "All TSOs' proposal for a methodology
> for assessing flexibility needs" (Art. 19e of Reg. (EU) 2019/943, as amended by
> the 2024 Electricity Market Design package) is the reference. This repo already
> self-anchors three articles in `fna_indicators.py` (RES integration = Art. 8,
> ramping = Art. 9, short-term = Art. 10) and `docs/ANNEX1_MAPPING.md` (network /
> Art. 14). The matrix below follows that numbering convention. If your copy of
> the adopted methodology numbers articles differently, the **topic** column is
> the durable reference — re-map article numbers in a five-minute pass once you
> have the final PDF open side by side.

Legend: ✅ implemented · 🟡 partial · ❌ missing · ⬜ N/A for current prototype scope.

---

## 1. Article-by-article matrix

| Art. | Topic | Status | Evidence in repo | What's missing |
|---|---|---|---|---|
| 1 | Subject matter & scope | 🟡 | [README.md](../README.md) states scope ("single-node Belgium model... not an official FNA") | No formal scope statement mapped to the methodology's defined "system needs" taxonomy (timeframes, zones, scenarios) |
| 2 | Definitions | 🟡 | Implicit via column names (`residual_load_mw`, `curtailment_mw`, etc.) | No glossary mapping repo terms ⇄ ACER defined terms (e.g. "flexibility need", "structural need", "MTU") |
| 3 | General principles / guiding criteria | 🟡 | [docs/ANNEX1_MAPPING.md](../docs/ANNEX1_MAPPING.md) "Known deviations" section is an honest self-assessment | No explicit traceability table showing *which* guiding criteria (transparency, non-discrimination, cost-efficiency, technology-neutrality) each model choice satisfies — see §3 below |
| 4 | Data, granularity & quality requirements | 🟡 | `data_quality` / `source_id` columns on every input sheet (`11_Availability_Outages`, `12_Reserve_ForecastError`, `13_NetworkNeeds`, ...); hourly rep-day granularity | No machine-readable granularity/quality **report** sheet; no check that outputs meet ACER's minimum hourly + per-climate-year granularity end-to-end (Monte Carlo currently summarises, doesn't preserve per-scenario hourly detail in Annex-1 shape) |
| 5 | Scenarios / target years & time horizons | 🟡 | `01_Control` `target_year`/`future_year` (2025/2030); `monte_carlo.py` PECD weather years | Only two target years, single zone, no explicit horizon classes (seasonal / weekly / daily / intraday) reported separately |
| 6 | Identification & classification of system needs (general) | 🟡 | `fna_indicators.py` docstring explicitly classifies into RES integration / ramping / short-term | No top-level "needs register" output that lists every identified need with its classification, magnitude, and timeframe in one place |
| 7 | (placeholder — often "methodology for quantification") | ⬜ | — | N/A — covered by Art. 8–10 implementations |
| **8** | **RES integration needs** | ✅ | `gams/uc_ed_model_v3.gms` curtailment block; `fna_indicators._res_integration` → sheet `40_FNA_RES_Integration` (annual/seasonal/day-type/hourly curtailment) | Minor: no split by RES technology (PV vs wind vs offshore) — currently aggregate `curtailment_mw` only |
| **9** | **Ramping needs** | ✅ | `fna_indicators._ramping` → sheet `41_FNA_Ramping` (max/P95 ramp up/down, mean abs ramp, annual + seasonal) | No explicit "ramping need = required flexible capacity per MTU" translation — currently MW/h statistics, not a capacity requirement curve |
| **10** | **Short-term flexibility needs** | 🟡 | `_short_term_needs` in `io_excel.py` (parametric percentile method, combines forecast-error sigmas in quadrature) feeding GAMS reserve requirement; `fna_indicators._short_term` → sheet `42_FNA_ShortTerm` reports the *realised* empirical counterpart | Empirical forecast-error distribution is not yet built from historical data — `12_Reserve_ForecastError` percentages are static assumptions ("S20/S21... replace with Elia forecast error distributions"); no per-season/per-target-year short-term need breakdown |
| 11 | **DSO network-related flexibility needs** | ❌ | `13_NetworkNeeds` has `region`/`voltage_level` columns (e.g. "Flanders", "LV/MV") but rows are `active_in_run=0` placeholders | No DSO-specific need quantification path: no congestion-hour identification, no hosting-capacity-driven curtailment need per DSO zone, no output sheet distinguishing DSO vs TSO needs |
| 12 | **TSO network-related flexibility needs** | 🟡 | `networkUpReserve(t)` / `networkDownLimit(nw,t)` constraints in GAMS (single-node, "Article-14 style" coarse cap) | No zonal/nodal congestion representation (by design, single node); no TSO-specific need output separate from the generic `network_shortfall` row in `fna_indicators` CSV |
| 13 | **Unavailability needs (prequalification, temporary derating/limits)** | 🟡 | `11_Availability_Outages` (planned/forced outage %, `largest_unit_outage_MW`); feeds derating of `06_DispatchableBlocks` capacity in GAMS and the short-term stress term in `12_Reserve_ForecastError` | No explicit "unavailability-driven flexibility need" indicator (e.g. MW of replacement flexibility required due to prequalification failures or temporary network-driven derating of flexibility resources themselves — `10_FlexAvailability` derating is about *flexibility resource* availability, not a *need* output) |
| **14** | **Fine-tuning needs (Article 14 network layer)** | 🟡 | `13_NetworkNeeds`, `networkDownLimit`/`networkUpReserve` in GAMS, `network_shortfall` indicator, switch `use_network` | Dormant by default (all rows `active_in_run=0`); no dedicated output sheet (currently a single CSV row, not a structured `4x_FNA_Network*` sheet); no distinction between "fine-tuning" (close to real-time, Art. 14 sense) vs. structural network needs |
| 15 | **Barriers to flexibility & digitalisation** | ❌ | None found (`grep` for "barrier"/"digitali" returns nothing in `python/`, `gams/`, docs) | Entirely missing — no qualitative/quantitative barrier register, no digitalisation-readiness indicators (smart meter penetration, baseline methodologies for DR, real-time data availability for aggregators) |
| 16 | Reporting, publication & review cycle | 🟡 | `15_Dashboard`, `16_OutputTemplates`, `40_Charts`/`54_MC_Charts` sheets; `plot_results.py` | No versioned "FNA report" export (PDF/structured workbook) bundling all indicator sheets with metadata (assessment date, scenario set, data vintage) for publication |

---

## 2. Focus-area drill-down (your 10 priorities)

### 2.1 RES integration needs (Art. 8) — ✅ mostly implemented
- **Implemented:** curtailment dispatch variable `resCurt(r,t)` in [uc_ed_model_v3.gms](../gams/uc_ed_model_v3.gms), aggregated by `fna_indicators._res_integration` into sheet `40_FNA_RES_Integration` (annual / seasonal / day-type / hourly, with share-of-total %).
- **Gap:** no per-technology breakdown (solar vs onshore vs offshore wind), which ACER Annex 1 examples typically request.
- **Proposed addition:**
  - Module: [`fna_indicators.py`](../python/fna_indicators.py)
  - Function: `_res_integration_by_technology(df, res_portfolio)` — join `curtailment_mw` (per-resource, not yet emitted) against `07_RES_Portfolios.technology`
  - Input sheet: extend `31_Dispatch_Raw` (or GAMS CSV writer, line ~289 `put t.tl:0 ',' r.tl:0 '_curtailment...'`) to keep the per-resource curtailment rows already written, and read them in `io_excel.py` instead of discarding
  - Output sheet: new `40b_FNA_RES_Integration_byTech`

### 2.2 Ramping needs (Art. 9) — ✅ implemented, partial translation
- **Implemented:** `fna_indicators._ramping` → `41_FNA_Ramping` (max/P95 ramp up & down, mean abs ramp, per season).
- **Gap:** ACER frames ramping *needs* as a required flexible-capacity duration curve (MW available within X minutes), not just realised MW/h statistics.
- **Proposed addition:**
  - Module: `fna_indicators.py`
  - Function: `_ramping_capacity_requirement(df, mtu_minutes)` — convert MW/h percentiles into "MW required within one MTU", bucketed by season/day-type
  - Input sheet: `01_Control` — add `market_time_unit_minutes` parameter (currently implicit hourly)
  - Output sheet: `41b_FNA_Ramping_Capacity`

### 2.3 Short-term flexibility needs (Art. 10) — 🟡 partial
- **Implemented:** parametric percentile method (`_short_term_needs` in [io_excel.py](../python/io_excel.py)) feeding the GAMS reserve constraint; empirical counterpart in `42_FNA_ShortTerm`.
- **Gap:** forecast-error std-devs in `12_Reserve_ForecastError` are flat assumptions ("S20/S21..."), not derived from historical ENTSO-E/Elia data; no per-target-year (2025 vs 2030) or per-season variation.
- **Proposed addition:**
  - Module: new `python/forecast_error.py`
  - Function: `compute_forecast_error_stats(entsoe_load, entsoe_wind, entsoe_solar, day_ahead_forecasts) -> pd.DataFrame` — empirical std-dev of (forecast − actual) per season/hour
  - Input sheet: extend `12_Reserve_ForecastError` with `season` column and a `data_quality='empirical'` row set, sourced via `rep_days.py`-style ENTSO-E pull
  - Output sheet: `42b_FNA_ShortTerm_BySeasonYear`

### 2.4 DSO network flexibility needs — ❌ missing
- **Gap:** `13_NetworkNeeds` has the right columns (`region`, `voltage_level`, `direction`, `need_value`) but nothing computes a *need* — it's a manually-entered placeholder, always `active_in_run=0`.
- **Proposed addition:**
  - Module: new `python/network_needs.py`
  - Function: `compute_dso_needs(residual_df, dso_zones, res_portfolio) -> pd.DataFrame` — for each DSO zone, estimate hours where local RES output exceeds a configurable hosting-capacity proxy → downward DSO need (MW/MWh); for demand-driven congestion, flag hours where local peak demand exceeds a feeder-capacity proxy → upward DSO need
  - Input sheet: new `13b_DSO_Zones` (zone_id, region, voltage_level, hosting_capacity_MW, peak_feeder_capacity_MW, share_of_national_RES_pct, share_of_national_demand_pct)
  - Output sheet: new `44_FNA_DSO_Needs` (zone_id, direction, season/hour, need_MW, need_MWh)
  - GAMS: not required initially — this can be a post-processing proxy layer like `fna_indicators.py`, later promoted to a constraint (`networkDownLimit` already supports per-`nw` granularity, so DSO zones could become additional `nw` entries in `13_NetworkNeeds`).

### 2.5 TSO network flexibility needs — 🟡 partial
- **Implemented:** `networkUpReserve(t)` / `networkDownLimit(nw,t)` in GAMS provide a coarse single-node TSO need; `network_shortfall` indicator row.
- **Gap:** no structured output sheet, no distinction from DSO needs, no time-series of `networkSlack(nw,t)`.
- **Proposed addition:**
  - Module: [`io_excel.py`](../python/io_excel.py) (results-writing section) and `fna_indicators.py`
  - Function: `_tso_network_needs(df, network_needs_sheet)` — pivot `networkSlack` time series (already computed in GAMS but only summed to a scalar at line ~316) into per-`nw`, per-hour rows
  - GAMS change: write `networkSlack.l(nw,t)` per-period (currently only `networkShortMWh` total is exported, around line 316) into a new CSV `network.csv`
  - Output sheet: new `45_FNA_TSO_Needs` (network_need_id, time_id, slack_MW, direction, region, voltage_level)

### 2.6 Unavailability due to prequalification / temporary limits — 🟡 partial
- **Implemented:** `11_Availability_Outages` derates dispatchable capacity (planned/forced outage %, largest-unit outage MW); this MW feeds the short-term stress term in `12_Reserve_ForecastError`.
- **Gap:** ACER's "unavailability" need is broader — it covers flexibility *resources* (storage, DSR) being temporarily unavailable for prequalification reasons or due to local network constraints, and the resulting *additional* flexibility need this creates. `10_FlexAvailability` captures resource availability but there's no indicator quantifying the *gap* it creates.
- **Proposed addition:**
  - Module: `fna_indicators.py`
  - Function: `_unavailability_needs(flex_availability_df, flex_storage_df, dispatch_df) -> pd.DataFrame` — for each flex resource, compute MW of "nameplate minus available" capacity per hour, weighted by how often that resource is at its availability bound in the dispatch solution (i.e. it would have been used if available)
  - Input sheet: `10_FlexAvailability` (existing) + new column `prequalification_status` (qualified / temporary_limit / unavailable) on a new sheet `10b_Prequalification_Log`
  - Output sheet: new `46_FNA_Unavailability_Needs`

### 2.7 Article 14 fine-tuning — 🟡 partial, dormant
- **Implemented:** `13_NetworkNeeds` schema, `networkDownLimit`/`networkUpReserve` constraints, `use_network` switch, `network_shortfall` summary line.
- **Gap:** (a) always off in the shipped workbook (all `active_in_run=0`); (b) output is a single scalar, not a time-resolved sheet; (c) no distinction between Art. 14 "fine-tuning" (near-real-time residual local imbalances) and structural DSO/TSO needs from §2.4/2.5 — these are conceptually different timeframes in ACER's framework.
- **Proposed addition:**
  - Reuse the `network.csv` export proposed in §2.5
  - Module: `fna_indicators.py` → `_fine_tuning_needs(network_df, rep_hours)` — restrict to `time_block_or_rep_day` entries tagged as fine-tuning timeframe (new column on `13_NetworkNeeds`)
  - Input sheet: `13_NetworkNeeds` — add `timeframe` column (`structural` / `fine_tuning`) and populate at least one non-placeholder `active_in_run=1` row so the layer is demonstrably live
  - Output sheet: new `47_FNA_FineTuning_Art14`

### 2.8 Barriers and digitalisation — ❌ missing entirely
- **Gap:** no code, sheet, or doc addresses this. ACER's methodology asks for a qualitative/semi-quantitative register of barriers (e.g. lack of dynamic tariffs, metering granularity, aggregator access rules, baseline methodology maturity) and digitalisation readiness indicators.
- **Proposed addition:**
  - Module: new `python/barriers.py` (lightweight — mostly a structured pass-through/validator, not a model)
  - Function: `summarise_barriers(barriers_sheet) -> pd.DataFrame` — validate completeness/scoring of the input sheet, compute a simple digitalisation-readiness score per flexibility category
  - Input sheet: new `17_Barriers_Digitalisation` (barrier_id, category [DSR/storage/RES/...], description, severity_1to5, digitalisation_dependency [smart_meter/HEMS/aggregator_API/...], status, source_id, data_quality, notes)
  - Output sheet: new `48_FNA_Barriers_Summary` (category, count_high_severity, digitalisation_readiness_score, notes) — feeds `15_Dashboard`

### 2.9 Guiding criteria — 🟡 partial (implicit)
- **Implemented:** `docs/ANNEX1_MAPPING.md` "Known deviations from full FNAM" is effectively an honest gap statement; `data_quality`/`source_id` columns support transparency.
- **Gap:** no explicit traceability matrix showing how each guiding criterion (e.g. cost-efficiency, non-discrimination between flexibility sources, technology neutrality, transparency of data sources) is addressed by specific model/input choices.
- **Proposed addition:**
  - Doc, not code: new `docs/GUIDING_CRITERIA_TRACEABILITY.md` — one row per criterion, mapped to repo evidence (e.g. "technology neutrality" → `09_FlexStorage`/`10_FlexAvailability` treat storage, DSR, and thermal flex resources through the same `flexUp`/`flexDown` variables in GAMS, no technology-specific bonus/penalty)
  - Optional lightweight check: `python/guiding_criteria_check.py` → `check_technology_neutral_costs(flex_storage_df)` warns if any flex resource has a cost parameter set to zero/None while others don't (signals an implicit non-neutral assumption)

### 2.10 Data format and granularity (Art. 4) — 🟡 partial
- **Implemented:** consistent `time_id` (rep-day/hour) keying across sheets; `data_quality`/`source_id` provenance columns on every input sheet; `build_full_year.py` for 8760h benchmarking; PECD climate-year Monte Carlo.
- **Gap:** (a) no single machine-readable manifest of "what granularity/quality does each output actually have" for publication; (b) Monte Carlo summaries (`50-53`) lose per-scenario hourly detail that Annex 1 outputs expect to be preservable.
- **Proposed addition:**
  - Module: `fna_indicators.py` or new `python/data_quality_report.py`
  - Function: `build_granularity_report(input_sheets, output_sheets) -> pd.DataFrame` — for each sheet, record temporal resolution (hourly/rep-day/annual), spatial resolution (national/zonal), and aggregate `data_quality` distribution (assumption / calibrated / empirical)
  - Output sheet: new `19_DataQuality_Report` (sheet_name, temporal_resolution, spatial_resolution, pct_empirical, pct_assumption, notes)
  - For Monte Carlo: extend `monte_carlo.py` to optionally retain per-scenario hourly residual-load arrays in a compressed sidecar (e.g. parquet under `data/outputs/scenario_*/residual_full.parquet`) rather than only summarised `5x_MC_*` sheets

---

## 3. Summary table (quick reference)

| Priority area | Article(s) | Status |
|---|---|---|
| RES integration needs | 8 | ✅ |
| Ramping needs | 9 | ✅ (partial translation to capacity-requirement form) |
| Short-term flexibility needs | 10 | 🟡 |
| DSO network flexibility needs | 11 | ❌ |
| TSO network flexibility needs | 12 | 🟡 |
| Unavailability (prequalification/temporary limits) | 13 | 🟡 |
| Article 14 fine-tuning | 14 | 🟡 (dormant) |
| Barriers & digitalisation | 15 | ❌ |
| Guiding criteria | 3 | 🟡 (implicit) |
| Data format & granularity | 4 | 🟡 |

---

## 4. Suggested implementation order

1. **Network split (2.4 + 2.5 + 2.7)** — share one `network.csv` GAMS export and one `13_NetworkNeeds` schema extension (`timeframe`, `zone_id`) across DSO, TSO, and Article-14 outputs. Highest leverage: three gaps closed from one data-plumbing change.
2. **Unavailability needs (2.6)** — purely a post-processing function on data you already compute (`10_FlexAvailability`, dispatch results).
3. **Data quality report (2.10)** — cheap, makes the "assumption vs empirical" status visible for every other gap.
4. **Barriers & digitalisation (2.8)** — new input sheet + simple summary; no model changes.
5. **Short-term empirical calibration (2.3)** and **ramping capacity translation (2.2)** — both require new ENTSO-E pulls or `01_Control` parameters; bundle with the next `rep_days.py` refresh.
6. **Guiding criteria traceability (2.9)** — documentation pass once 1–5 give you concrete evidence to cite.
