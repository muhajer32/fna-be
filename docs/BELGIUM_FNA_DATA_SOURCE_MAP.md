# Belgium FNA data-source map

Companion to [ANNEX1_MAPPING.md](ANNEX1_MAPPING.md) and the
[ACER FNA compliance gap matrix](ACER_FNA_COMPLIANCE_GAP_MATRIX.md). Where that
note maps *workbook sheets* to Annex 1 field families, this note maps each
**input category** to the actual data source to pull from — primary (Belgium)
and fallback (any EU Member State / TSO-agnostic), the extraction method, and
known limitations. Use it when replacing `assumption`/`placeholder` rows
(`data_quality` column) with sourced data, and to populate `source_id` /
`20_Sources`.

Legend for **access**: `public` = no registration or free registration;
`restricted` = requires market-party status, NDA, or paid subscription.

---

## 1. Demand (electricity load)

| | |
|---|---|
| **ACER article/annex** | Annex 1 (system data), feeds Art. 8/9/10 |
| **Required granularity** | Hourly (or 15-min), per zone/country, multi-year history + scenario years |
| **Belgium source** | Elia Open Data Portal — "Total Load" / "Load on the Belgian grid"; Elia "Adequacy & Flexibility Study" load scenarios for 2025/2030 |
| **EU-wide fallback** | ENTSO-E Transparency Platform — `Total Load - Day Ahead / Actual` (per bidding zone) |
| **Extraction method/API** | ENTSO-E Transparency REST API (used by [rep_days.py](../python/rep_days.py)) or Elia Open Data API (Socrata/OData); ERAA demand scenarios are manual XLSX download |
| **Limitation** | Historical actuals only — target-year (2030) demand requires scenario assumptions (electrification of heat/EV) not in either API; Elia load excludes some embedded generation netting |
| **Access** | Public |

## 2. Wind generation

| | |
|---|---|
| **ACER article/annex** | Art. 8 (RES integration), Annex 1 RES capacity + availability |
| **Required granularity** | Hourly capacity factor, split onshore/offshore, multi climate-year |
| **Belgium source** | Elia Open Data — "Wind power production estimation and forecast" (onshore + offshore split); Elia "Power Generation in Belgium" registry for installed capacity |
| **EU-wide fallback** | ENTSO-E Transparency — `Actual Generation per Production Type` (Wind Onshore/Offshore); PECD (Pan-European Climate Database, ENTSO-E ERAA dataset) for climate-year CFs |
| **Extraction method/API** | Elia Open Data API for BE actuals (already used as `08_RES_CF_Profiles` proxy); PECD bulk CSV/NetCDF download for [monte_carlo.py](../python/monte_carlo.py) climate years |
| **Limitation** | Offshore wind concentrated in ~9 parks — single-node CF profile masks intra-zone variability; PECD CFs are simulated, not metered, and need rescaling to actual installed capacity |
| **Access** | Public |

## 3. Solar PV generation

| | |
|---|---|
| **ACER article/annex** | Art. 8, Annex 1 RES capacity + availability |
| **Required granularity** | Hourly capacity factor, multi climate-year |
| **Belgium source** | Elia Open Data — "Solar power production estimation and forecast"; Elia/CREG annual PV capacity statistics (incl. residential) |
| **EU-wide fallback** | ENTSO-E Transparency — `Actual Generation per Production Type` (Solar); PECD solar CF series |
| **Extraction method/API** | Elia Open Data API (estimation methodology documented separately); PECD bulk download for Monte Carlo |
| **Limitation** | Behind-the-meter residential PV is *estimated* by Elia from a sample of inverters/telemetry, not directly metered — systematic error grows with rooftop PV penetration |
| **Access** | Public |

## 4. Hydro (incl. pumped storage as generation)

| | |
|---|---|
| **ACER article/annex** | Annex 1 generation fleet (06_DispatchableBlocks) |
| **Required granularity** | Installed capacity + hourly generation/pumping |
| **Belgium source** | Elia Open Data — `Actual Generation per Production Type` (Hydro Pumped Storage, Hydro Run-of-river); Coo-Trois-Ponts technical data (ENGIE Electrabel public factsheets) |
| **EU-wide fallback** | ENTSO-E Transparency — `Actual Generation per Production Type` (Hydro categories) |
| **Extraction method/API** | ENTSO-E/Elia API for actuals |
| **Limitation** | Belgium hydro is negligible (~1.3 GW pumped storage at Coo + Plate-Taille, no significant run-of-river) — reservoir levels and pump/turbine schedules are commercially confidential |
| **Access** | Public (aggregate generation); restricted (reservoir levels, dispatch schedules) |

