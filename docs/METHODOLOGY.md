# ACER FNA methodology — compliance, alignment & data

How this prototype maps to ACER's Flexibility Needs Assessment methodology, what
is implemented vs. still a proxy, and where each input should ultimately come
from. For how to *run* the model, see [USER_GUIDE.md](USER_GUIDE.md).

> **Reference & numbering.** The "All TSOs' methodology for assessing flexibility
> needs" (Art. 19e of Reg. (EU) 2019/943, as amended by the 2024 Electricity
> Market Design package) is the reference. The repo anchors RES integration =
> Art. 8, ramping = Art. 9, short-term = Art. 10, network/fine-tuning = Art. 14.
> If the adopted methodology numbers articles differently, the **topic** column
> is the durable reference.

---

## 1. Scope & known deviations (read first)

This is an independent single-zone research prototype, **not** an official FNA.
The deviations below apply throughout and are not repeated per-section:

- **Single node** — no nodal/zonal transmission detail; network needs (Art. 14)
  are a coarse hosting-cap proxy, off by default (§3).
- **Representative-day compression** — 240 h rep-days with annualising weights,
  not full 8760 h (a full-year benchmark workbook exists; see USER_GUIDE §8).
- **Data maturity** — most coefficients are `assumption`/`placeholder` (visible
  in sheet `19_DataQuality_Report`), to be replaced by the sources in §6.
- **Two target years** (2025/2030) via `01_Control.target_year`; ACER expects the
  method to be repeatable for any adopted scenario set.

Legend: ✅ implemented · 🟡 partial · ❌ missing · ⬜ N/A for current scope.

## 2. Article-by-article status

| Art. | Topic | Status | Evidence (module / output sheet) |
|---|---|---|---|
| 3 | Guiding criteria | ✅ | §4 traceability table |
| 4 | Data, granularity & quality | 🟡 | `data_quality`/`source_id` columns on every input sheet; `io/indicators/quality.py` → `19_DataQuality_Report` |
| 5 | Scenarios / target years | 🟡 | `01_Control` `target_year`/`future_year`; `model/monte_carlo.py` PECD years |
| 6 | Identification of system needs | ✅ | indicator sheets 40–48 |
| **8** | **RES integration needs** | ✅ | GAMS `resCurt`; `io/indicators/res_integration.py` → `40_FNA_RES_Integration` (annual/seasonal/hourly) |
| **9** | **Ramping needs** | ✅ | residual-load ramps; `io/indicators/ramping.py` → `41_FNA_Ramping` + `41b_FNA_Ramping_Capacity` (MW-per-MTU) |
| **10** | **Short-term flexibility needs** | 🟡 | percentile forecast-error method in `io/excel.py` → GAMS reserve; `io/indicators/shortterm.py` → `42_FNA_ShortTerm` + `42b_…_BySeason`. *Gap:* error σ still assumptions, not empirical |
| 11 | **DSO network needs** | 🟡 | `io/indicators/network.py:compute_dso_needs` → `44_FNA_DSO_Needs` from `13b_DSO_Zones`. *Gap:* placeholder zone data |
| 12 | **TSO network needs** | 🟡 | `compute_tso_needs` → `45_FNA_TSO_Needs`. Single-node proxy by design |
| 13 | **Unavailability needs** | 🟡 | `io/indicators/core.py:_unavailability_needs` → `46_FNA_Unavailability_Needs` (derating + prequalification from `10b_Prequalification_Log`) |
| **14** | **Fine-tuning (network) needs** | 🟡 | optional GAMS layer (`useNetwork`, default off, §3); `compute_fine_tuning_needs` → `47_FNA_FineTuning_Art14` |
| 15 | **Barriers & digitalisation** | ✅ | `io/indicators/quality.py:summarise_barriers` ← `17_Barriers_Digitalisation` → `48_FNA_Barriers_Summary` |
| 16 | Reporting & review | 🟡 | per-run workbook + `make-report`; `99_Run_Metadata` provenance. *Gap:* no formal published-report export |

Remaining substantive gaps: empirical forecast-error calibration (Art. 10), real
DSO/DNDP data (Art. 11/14), per-technology RES curtailment split (Art. 8).

