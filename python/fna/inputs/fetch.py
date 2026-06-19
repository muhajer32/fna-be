"""
fetch.py - one-off data pull for the full-year countrycode FNA input workbook.

Pulls calendar-year hourly data for a specified ENTSO-E country and caches each
series as CSV under data/inputs/raw_{countrycode}{year}/, so the
workbook-building script (build_full_year_workbook.py) can run repeatedly
without re-hitting the API.

Each series is fetched independently and wrapped in try/except so a single
failing query does not abort the whole pull - missing series simply mean the
corresponding workbook columns stay as documented assumptions.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


from fna.config import ENTSOE_API_KEY, ENTSOE_TIMEZONE, raw_data_dir  # noqa: E402

# Module globals set per pull by fetch_country_year(); defaults to the first known country.
_NEIGHBOURS = {
    "BE": {"BE_FR": "FR", "BE_NL": "NL", "BE_DE": "DE_LU", "BE_UK": "GB", "BE_LU": "LU"},
}
COUNTRY = next(iter(_NEIGHBOURS)) if _NEIGHBOURS else "BE"
TZ = "Europe/Brussels"
# START/END left unset until fetch_country_year runs so defaults don't remain hard-coded
START: pd.Timestamp | None = None
END: pd.Timestamp | None = None
OUT_DIR = raw_data_dir(COUNTRY, 2023)
BORDERS = dict(_NEIGHBOURS.get(COUNTRY, {}))


def hourly(series: pd.Series, name: str) -> pd.Series:
    s = pd.Series(series).copy()
    if isinstance(s.index, pd.DatetimeIndex):
        s = s.resample("1h").mean()
    s.name = name
    return pd.to_numeric(s, errors="coerce")


def save(df: pd.DataFrame, name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.csv"
    df.to_csv(path)
    print(f"wrote {path} ({len(df)} rows)")


def fetch_country_year(country: str, year: int, out_dir: "Path", borders: "dict | None" = None) -> Path:
    """Pull a calendar year of ENTSO-E hourly data for COUNTRY into ``out_dir``.

    Produces the same CSV set the full-year builder expects (load, RES actual/
    forecast, day-ahead prices, cross-border flows, installed capacity, UMM
    outages). Neighbours default to the built-in map for known countries; pass
    ``borders={'XX_YY': 'YY', ...}`` for others."""

    global COUNTRY, OUT_DIR, START, END, BORDERS
    COUNTRY = str(country).strip().upper()
    OUT_DIR = Path(out_dir)
    tz = ENTSOE_TIMEZONE or "Europe/Brussels"
    START = pd.Timestamp(f"{int(year)}-01-01", tz=tz)
    END = pd.Timestamp(f"{int(year) + 1}-01-01", tz=tz)
    BORDERS = dict(borders) if borders else dict(_NEIGHBOURS.get(COUNTRY, {}))
    if not BORDERS:
        print(f"No neighbour map for {COUNTRY}; cross-border flows will be skipped.")
    _pull()
    return OUT_DIR


def main() -> None:
    """Default pull using built-in country/year defaults into OUT_DIR."""
    fetch_country_year(COUNTRY, START.year, OUT_DIR)


def _pull() -> None:
    from entsoe import EntsoePandasClient

    client = EntsoePandasClient(api_key=ENTSOE_API_KEY)

    # --- Load: actual + day-ahead forecast -------------------------------
    load_actual = hourly(client.query_load(COUNTRY, start=START, end=END).iloc[:, 0], "load_actual")
    try:
        load_fc = hourly(client.query_load_forecast(COUNTRY, start=START, end=END).iloc[:, 0], "load_forecast")
    except Exception as exc:
        print("load forecast failed:", exc)
        load_fc = pd.Series(dtype=float, name="load_forecast")
    save(pd.concat([load_actual, load_fc], axis=1), "load")

    # --- RES generation: wind onshore/offshore, solar (actual) ------------
    res_actual = {}
    for psr, name in [("B18", "wind_offshore"), ("B19", "wind_onshore"), ("B16", "solar")]:
        try:
            gen = client.query_generation(COUNTRY, start=START, end=END, psr_type=psr)
            if isinstance(gen, pd.DataFrame):
                gen = gen.iloc[:, 0]
            res_actual[name] = hourly(gen, name)
        except Exception as exc:
            print(f"generation {name} ({psr}) failed:", exc)
    save(pd.concat(res_actual.values(), axis=1) if res_actual else pd.DataFrame(), "res_generation_actual")

    # --- RES forecast: wind + solar (combined and/or split) ---------------
    res_fc = {}
    for psr, name in [("B18", "wind_offshore_fc"), ("B19", "wind_onshore_fc"), ("B16", "solar_fc")]:
        try:
            fc = client.query_wind_and_solar_forecast(COUNTRY, start=START, end=END, psr_type=psr)
            if isinstance(fc, pd.DataFrame):
                fc = fc.iloc[:, 0]
            res_fc[name] = hourly(fc, name)
        except Exception as exc:
            print(f"forecast {name} ({psr}) failed:", exc)
    if not res_fc:
        try:
            fc = client.query_wind_and_solar_forecast(COUNTRY, start=START, end=END)
            if isinstance(fc, pd.DataFrame):
                for col in fc.columns:
                    res_fc[f"combined_{col}"] = hourly(fc[col], f"combined_{col}")
        except Exception as exc:
            print("combined wind+solar forecast failed:", exc)
    save(pd.concat(res_fc.values(), axis=1) if res_fc else pd.DataFrame(), "res_generation_forecast")

    # --- Day-ahead prices ---------------------------------------------------
    try:
        prices = hourly(client.query_day_ahead_prices(COUNTRY, start=START, end=END), "price_eur_mwh")
        save(prices.to_frame(), "day_ahead_prices")
    except Exception as exc:
        print("day-ahead prices failed:", exc)

    # --- Cross-border physical flows (both directions per border) --------
    flow_cols = {}
    for border_id, code in BORDERS.items():
        try:
            out_flow = hourly(client.query_crossborder_flows(COUNTRY, code, start=START, end=END), f"{border_id}_export")
            in_flow = hourly(client.query_crossborder_flows(code, COUNTRY, start=START, end=END), f"{border_id}_import")
            flow_cols[f"{border_id}_export"] = out_flow
            flow_cols[f"{border_id}_import"] = in_flow
        except Exception as exc:
            print(f"flows {border_id} failed:", exc)
    save(pd.concat(flow_cols.values(), axis=1) if flow_cols else pd.DataFrame(), "cross_border_flows")

    # --- Installed generation capacity (latest snapshot for 2023) --------
    try:
        cap = client.query_installed_generation_capacity(COUNTRY, start=START, end=END)
        save(cap, "installed_capacity")
    except Exception as exc:
        print("installed capacity failed:", exc)

    # --- Generation unavailability (UMM, for outage calibration) ---------
    try:
        unavail = client.query_unavailability_of_generation_units(
            COUNTRY, start=START, end=END, docstatus=None
        )
        save(unavail, "generation_unavailability")
    except Exception as exc:
        print("generation unavailability failed:", exc)

    print("Done.")


if __name__ == "__main__":
    main()