## 5. Storage (batteries, other than pumped hydro)

| | |
|---|---|
| **ACER article/annex** | Art. 9/10, `09_FlexStorage` |
| **Required granularity** | Installed power/energy capacity, efficiency, by target year |
| **Belgium source** | Elia "Power Generation in Belgium" capacity register (grid-connected BESS); CREG annual market monitoring report (battery storage chapter); Elia connection-request queue (pipeline projects) |
| **EU-wide fallback** | ENTSO-E TYNDP/ERAA storage capacity database (installed + planned, by Member State) |
| **Extraction method/API** | Manual extraction from Elia capacity register (CSV/PDF) and CREG report; ERAA database is a structured XLSX |
| **Limitation** | Small/behind-the-meter residential batteries and aggregator-controlled fleets are not individually registered — only grid-connected/metered assets above reporting thresholds appear |
| **Access** | Public (aggregate capacity); restricted (asset-level connection data) |

## 6. Thermal fleet (technical parameters)

| | |
|---|---|
| **ACER article/annex** | Annex 1 generation fleet, `06_DispatchableBlocks` (Pmin, ramp rate, min up/down, start cost) |
| **Required granularity** | Per-unit, by target year (incl. planned closures e.g. nuclear phase-out) |
| **Belgium source** | Elia "Power Generation in Belgium" / grid-connection register (capacities, fuel type, commissioning/decommissioning dates); FPS Economy nuclear phase-out schedule |
| **EU-wide fallback** | ENTSO-E Transparency — `Installed Capacity per Production Unit`; ERAA generation database (technical parameters by technology cluster) |
| **Extraction method/API** | Manual compilation from Elia register + ERAA generation database (technology-level Pmin/ramp/start-up defaults where unit-level data is unavailable) |
| **Limitation** | Unit-level ramp rates, min up/down times and start-up costs are commercially sensitive and rarely published — ERAA technology-cluster defaults are the realistic substitute, not true unit data |
| **Access** | Public (capacities, commissioning dates); restricted (unit-level technical/commercial parameters) |

## 7. Outages (planned and forced unavailability)

| | |
|---|---|
| **ACER article/annex** | Annex 1 unavailability, `11_Availability_Outages` |
| **Required granularity** | Per-unit, daily/hourly start-end windows, by cause (planned/forced) |
| **Belgium source** | Elia Open Data — "Generation unavailability" (REMIT Unavailability of Production Units, UMM) |
| **EU-wide fallback** | ENTSO-E Transparency — `Unavailability of Generation Units` (UMM, all REMIT-reporting units, EU-wide) |
| **Extraction method/API** | ENTSO-E Transparency REST API / Elia Open Data API — both expose the same REMIT feed for BE units |
| **Limitation** | REMIT reporting threshold is 100 MW (generation) / 200 MW (consumption) — smaller units (most DR, batteries, small thermal) have no public outage record; only covers *announced* outages, not realised derating |
| **Access** | Public |

## 8. Reserve requirements (FCR/aFRR/mFRR dimensioning)

| | |
|---|---|
| **ACER article/annex** | Art. 10 (short-term flexibility), `12_Reserve_ForecastError` |
| **Required granularity** | System-level MW requirement per reserve product, by target year |
| **Belgium source** | Elia "Ancillary Services — dimensioning of reserve needs" reports; Elia FCR/aFRR/mFRR yearly volume reports |
| **EU-wide fallback** | ENTSO-E SOGL Art. 157 reserve-sizing reports (published per synchronous area / per TSO); ENTSO-E "FCR Dimensioning" report (Continental Europe) |
| **Extraction method/API** | Manual — these are published as periodic PDF/XLSX reports, not API feeds |
| **Limitation** | Published periodically (typically annual), not as a continuous time series — no native hourly granularity; values must be mapped onto `02_RepHours`/`03_RepDays` manually |
| **Access** | Public |

## 9. Forecast errors (load / wind / solar D-1 vs realised)

| | |
|---|---|
| **ACER article/annex** | Art. 10, `12_Reserve_ForecastError` (short-term percentile method) |
| **Required granularity** | Hourly, paired forecast + actual, multi-year history, by season |
| **Belgium source** | Elia Open Data — "Load forecast" and "Wind/Solar forecast" series alongside the actuals already used for items 1-3 |
| **EU-wide fallback** | ENTSO-E Transparency — `Day-Ahead Total Load Forecast` and `Generation Forecasts for Wind and Solar` vs `Actual` |
| **Extraction method/API** | Same ENTSO-E/Elia API pulls as items 1-3, paired and differenced (`forecast - actual`) to derive empirical std-dev per season — this is the function proposed in the gap matrix (`compute_forecast_error_stats`) |
| **Limitation** | No official "forecast error" dataset exists — must be derived; forecast methodology changes over time (Elia revises its wind/solar forecast models), so longer history mixes vintages |
| **Access** | Public |