## 3. GAMS core ↔ ACER alignment (`gams/uc_ed_model_v3.gms`)

The optimisation core is a chronological least-cost UC/ED. Every reported need
traces to a GAMS object — nothing is fabricated in post-processing:

| ACER need | GAMS object |
|---|---|
| Chronological dispatch | `next(t,tt)`, ramp eqs, storage `socFlow`/`socCyclicEq` |
| Least-cost, technology-neutral | single `obj`; generation/RES/storage/DR enter the same `balance`/reserve constraints with no per-type term |
| RES integration (Art. 8) | `resBalance`: `resUse + resCurt = available` |
| Ramping (Art. 9) | `residualRampUp/Dn(t)` → `residual.csv` |
| Short-term (Art. 10) | `shortUpNeed/shortDnNeed` in `reserveUpEq/reserveDnEq`; slacks expose shortfall |
| Adequacy | `ens(t)` priced at `voll` |

**Network parameters (Art. 14) — decision.** The `nw*` layer
(`networkDownLimit`/`networkUpReserve`, `nwDownCap`, `networkSlack`,
`network.csv`) is gated behind `useNetwork`. With `01_Control.use_network = 0`
(default) it is **inert** — zero added constraints, `networkSlack` fixed to 0.
It is kept (not deleted) because it is ACER-Art-14-relevant and the DSO/TSO
post-processing reads `13_NetworkNeeds` directly; on a single node it can only
ever be a proxy. **To ignore it** (system-need assessment): leave
`use_network = 0`. **To use it:** set `use_network = 1` and populate
`13_NetworkNeeds` with `active_in_run=1` rows. To strip it entirely, remove the
`nw`-related objects + the two `if(useNetwork…)` guards + `network.csv` from
`config.EXPECTED_CSV_OUTPUTS`.

**Solver settings** (currently hard-coded in the `.gms`, unlike all
coefficients): `MIP/LP = CPLEX`, `reslim = 1200 s`, `optcr = 0.10`. The 10 %
gap is fine for *sizing* needs; tighten to ~0.01 for headline cost figures.

## 4. Guiding-criteria traceability (Art. 3)

| Criterion | How it is satisfied |
|---|---|
| Transparency | `source_id`/`data_quality` on every input sheet; `19_DataQuality_Report` aggregates maturity; per-run `99_Run_Metadata` provenance |
| Technology neutrality | all resources use the same `flex*`/`res*` parameters and constraints — no per-technology bonus/penalty |
| Non-discrimination | generation and flexibility meet reserve/short-term needs on one shared constraint |
| Cost-efficiency | single least-cost `obj`; flexibility activates only when cheaper than the alternative |
| Granularity ↔ timeframe | hourly rep-day resolution; seasonal breakdowns (`41b`,`42b`); network needs carry annual/seasonal levels |
| Structural vs. fine-tuning | `13_NetworkNeeds.timeframe` routes entries to `45_FNA_TSO_Needs` (structural) vs. `47_FNA_FineTuning_Art14` |
| Traceability to scenario/year | `target_year` selects `*_<year>` columns consistently; recorded in the manifest |
| Avoiding double-counting | each need from a distinct GAMS variable, each entering the objective once; unavailability needs are diagnostic-only (flag when totalling) |

## 5. Data lineage to ERAA / NRAA (Annex 1)

Each workbook table has a defined slot to be replaced by an official source:

| Workbook sheet | Annex 1 family | Replace with |
|---|---|---|
| `02_RepHours` demand | Demand time series, hourly | ERAA demand per climate year |
| `07_RES_Portfolios`, `08_RES_CF_Profiles` | RES capacity + hourly availability | ERAA capacities + PECD CFs |
| `06_DispatchableBlocks` | Thermal fleet technical limits | ERAA generation database |
| `09_FlexStorage`, `10_FlexAvailability` | Storage / DSR | ERAA storage + national DSR |
| `04/05_Interco*` | Cross-border capacity & flows | ERAA NTC / flow-based, JAO |
| `11_Availability_Outages` | Derating / outages | ERAA outage patterns |
| `12_Reserve_ForecastError` | Reserve + forecast-error | Elia reserve dimensioning + historical error series |
| `13_NetworkNeeds`, `13b_DSO_Zones` | Network / Art-14 | DNDP / local flexibility plans |

