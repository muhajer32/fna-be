# Changes v2 -> v3: assessment issues addressed

Each item below corresponds to a gap raised in the FNA assessment (GitHub-specific
items excluded as requested).

### 1. Hard-coded ENTSO-E API key
`config.py` no longer contains a default key. The key is read only from
`ENTSOE_API_KEY` (environment or `.env`). `.env.example` documents this.

### 2. Hard-coded representative-day cluster pin (`= 10`)
Restored to env/Excel-driven: `REPRESENTATIVE_DAY_CLUSTERS` defaults to `None`,
and `rep_days.py` falls back to the number of rows in `03_RepDays`. The
"TEMPORARY" note was removed.

### 3. Representative-day weights summed to 362, not 365
`cluster_representative_days` now rescales cluster sizes so weights sum to
`REPRESENTATIVE_DAY_TARGET_DAYS` (default 365). Weighted annual demand and energy
are no longer understated by the dropped daylight-saving days.

### 4. Short-term flexibility was a fixed-percentage proxy
Replaced with the ACER percentile method (`_short_term_needs` in `io_excel.py`):
component forecast-error shares are read as standard deviations, combined in
quadrature into a residual-load error sigma, and scaled by the percentile z-score
(P99.9 up, P0.1 down; configurable in `01_Control`). The largest-outage stress is
added to the upward need only.

### 5. Storage formulation
`uc_ed_model_v3.gms` rewrites storage with explicit `chargeF`/`dischargeF`,
round-trip efficiency applied on charging only, and an optional cyclic SOC
(`use_storage_cyclic_SOC`) enforced via the new `lastT` / `sameDay` sets, so
storage cannot harvest free energy from the daily reset.

### 6. No full-year hourly benchmark
New `build_full_year.py` writes an 8760-hour copy of the workbook (weights 1/24
per hour). The same model runs on it, so the representative-day compression error
can be quantified by comparing sheets 30 and 40-43.

### 7. No ACER-native indicator post-processing
New `fna_indicators.py` plus output sheets 40-43:
RES integration (seasonal/daily/hourly), ramping percentiles per MTU, short-term
percentile bands, and a weighted residual-load duration curve.

### 8. Network / Article-14 fine-tuning inactive
`13_NetworkPlaceholder` renamed to `13_NetworkNeeds` with an `active_in_run`
column. When `use_network=1`, `io_excel.py` parses downward hosting caps and
upward local needs and the model's `networkDownLimit` / `networkUpReserve`
constraints bind. Dormant when off.

### 9. ERAA/NRAA data lineage not formalised
`docs/ANNEX1_MAPPING.md` maps every input table to an ACER Annex 1 field family
and lists the official source that should replace each proxy.

### 10. Hard-coded parameters in code
New model coefficients (cyclic SOC, network penalty, short-term percentiles) all
live in `01_Control`, not in code. `config.py` values are fallbacks only.

## File renames / additions
- `uc_ed_model_v2.gms` -> `gams/uc_ed_model_v3.gms`
- added `fna_indicators.py`, `build_full_year.py`
- added `README.md`, `requirements.txt`, `.env.example`, `docs/ANNEX1_MAPPING.md`
- workbook sheet `13_NetworkPlaceholder` -> `13_NetworkNeeds`