## 10. Interconnectors (capacities and flows)

| | |
|---|---|
| **ACER article/annex** | Annex 1, `04_Interconnectors` / `05_IntercoProfiles` |
| **Required granularity** | Hourly NTC/ATC and physical flows, per border (FR, NL, DE/ALEGrO, GB/Nemo, LU) |
| **Belgium source** | Elia Open Data — "Cross-border flows" and "Net Transfer Capacities"; JAO Publication Tool for CORE/CWE flow-based domains (Belgium is in CORE FB) |
| **EU-wide fallback** | ENTSO-E Transparency — `Cross-Border Physical Flows` and `Offered Capacity / NTC` |
| **Extraction method/API** | ENTSO-E Transparency REST API (used in [rep_days.py](../python/rep_days.py)); JAO Publication Tool API for flow-based domain data (max import/export per border per hour) |
| **Limitation** | CORE region uses flow-based capacity allocation, not simple bilateral NTCs — mapping flow-based domain constraints onto the single-node `04_Interconnectors` capacities is a simplification (already flagged in ANNEX1_MAPPING "Known deviations") |
| **Access** | Public |

## 11. Market prices (day-ahead, intraday, imbalance)

| | |
|---|---|
| **ACER article/annex** | Annex 1 (price signals for cost-efficiency assessment, Art. 3) |
| **Required granularity** | Hourly/15-min day-ahead price, 15-min imbalance price |
| **Belgium source** | Elia Open Data — "Imbalance prices"; EPEX SPOT Belgium day-ahead auction results (BE bidding zone) |
| **EU-wide fallback** | ENTSO-E Transparency — `Day-Ahead Prices` (per bidding zone, all EU) |
| **Extraction method/API** | ENTSO-E Transparency REST API for day-ahead; Elia Open Data API for imbalance price (15-min) |
| **Limitation** | ENTSO-E day-ahead prices are free and hourly; granular order-book / intraday continuous trade data requires an EPEX/Nasdaq subscription |
| **Access** | Public (day-ahead, imbalance); restricted (intraday order book, historical bulk via vendor) |

## 12. RES curtailment

| | |
|---|---|
| **ACER article/annex** | Art. 8 (RES integration needs), `40_FNA_RES_Integration` |
| **Required granularity** | Hourly, per technology (wind/solar), MW curtailed |
| **Belgium source** | Elia Open Data — "Congestion management / redispatching" dataset (includes RES curtailment volumes); Elia annual reports occasionally publish aggregate curtailed energy |
| **EU-wide fallback** | ENTSO-E Transparency — `Generation Curtailment` (where reported; coverage is uneven across Member States) |
| **Extraction method/API** | Elia Open Data API (redispatch/curtailment dataset); manual cross-check against Elia annual report figures |
| **Limitation** | Historically very low curtailment volumes in Belgium make the empirical series noisy/near-zero — model-derived curtailment (`resCurt` in GAMS) is currently the only forward-looking source; redispatch dataset mixes RES curtailment with thermal redispatch and needs filtering |
| **Access** | Public |

## 13. DSO network constraints (hosting capacity, congestion)

| | |
|---|---|
| **ACER article/annex** | Art. 14 (network-related flexibility needs), `13_NetworkNeeds` / `13b_DSO_Zones` |
| **Required granularity** | Per substation/feeder, hosting-capacity headroom (MW), by voltage level |
| **Belgium source** | Fluvius Open Data — "Hosting capacity" GIS layers (Flanders DSO, covers ~70% of BE connections); ORES / RESA open-data portals (Wallonia DSOs, more limited); Sibelga (Brussels) network plan |
| **EU-wide fallback** | CEER / national regulator "Distribution Network Development Plan" (DNDP) summaries; EDSO for Smart Grids hosting-capacity benchmarking reports |
| **Extraction method/API** | Fluvius Open Data portal exposes a hosting-capacity map/API (ArcGIS REST); ORES/RESA and Sibelga data is largely manual PDF/GIS download |
| **Limitation** | Granularity, update frequency and digital availability vary sharply by DSO — Flanders (Fluvius) is the most digitalised, Wallonia/Brussels DSOs lag; aggregating feeder-level hosting capacity into the single `13b_DSO_Zones` regional rows requires manual GIS aggregation |
| **Access** | Public (Fluvius); public but limited (ORES/RESA, Sibelga) |