Outputs target at least hourly resolution per weather scenario (sheets
`33_Residual`, `31_Dispatch_Raw`, `40–48`, `50–53`). Monte Carlo climate years
should use one consistent PECD vintage across demand/wind/solar.

## 6. Belgium data-source map

Best primary (Belgium) + fallback (EU-wide) source per input category. Access:
**P** = public, **R** = restricted (market-party/NDA/paid).

| # | FNA item (article) | Granularity | Belgium source | EU fallback | Method | Access |
|---|---|---|---|---|---|---|
| 1 | Demand (Annex 1) | hourly | Elia Open Data "Total Load"; Adequacy & Flexibility Study scenarios | ENTSO-E Transparency Total Load | ENTSO-E/Elia API | P |
| 2 | Wind (Art. 8) | hourly CF, on/offshore | Elia wind production + registry | ENTSO-E gen-per-type; PECD CFs | API / PECD bulk | P |
| 3 | Solar (Art. 8) | hourly CF | Elia solar production | ENTSO-E Solar; PECD | API / PECD | P |
| 4 | Hydro/PSP (Annex 1) | capacity + hourly | Elia gen-per-type; Coo factsheets | ENTSO-E Hydro categories | API | P (levels R) |
| 5 | Storage / BESS (Art. 9/10) | power+energy by year | Elia capacity register; CREG report | ENTSO-E TYNDP/ERAA | manual | P (asset-level R) |
| 6 | Thermal fleet (Annex 1) | per-unit limits | Elia register; nuclear phase-out schedule | ENTSO-E installed capacity; ERAA | manual | P (unit params R) |
| 7 | Outages (Annex 1) | per-unit windows | Elia REMIT UMM | ENTSO-E Unavailability | API | P |
| 8 | Reserve requirements (Art. 10) | system MW/product | Elia reserve-dimensioning reports | ENTSO-E SOGL Art. 157 | manual (PDF) | P |
| 9 | Forecast errors (Art. 10) | hourly fc vs actual | Elia load/wind/solar forecast vs actual | ENTSO-E D-A forecast vs actual | derive (fc−actual) | P |
| 10 | Interconnectors (Annex 1) | hourly NTC/flows | Elia cross-border; JAO (CORE FB) | ENTSO-E flows / NTC | API | P |
| 11 | Market prices (Art. 3) | hourly/15-min | Elia imbalance; EPEX BE | ENTSO-E Day-Ahead Prices | API | P (intraday R) |
| 12 | RES curtailment (Art. 8) | hourly per tech | Elia congestion/redispatch dataset | ENTSO-E Generation Curtailment | API + model `resCurt` | P |
| 13 | DSO constraints (Art. 14) | per feeder, MW | Fluvius hosting-capacity GIS; ORES/RESA/Sibelga | CEER DNDP summaries; EDSO | Fluvius ArcGIS API; else manual | P (uneven) |
| 14 | Flexible resources (Art. 9/10) | MW by type + hourly avail | Elia BSP lists; CREG report; Adequacy study | ENTSO-E MARI/PICASSO; ERAA DSR | manual | P (asset-level R) |
| 15 | Prequalification limits (Art. 14) | per-asset status | Elia prequalification rules (per-asset bilateral) | ENTSO-E balancing reports (frameworks) | manual / none public | R |
| 16 | DNDP data (Art. 14) | per-DSO multi-year plan | Fluvius/ORES/RESA/Sibelga plans (VREG/CWaPE/BRUGEL) | other MS DNDPs (NRA, Dir. Art. 32) | manual (PDF) | P |

**Weakest links:** prequalification (#15, no public per-asset registry),
DSO/DNDP (#13/#16, uneven regional digitalisation — Fluvius best), and forecast
errors (#9, must be derived, no native dataset). Most primaries are **Elia Open
Data**; the consistent EU fallback is the **ENTSO-E Transparency Platform**.
When upgrading an `assumption` row to sourced data, record it in `source_id`
and it surfaces automatically in `19_DataQuality_Report`.
