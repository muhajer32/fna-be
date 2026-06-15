# PECD Data

The Pan-European Climate Database (PECD) is produced by ENTSO-E / JRC and is
subject to restricted redistribution terms. It is therefore **not included** in
this repository.

## How to obtain it

1. Register on the [ENTSO-E Data Portal](https://www.entsoe.eu/data/) or
   contact your national TSO for access.
2. Download the PECD 2021.3 Parquet files for the technologies and years you
   need (Solar PV, Onshore Wind, Offshore Wind, Demand — country level).
3. Place the `.parquet` files in this directory. The expected naming convention
   is `PECD-2021.3-country-<Tech>-<Year>.parquet`, e.g.:
   - `PECD-2021.3-country-LFSolarPV-2030.parquet`
   - `PECD-2021.3-country-Onshore-2030.parquet`
   - `PECD-2021.3-country-Offshore-2030.parquet`
   - `PECD-country-demand_national_estimates-2030.parquet`

4. Set `PECD_DATA_DIR=./data/pecd` in your `.env` file (or leave it at the
   default — it already points here).

> The Monte Carlo workflow (`python -m fna_be run-monte-carlo`) reads these
> files to draw climate-year samples. The deterministic workflow does not
> require them.
