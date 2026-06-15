# ACER FNA guiding criteria - traceability (Art. 3)

This note maps the general guiding criteria for assessing flexibility needs to
concrete evidence in this repository. It complements
`docs/ACER_FNA_COMPLIANCE_GAP_MATRIX.md` (item 2.9) by making explicit *how*
each criterion is addressed, so reviewers don't have to infer it from code.

| Guiding criterion | Repo evidence | Notes / residual gap |
|---|---|---|
| **Transparency of data sources** | Every input sheet carries `source_id`, `data_quality`, `notes` columns (e.g. `11_Availability_Outages`, `12_Reserve_ForecastError`, `13_NetworkNeeds`, `17_Barriers_Digitalisation`). [docs/ANNEX1_MAPPING.md](ANNEX1_MAPPING.md) maps each table to its ACER Annex 1 family and the official source that should replace it. [19_DataQuality_Report](#) (new, [data_quality_report.py](../python/data_quality_report.py)) aggregates the `data_quality` distribution per sheet. | Most values remain `assumption`/`placeholder`; the report makes that visible rather than fixing it. |
| **Technology neutrality** | All flexibility resources (storage, EV/heat-pump DR, industrial DR) are represented through the same `flexUpCap`/`flexDnCap`/`flexAvailUp/Dn`/`flexCost`/`flexEff` parameters and the same `chargeF`/`dischargeF`/`resUpFlex`/`resDnFlex` variables in [uc_ed_model_v3.gms](../gams/uc_ed_model_v3.gms) — no resource type gets a bonus/penalty term. | `09_FlexStorage.activation_cost_EUR_MWh` and `availability_basis` differ by resource (legitimately, reflecting real technical limits), not by a technology label. |
| **Non-discrimination between flexibility providers** | Reserve and short-term needs (`reserveUpEq`/`reserveDnEq`) are met by `SUM(gd, resUpGen) + SUM(f, resUpFlex)` — generation and flexibility portfolios compete on the same constraint with no preferential ordering. | Network fine-tuning (`networkUpReserve`) likewise adds to the same pooled requirement. |
| **Cost-efficiency / least-cost dispatch** | The objective `obj` in `uc_ed_model_v3.gms` minimises total system cost (dispatch + start-up + curtailment penalty + flex activation + trade + ENS + slack penalties); flexibility is only activated when cheaper than the alternative. | `curt_penalty`, `reserve_slack_penalty`, `network_slack_penalty` are tunable in `01_Control` — large values can distort which option looks "cheapest"; document chosen values when publishing results. |
| **Granularity matching the need's timeframe** | Hourly representative-day resolution for RES integration / ramping / short-term (Art. 8-10); seasonal breakdowns added in `41b_FNA_Ramping_Capacity` and `42b_FNA_ShortTerm_BySeason`; DSO/TSO/Art.14 needs (sheets 44/45/47) carry `annual`/`seasonal` levels via [network_needs.py](../python/network_needs.py). | Single-node spatial granularity throughout (see ANNEX1_MAPPING "Known deviations"). |
| **Distinguishing structural vs. fine-tuning (near-real-time) needs** | `13_NetworkNeeds.timeframe` (`structural` / `fine_tuning`, added by `migrate_v3_1_workbook.py`) routes entries to `45_FNA_TSO_Needs` (structural) vs. `47_FNA_FineTuning_Art14` (fine-tuning) via `network_needs.compute_tso_needs` / `compute_fine_tuning_needs`. | Only one example row exists; needs real DSO/TSO input to be meaningful. |
| **Traceability of assumptions to scenario/target year** | `01_Control.target_year`/`future_year` select `*_2025`/`*_2030` columns consistently across `06_DispatchableBlocks`, `07_RES_Portfolios`, `09_FlexStorage`, `04_Interconnectors`. `_pick_capacity_col` in [fna_indicators.py](../python/fna_indicators.py) applies the same year-selection logic to the new unavailability indicator. | Only two target years; ACER expects the methodology to be repeatable for any adopted scenario set. |
| **Avoiding double-counting between need categories** | RES integration (curtailment), ramping (residual-load diff), short-term (forecast-error percentile reserve) and network needs (hosting-cap slack) are computed from distinct GAMS variables (`resCurt`, `residualLoad` diff, `shortUpNeed`/`shortDnNeed`, `networkSlack`/`nwUpReqT`), each entering the objective/constraints once. | Unavailability needs (`46_FNA_Unavailability_Needs`) are diagnostic/additive, not yet fed back into the optimisation — flag this when comparing totals across sheets. |

## How to use this table

- When adding a new indicator or input sheet, add a row here describing which
  criterion it serves (or note if it's purely diagnostic).
- When replacing an `assumption`/`placeholder` value with an official source,
  update `19_DataQuality_Report` will reflect the change automatically on the
  next run — no manual edit needed here unless the *mechanism* changes.