## 14. Flexible resources (DR, EV, heat pumps, aggregators)

| | |
|---|---|
| **ACER article/annex** | Art. 9/10, `09_FlexStorage` / `10_FlexAvailability` |
| **Required granularity** | Aggregate available capacity (MW) by resource type, hourly availability profile, by target year |
| **Belgium source** | Elia "List of Balancing Service Providers" and aFRR/mFRR registered volumes (published periodically); CREG market monitoring report (DR/aggregator capacity chapter); Elia "Adequacy and Flexibility Study" flexibility-potential scenarios |
| **EU-wide fallback** | ENTSO-E MARI/PICASSO platform statistics (EU-wide balancing-market volumes by product); ERAA demand-side response potential database |
| **Extraction method/API** | Manual — Elia BSP lists and CREG reports are periodic PDF/XLSX, not API feeds |
| **Limitation** | Asset-level/aggregator-level capacity is commercially confidential — only market-level aggregates are published; hourly *availability* profiles for DR/EV/heat-pump flexibility are not measured anywhere and remain modelling assumptions |
| **Access** | Public (aggregate volumes); restricted (asset/aggregator-level data) |

## 15. Prequalification limits (reserve product prequalification status)

| | |
|---|---|
| **ACER article/annex** | Art. 14 / Annex 1 unavailability due to prequalification, `10b_Prequalification_Log` |
| **Required granularity** | Per flexibility resource, prequalification status (qualified/temporary_limit/unavailable) and date |
| **Belgium source** | Elia "Prequalification process" technical requirement documents (FCR/aFRR/mFRR) — define the *rules*; actual per-asset prequalification status is communicated bilaterally between Elia and the BSP |
| **EU-wide fallback** | ENTSO-E "Electricity Balancing" market reports describe prequalification frameworks per TSO; no EU-wide per-asset registry exists |
| **Extraction method/API** | Manual — no public API; per-asset status would have to come from the asset owner/aggregator directly |
| **Limitation** | This is the least observable category in the whole map — prequalification outcomes for individual units are not published by any TSO for confidentiality/competition reasons; `10b_Prequalification_Log` will remain an `assumption`-quality sheet unless the model is run by/with a market party that holds its own prequalification records |
| **Access** | Restricted (per-asset); public (process rules/requirements only) |

## 16. DNDP (Distribution Network Development Plan) data

| | |
|---|---|
| **ACER article/annex** | Art. 14, `13_NetworkNeeds` (timeframe = structural, zone_id = DSO) |
| **Required granularity** | Per-DSO, multi-year investment plan with forecast congestion/hosting-capacity needs |
| **Belgium source** | Fluvius "Investeringsplan" (Flanders DNDP, submitted to VREG); ORES/RESA "Plan d'adaptation" (Wallonia DNDPs, submitted to CWaPE); Sibelga investment plan (submitted to BRUGEL) |
| **EU-wide fallback** | CEER "Benchmarking report on DSO network development plans" (cross-country summary); other Member States' DNDPs filed with their national regulator (NRA) under the recast Electricity Directive Art. 32 |
| **Extraction method/API** | Manual — DNDPs are submitted as PDF documents to the relevant regional regulator (VREG/CWaPE/BRUGEL); no machine-readable API |
| **Limitation** | DNDPs are qualitative/narrative for the most part, published every ~2 years, and use inconsistent formats across the three Belgian regions — extracting comparable `need_value`/`time_block_or_rep_day` rows for `13_NetworkNeeds` requires manual interpretation per DSO |
| **Access** | Public (filed with regional regulators, generally downloadable from VREG/CWaPE/BRUGEL websites) |

---

## How to use this map

1. When upgrading a `13_NetworkNeeds`, `09_FlexStorage`, `10_FlexAvailability`,
   `11_Availability_Outages` or `12_Reserve_ForecastError` row from
   `assumption`/`placeholder` to `historical`/`empirical`, record the source in
   `source_id` and cross-reference the row number here in `20_Sources`.
2. For categories marked **restricted**, the realistic path is either (a) run
   the model as/with a market party that has the access, or (b) keep the
   `assumption` quality flag and surface it via `19_DataQuality_Report`
   (see [data_quality_report.py](../python/data_quality_report.py)) rather than
   inventing a precision the input data doesn't have.
3. For Monte Carlo climate-year sampling (`monte_carlo.py`), items 2-3 should
   use the **same PECD vintage and climate-year set** across demand, wind and
   solar — see ANNEX1_MAPPING "Climate years".
