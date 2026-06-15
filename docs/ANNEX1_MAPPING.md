# ACER Annex 1 data lineage

This note maps each workbook table to the corresponding ACER FNA Annex 1 field
family, so the prototype can later ingest ERAA / NRAA inputs without restructuring.
It does **not** claim ERAA/NRAA compliance: today most values are assumptions or
ENTSO-E-derived proxies (see `20_Sources`). The point is that every input already
has a defined slot to be replaced by an official source.

## Scenario and target years

ACER expects at least two target years, one a common EU policy year. The workbook
stores parallel `*_2025` and `*_2030` columns and selects one via `target_year`
in `01_Control`. To align with ERAA, set these columns from the ERAA reference
scenario installed capacities and the same climate years used below.

## TSO system inputs (Annex 1)

| Workbook sheet | Annex 1 field family | Replace with (ERAA/NRAA) |
|---|---|---|
| 02_RepHours (`gross_demand_MW_*`) | Demand time series, hourly | ERAA demand per climate year |
| 07_RES_Portfolios, 08_RES_CF_Profiles | RES installed capacity + hourly availability | ERAA RES capacities + PECD climate-year CFs |
| 06_DispatchableBlocks | Thermal fleet, technical limits (Pmin, ramp, min up/down, start-up) | ERAA generation database |
| 09_FlexStorage, 10_FlexAvailability | Storage / DSR, energy limits, hourly availability | ERAA storage + national DSR data |
| 04_Interconnectors, 05_IntercoProfiles | Cross-border exchange capacity and flows | ERAA NTC / flow-based, JAO |
| 11_Availability_Outages | Derating, forced and planned outages | ERAA outage patterns |
| 12_Reserve_ForecastError | Reserve requirements + forecast-error inputs for the short-term percentile method | Elia reserve dimensioning + historical forecast-error series |
| 13_NetworkNeeds | DSO/TSO network needs for Article-14 fine-tuning | DNDP / local flexibility plans |

## Outputs (Annex 1 expects at least hourly, per weather scenario)

| Sheet | Annex 1 output |
|---|---|
| 33_Residual | Residual load, RES availability, curtailment, ENS, net imports per hour |
| 31_Dispatch_Raw | Generation, storage, trade dispatch per hour |
| 40_FNA_RES_Integration | RES-integration need (seasonal / daily / hourly) |
| 41_FNA_Ramping | Ramping need per market time unit |
| 42_FNA_ShortTerm | Short-term need percentile bands |
| 50–53 | Per-weather-scenario distributions (Monte Carlo) |

## Climate years

PECD weather years drive the Monte Carlo layer (`monte_carlo.py`). To make the
lineage ERAA-consistent, restrict the sampled years (`PECD_YEARS`) to the ERAA
climate-year set and use the same PECD vintage for demand, wind and solar.

## Known deviations from full FNAM

- Single-node system; no nodal/zonal transmission detail.
- Representative-day compression (240 h) rather than full 8760 h — quantify the
  error with `build_full_year.py`.
- Short-term need is a parametric percentile of combined forecast-error sigmas,
  not yet a full empirical forecast-error distribution per target year.
- Network layer is a coarse Article-14 approximation, not a grid model.
