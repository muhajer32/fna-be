"""
io_excel.py - Belgium FNA-ED/UC v3
===================================
Reads the Excel workbook, writes a GAMS 25.1-compatible include file, parses
GAMS CSV outputs, and writes organized output tables back to Excel.

Design principles
-----------------
- Sheets are read by header row, not by hard-coded cell ranges.
- Target year is selected in 01_Control with the `target_year` parameter.
- Every model coefficient comes from Excel; none are hard-coded here.
- Long GAMS declarations are written vertically to respect GAMS 25.1 line limits.

v3 additions
------------
- Short-term up/down needs use the ACER percentile method (see _short_term_needs).
- Storage uses explicit charge/discharge plus optional cyclic SOC (socCyclic).
- The network sheet (13_NetworkNeeds) feeds an Article-14 fine-tuning layer.
- Emits firstT/lastT/sameDay so the GAMS model can close daily storage cycles.
"""
from __future__ import annotations
import copy
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore
import xlwings as xw  # type: ignore

from config import (
    MC_CONFIG_SHEET,
    MC_DEFAULTS,
    PROJECT_ROOT,
    SHORTTERM_DN_PERCENTILE,
    SHORTTERM_UP_PERCENTILE,
    WIND_RESOURCE_IDS,
)

log = logging.getLogger(__name__)

EXCEL_READ_CHUNK_ROWS = 5_000
EXCEL_WRITE_CHUNK_ROWS = 5_000

# Required sheets in the v2 workbook.
SHEETS = {
    "control": "01_Control",
    "hours": "02_RepHours",
    "days": "03_RepDays",
    "borders": "04_Interconnectors",
    "border_profiles": "05_IntercoProfiles",
    "dispatchable": "06_DispatchableBlocks",
    "res": "07_RES_Portfolios",
    "res_profiles": "08_RES_CF_Profiles",
    "flex": "09_FlexStorage",
    "flex_profiles": "10_FlexAvailability",
    "outages": "11_Availability_Outages",
    "reserves": "12_Reserve_ForecastError",
    "network": "13_NetworkNeeds",
}

# Sheets that may not exist yet in older workbooks. Read as empty frames when
# absent so new v3.1 indicators (network split, unavailability, barriers) stay
# dormant rather than breaking the whole run.
OPTIONAL_SHEETS = {
    "dso_zones": "13b_DSO_Zones",
    "prequalification": "10b_Prequalification_Log",
    "barriers": "17_Barriers_Digitalisation",
}


def read_inputs(wb: xw.Book) -> dict[str, Any]:
    """Read all v2 input tables from Excel into a single dictionary."""
    frames = {key: _read_sheet(wb, name) for key, name in SHEETS.items()}
    frames.update({key: _read_sheet_optional(wb, name) for key, name in OPTIONAL_SHEETS.items()})
    control = _control_dict(frames["control"])
    target_year = int(float(control.get("target_year", 2025)))
    log.info("Reading FNA v2 inputs for target year %s", target_year)
    return {"target_year": target_year, "control": control, "frames": frames}


def read_inputs_with_mc(
    wb: xw.Book,
    scenario_id: int | None = None,
    uncertainty_scenarios: dict[int, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Read base workbook inputs and optionally apply one uncertainty scenario."""

    inputs = read_inputs(wb)
    return apply_uncertainty_scenario(inputs, scenario_id, uncertainty_scenarios)


def apply_uncertainty_scenario(
    inputs: dict[str, Any],
    scenario_id: int | None = None,
    uncertainty_scenarios: dict[int, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Return a copied input dictionary with RES and load uncertainty applied."""

    updated = copy.deepcopy(inputs)
    if scenario_id is None or uncertainty_scenarios is None:
        return updated

    scenario = uncertainty_scenarios.get(scenario_id)
    if scenario is None:
        raise KeyError(f"Uncertainty scenario {scenario_id} not found.")

    cf_col = f"cf_scenario_{scenario_id}"
    demand_col = f"demand_multiplier_scenario_{scenario_id}"
    frames = updated["frames"]

    if cf_col in scenario.columns and {"time_id", "res_id"}.issubset(scenario.columns):
        res_profiles = frames["res_profiles"].copy()
        scenario_cf = scenario[["time_id", "res_id", cf_col]].dropna(subset=["res_id"]).copy()
        scenario_cf["res_id"] = scenario_cf["res_id"].astype(str)
        scenario_cf[cf_col] = pd.to_numeric(scenario_cf[cf_col], errors="coerce").clip(0.0, 1.0)
        merged = res_profiles.merge(scenario_cf, on=["time_id", "res_id"], how="left")
        mask = merged[cf_col].notna()
        merged.loc[mask, "capacity_factor"] = merged.loc[mask, cf_col]
        frames["res_profiles"] = merged.drop(columns=[cf_col])

    if demand_col in scenario.columns and "time_id" in scenario.columns:
        hours = frames["hours"].copy()
        target_year = int(updated["target_year"])
        demand_column = f"gross_demand_mw_{target_year}"
        if demand_column not in hours.columns:
            raise KeyError(f"Missing {demand_column} in 02_RepHours.")
        demand = scenario[["time_id", demand_col]].dropna(subset=[demand_col]).copy()
        demand[demand_col] = pd.to_numeric(demand[demand_col], errors="coerce").fillna(1.0).clip(0.0)
        hours = hours.merge(demand, on="time_id", how="left")
        mask = hours[demand_col].notna()
        hours.loc[mask, demand_column] = pd.to_numeric(hours.loc[mask, demand_column], errors="coerce").fillna(0.0) * hours.loc[mask, demand_col]
        frames["hours"] = hours.drop(columns=[demand_col])

    updated["scenario_id"] = scenario_id
    log.info("Applied uncertainty scenario %s", scenario_id)
    return updated


apply_wind_scenario = apply_uncertainty_scenario


def write_inc_files_mc(inputs: dict[str, Any], inc_dir: Path, out_dir: Path, scenario_id: int | None = None) -> None:
    """Write include files for one scenario."""

    write_inc_files(inputs, inc_dir, out_dir)
    if scenario_id is not None:
        log.info("Wrote include files for scenario %s in %s", scenario_id, inc_dir)


def read_uncertainty_params(wb: xw.Book) -> dict[str, Any]:
    """Read run and MC parameters from workbook sheets with config defaults."""

    defaults = _get_default_mc_params()
    params: dict[str, Any] = {}
    sheet_names = [MC_CONFIG_SHEET]
    seen_sheets: set[str] = set()
    for sheet_name in sheet_names:
        if sheet_name in seen_sheets:
            continue
        seen_sheets.add(sheet_name)
        sheet_params = _read_mc_params_from_sheet(wb, sheet_name)
        if sheet_params:
            params.update(sheet_params)

    if not params:
        log.warning("No MC parameters found in %s; using config.MC_DEFAULTS", sheet_names)

    for key, value in defaults.items():
        if key not in params or params[key] is None:
            params[key] = value

    log.info(
        "Run params: mc=%s, %s scenarios, use_pecd=%s, workers=%s",
        params["run_monte_carlo"],
        params["n_mc_scenarios"],
        params["use_pecd_data"],
        params["max_parallel_workers"],
    )
    return params


def _read_mc_params_from_sheet(wb: xw.Book, sheet_name: str) -> dict[str, Any]:
    try:
        sht = wb.sheets[sheet_name]
    except KeyError:
        log.info("Sheet %r not found while reading run parameters", sheet_name)
        return {}

    values = sht.used_range.options(ndim=2).value
    df = _parameter_table_from_values(values)
    if df.empty:
        log.warning("Sheet %r has no parameter/value table", sheet_name)
        return {}

    params: dict[str, Any] = {}
    for _, row in df.iterrows():
        key = _canonical_mc_key(row.get("parameter"))
        if not key:
            continue
        value = _parse_uncertainty_value(key, row.get("value"))
        if value is not None:
            params[key] = value
    return params


def _parameter_table_from_values(values: list[list[Any]] | None) -> pd.DataFrame:
    if not values:
        return pd.DataFrame()

    rows = [list(row) for row in values if row and any(cell not in (None, "") for cell in row)]
    for header_index, row in enumerate(rows[:10]):
        columns = [_clean_col(cell) for cell in row]
        if {"parameter", "value"}.issubset(columns):
            data = rows[header_index + 1 :]
            width = len(columns)
            normalized_rows = [r[:width] + [None] * max(0, width - len(r)) for r in data]
            return pd.DataFrame(normalized_rows, columns=columns).dropna(how="all")
    return pd.DataFrame()


def _canonical_mc_key(value: Any) -> str:
    key = _clean_col(value)
    aliases = {
        "mc": "run_monte_carlo",
        "monte_carlo": "run_monte_carlo",
        "scenarios": "n_mc_scenarios",
        "n_scenarios": "n_mc_scenarios",
        "number_of_scenarios": "n_mc_scenarios",
        "workers": "max_parallel_workers",
        "max_workers": "max_parallel_workers",
        "parallel_workers": "max_parallel_workers",
        "use_pecd": "use_pecd_data",
        "seed": "seed_random",
        "random_seed": "seed_random",
    }
    return aliases.get(key, key)


def _parse_uncertainty_value(key: str, value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if key in {"n_mc_scenarios", "seed_random", "pecd_target_year", "max_parallel_workers"}:
        return int(float(value))
    if key in {"run_monte_carlo", "use_pecd_data"}:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(int(float(value)))
    if key in {
        "wind_capacity_mw",
    }:
        return float(value)
    return value


def _get_default_mc_params() -> dict[str, Any]:
    return copy.deepcopy(MC_DEFAULTS)


def _read_sheet(wb: xw.Book, sheet_name: str) -> pd.DataFrame:
    if sheet_name not in [s.name for s in wb.sheets]:
        raise KeyError(f"Required sheet missing: {sheet_name}")
    sht = wb.sheets[sheet_name]
    region = sht.range("A1").expand()
    df = _read_region_as_dataframe(region)
    df.columns = [_clean_col(c) for c in df.columns]
    df = df.dropna(how="all")
    return df


def _read_region_as_dataframe(region: xw.Range) -> pd.DataFrame:
    """Read an Excel table in chunks to avoid macOS Apple Event size limits."""

    row_count = region.rows.count
    col_count = region.columns.count
    if row_count <= 0 or col_count <= 0:
        return pd.DataFrame()

    top = region.row
    left = region.column
    bottom = top + row_count - 1
    right = left + col_count - 1
    sheet = region.sheet

    header_values = sheet.range((top, left), (top, right)).options(ndim=2).value
    columns = _normalize_excel_row(header_values[0] if header_values else [], col_count)

    if row_count == 1:
        return pd.DataFrame(columns=columns)

    rows: list[list[Any]] = []
    for start_row in range(top + 1, bottom + 1, EXCEL_READ_CHUNK_ROWS):
        end_row = min(start_row + EXCEL_READ_CHUNK_ROWS - 1, bottom)
        values = sheet.range((start_row, left), (end_row, right)).options(ndim=2).value
        if values:
            rows.extend(_normalize_excel_row(row, col_count) for row in values)

    return pd.DataFrame(rows, columns=columns)


def _normalize_excel_row(row: list[Any], width: int) -> list[Any]:
    return list(row[:width]) + [None] * max(0, width - len(row))


def _read_sheet_optional(wb: xw.Book, sheet_name: str) -> pd.DataFrame:
    """Like `_read_sheet`, but returns an empty DataFrame if the sheet is absent."""
    try:
        return _read_sheet(wb, sheet_name)
    except KeyError:
        log.info("Optional sheet not found (skipping): %s", sheet_name)
        return pd.DataFrame()


def _control_dict(df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for _, row in df.iterrows():
        key = str(row.get("parameter", "")).strip()
        if key:
            out[key] = row.get("value")
    return out


def write_inc_files(inputs: dict[str, Any], inc_dir: Path, out_dir: Path) -> None:
    """Write config.inc and data.inc for the v2 GAMS model."""
    inc_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    target_year = inputs["target_year"]
    control = inputs["control"]
    f = inputs["frames"]

    # Prepare cleaned tables.
    hours = f["hours"].copy()
    hours["time_id"] = hours["time_id"].map(_label)
    hours["next_time_id"] = hours["next_time_id"].map(_label_or_blank)

    dispatchable = f["dispatchable"].copy()
    dispatchable["unit_group_id"] = dispatchable["unit_group_id"].map(_label)

    res = f["res"].copy()
    res["res_id"] = res["res_id"].map(_label)

    flex = f["flex"].copy()
    flex["flex_id"] = flex["flex_id"].map(_label)

    borders = f["borders"].copy()
    borders["border_id"] = borders["border_id"].map(_label)

    # IDs.
    t = list(hours["time_id"])
    gd = list(dispatchable["unit_group_id"])
    r = list(res["res_id"])
    flx = list(flex["flex_id"])
    b = list(borders["border_id"])

    hour_int = pd.to_numeric(hours["hour"], errors="coerce").fillna(-1).astype(int)
    first_t = list(hours.loc[hour_int.eq(0), "time_id"])
    # Last hour of each representative day = the hour whose next_time_id points
    # back to its own day's first hour, or simply the max hour within each day.
    rep_day_col = "rep_day_id" if "rep_day_id" in hours.columns else "chronology_group"
    last_idx = hours.assign(_h=hour_int).groupby(rep_day_col)["_h"].idxmax()
    last_t = list(hours.loc[last_idx, "time_id"])
    # sameDay pairs link the first and last hour of each representative day so the
    # GAMS model can enforce cyclic storage. Only first->last pairs are needed.
    day_first = dict(zip(hours.loc[hour_int.eq(0), rep_day_col], hours.loc[hour_int.eq(0), "time_id"]))
    same_day_pairs = []
    for _, row in hours.loc[last_idx].iterrows():
        day = row[rep_day_col]
        if day in day_first:
            same_day_pairs.append((day_first[day], row["time_id"]))

    next_pairs = [(row["time_id"], row["next_time_id"]) for _, row in hours.iterrows() if row.get("next_time_id")]

    # Helper column selectors by target year.
    def col(base: str) -> str:
        specific = f"{base}_{target_year}"
        if specific in dispatchable.columns or specific in res.columns or specific in flex.columns or specific in borders.columns or specific in hours.columns:
            return specific
        return base

    # Main parameters.
    demand = dict(zip(hours["time_id"], _num_series(hours[f"gross_demand_mw_{target_year}"])))
    weight = dict(zip(hours["time_id"], _num_series(hours["weight_days"])))

    # Dispatchable parameters.
    cap_block = _series(dispatchable, "unit_group_id", f"capacity_per_block_mw_{target_year}")
    n_blocks = _series(dispatchable, "unit_group_id", f"n_blocks_{target_year}")
    pmin = {k: _share(v) for k, v in _series(dispatchable, "unit_group_id", "pmin_pct").items()}

    var_cost = _series(dispatchable, "unit_group_id", "var_cost_eur_mwh_excl_co2")
    emis = _series(dispatchable, "unit_group_id", "emission_tco2_mwh")
    startup = _series(dispatchable, "unit_group_id", "startup_cost_eur_per_block")
    ramp_up = {k: v * 60 for k, v in _series(dispatchable, "unit_group_id", "ramp_up_mw_per_min_per_block").items()}
    ramp_dn = {k: v * 60 for k, v in _series(dispatchable, "unit_group_id", "ramp_down_mw_per_min_per_block").items()}

    # RES parameters.
    res_cap = _series(res, "res_id", f"capacity_mw_{target_year}")
    curt_share = { k: _share(v) for k, v in _series(res, "res_id", f"curtailable_share_{target_year}").items()}

    # Flex parameters.
    flex_up = _series(flex, "flex_id", f"up_capacity_mw_{target_year}")
    flex_dn = _series(flex, "flex_id", f"down_capacity_mw_{target_year}")
    flex_energy = _series(flex, "flex_id", f"energy_capacity_mwh_{target_year}")
    flex_eff = {k: _share(v) for k, v in _series(flex, "flex_id", "roundtrip_efficiency").items()}
    flex_cost = _series(flex, "flex_id", "activation_cost_eur_mwh")

    # Interconnector parameters.
    imp_cap = _series(borders, "border_id", f"import_capacity_mw_{target_year}")
    exp_cap = _series(borders, "border_id", f"export_capacity_mw_{target_year}")

    # Time-dependent 2D parameters.
    gen_avail = _make_2d(f["outages"], "unit_group_id", gd, "time_id", t, "availability_pct", default=1.0, as_share=True)
    res_cf = _make_2d(f["res_profiles"], "res_id", r, "time_id", t, "capacity_factor")
    res_avail = _make_2d(f["res_profiles"], "res_id", r, "time_id", t, "availability_pct", default=1.0, as_share=True)
    flex_av_up = _make_2d(f["flex_profiles"], "flex_id", flx, "time_id", t, "availability_pct_up", default=1.0, as_share=True)
    flex_av_dn = _make_2d(f["flex_profiles"], "flex_id", flx, "time_id", t, "availability_pct_down", default=1.0, as_share=True)
    inter_avail = _make_2d(f["border_profiles"], "border_id", b, "time_id", t, "availability_pct", default=1.0, as_share=True)
    fixed_imp = _make_2d(f["border_profiles"], "border_id", b, "time_id", t, "fixed_import_mw")
    fixed_exp = _make_2d(f["border_profiles"], "border_id", b, "time_id", t, "fixed_export_mw")
    imp_price = _make_2d(f["border_profiles"], "border_id", b, "time_id", t, "import_price_eur_mwh")
    exp_price = _make_2d(f["border_profiles"], "border_id", b, "time_id", t, "export_price_eur_mwh")

    reserve_df = f["reserves"].copy()
    reserve_df["time_id"] = reserve_df["time_id"].map(_label)
    reserve_up = {}
    reserve_dn = {}
    short_up = {}
    short_dn = {}

    # Pre-compute RES availability by hour for forecast-error proxy.
    res_av_by_t = {ti: 0.0 for ti in t}
    wind_av_by_t = {ti: 0.0 for ti in t}
    solar_av_by_t = {ti: 0.0 for ti in t}
    tech_lookup = dict(zip(res["res_id"], res["technology"].astype(str).str.lower()))
    for ri in r:
        for ti in t:
            av = res_cap.get(ri, 0.0) * res_cf.get((ri, ti), 0.0) * res_avail.get((ri, ti), 1.0)
            res_av_by_t[ti] += av
            if "wind" in tech_lookup.get(ri, ""):
                wind_av_by_t[ti] += av
            if "pv" in tech_lookup.get(ri, "") or "solar" in tech_lookup.get(ri, ""):
                solar_av_by_t[ti] += av

    for _, row in reserve_df.iterrows():
        ti = _label(row["time_id"])
        fcr = _num(row.get("fcr_mw", 0))
        reserve_up[ti] = fcr + _num(row.get("afrr_up_mw", 0)) + _num(row.get("mfrr_up_mw", 0))
        reserve_dn[ti] = fcr + _num(row.get("afrr_down_mw", 0)) + _num(row.get("mfrr_down_mw", 0))

    # ACER short-term flexibility need (percentile method).
    short_up, short_dn = _short_term_needs(
        reserve_df, demand, wind_av_by_t, solar_av_by_t,
        up_percentile=_num(control.get("shortterm_up_percentile", SHORTTERM_UP_PERCENTILE)),
        dn_percentile=_num(control.get("shortterm_dn_percentile", SHORTTERM_DN_PERCENTILE)),
    )

    # Network needs (Article 14 fine-tuning). Parsed only when use_network = 1.
    use_network = _num(control.get("use_network", 0))
    nw_ids, nw_down_cap, nw_up_req_t, nw_active_triples = _network_needs(
        f.get("network"), target_year, t, r, res, hours, active=use_network > 0.5,
    )

    scalars = {
        "useUC": _num(control.get("useUC", 1)),
        "useNetwork": use_network,
        "socCyclic": _num(control.get("use_storage_cyclic_SOC", control.get("use_storage_SOC", 0))),
        "voll": _num(control.get("VOLL_EUR_per_MWh", 10000)),
        "co2_price": _num(control.get("CO2_price_EUR_t", 70)),
        "curt_penalty": _num(control.get("curtailment_penalty_EUR_per_MWh", 5)),
        "reserve_slack_penalty": _num(control.get("reserve_slack_penalty_EUR_per_MW", 5000)),
        "network_slack_penalty": _num(control.get("network_slack_penalty_EUR_per_MW", 8000)),
    }

    # Write config.inc and data.inc.
    with open(inc_dir / "config.inc", "w", encoding="ascii") as fh:
        fh.write(f"$set DATA_INC {inc_dir / 'data.inc'}\n")

    with open(inc_dir / "data.inc", "w", encoding="ascii") as fh:
        _write_set(fh, "t", "representative chronological hours", t)       
        _write_alias(fh, "(t, tt)")
        _write_set(fh, "gd", "dispatchable generator groups", gd)
        _write_set(fh, "r", "RES portfolios", r)
        _write_set(fh, "f", "flexibility and storage portfolios", flx)
        _write_set(fh, "b", "interconnector borders", b)
        _write_set(fh, "firstT(t)", "first hour of each representative day", first_t)
        _write_set(fh, "lastT(t)", "last hour of each representative day", last_t)
        _write_pair_set(fh, "next(t,tt)", "chronological transitions", next_pairs)
        _write_pair_set(fh, "sameDay(t,tt)", "first->last hour pairs per rep day", same_day_pairs)
        _write_set(fh, "nw", "network need entries", nw_ids if nw_ids else ["NW_NONE"])
        _write_triple_set(fh, "nwActive(nw,t,r)", "active network need links", nw_active_triples)

        _write_scalars(fh, scalars)
        _write_param(fh, "weight", "represented hours weight", weight)
        _write_param(fh, "demand", "gross demand [MW]", demand)
        _write_param(fh, "capBlock", "capacity per block [MW]", cap_block)
        _write_param(fh, "nBlocks", "number of blocks", n_blocks)
        _write_param(fh, "pminPct", "minimum stable generation share", pmin)
        _write_param(fh, "varCost", "variable cost excl CO2 [EUR/MWh]", var_cost)
        _write_param(fh, "emis", "emission intensity [tCO2/MWh]", emis)
        _write_param(fh, "startupCost", "startup cost per block [EUR]", startup)
        _write_param(fh, "rampUp", "hourly ramp-up per block [MW/h]", ramp_up)
        _write_param(fh, "rampDn", "hourly ramp-down per block [MW/h]", ramp_dn)
        _write_param(fh, "resCap", "RES installed capacity [MW]", res_cap)
        _write_param(fh, "curtShare", "curtailable RES share", curt_share)
        _write_param(fh, "flexUpCap", "upward flexibility capacity [MW]", flex_up)
        _write_param(fh, "flexDnCap", "downward flexibility capacity [MW]", flex_dn)
        _write_param(fh, "flexEnergy", "flex/storage energy capacity [MWh]", flex_energy)
        _write_param(fh, "flexEff", "flexibility efficiency", flex_eff)
        _write_param(fh, "flexCost", "activation cost [EUR/MWh]", flex_cost)
        _write_param(fh, "impCap", "import capacity [MW]", imp_cap)
        _write_param(fh, "expCap", "export capacity [MW]", exp_cap)
        _write_param(fh, "reserveUpReq", "upward reserve requirement [MW]", reserve_up)
        _write_param(fh, "reserveDnReq", "downward reserve requirement [MW]", reserve_dn)
        _write_param(fh, "shortUpNeed", "ACER short-term upward need [MW]", short_up)
        _write_param(fh, "shortDnNeed", "ACER short-term downward need [MW]", short_dn)
        _write_param(fh, "nwDownCap", "network downward hosting cap [MW]", nw_down_cap or {"NW_NONE": 0.0})
        _write_param(fh, "nwUpReqT", "network upward need by hour [MW]", nw_up_req_t or {ti: 0.0 for ti in t})
        _write_2d_param(fh, "genAvail", "generator availability [0..1]", gen_avail)
        _write_2d_param(fh, "resCF", "RES capacity factor", res_cf)
        _write_2d_param(fh, "resAvail", "RES availability [0..1]", res_avail)
        _write_2d_param(fh, "flexAvailUp", "upward flex availability", flex_av_up)
        _write_2d_param(fh, "flexAvailDn", "downward flex availability", flex_av_dn)
        _write_2d_param(fh, "interAvail", "interconnector availability", inter_avail)
        _write_2d_param(fh, "fixedImp", "fixed imports [MW]", fixed_imp)
        _write_2d_param(fh, "fixedExp", "fixed exports [MW]", fixed_exp)
        _write_2d_param(fh, "impPrice", "import price [EUR/MWh]", imp_price)
        _write_2d_param(fh, "expPrice", "export price [EUR/MWh]", exp_price)

    log.info("Wrote v2 include files with %d hours, %d generator groups, %d RES portfolios, %d flex portfolios", len(t), len(gd), len(r), len(flx))


def parse_csv_results(out_dir: Path, *_args) -> dict[str, Any]:
    """Parse all v2 CSV outputs from GAMS."""
    paths = {
        "dispatch": out_dir / "dispatch.csv",
        "indicators": out_dir / "fna_indicators.csv",
        "price": out_dir / "price.csv",
        "residual": out_dir / "residual.csv",
        "storage": out_dir / "storage.csv",
        "reserve": out_dir / "reserve.csv",
    }
    for name, p in paths.items():
        if not p.exists():
            raise FileNotFoundError(f"Missing expected output {name}: {p}")
    out = {name: _read_csv_robust(p) for name, p in paths.items()}

    # network.csv is written by v3.1+ GAMS models; older outputs may not have it.
    network_path = out_dir / "network.csv"
    out["network"] = _read_csv_robust(network_path) if network_path.exists() else pd.DataFrame()
    return out


def write_results(
    wb: xw.Book,
    results: dict[str, Any],
    rep_hours: pd.DataFrame | None = None,
    rep_days: pd.DataFrame | None = None,
    network_needs: pd.DataFrame | None = None,
    dso_zones: pd.DataFrame | None = None,
    flex_availability: pd.DataFrame | None = None,
    flex: pd.DataFrame | None = None,
    prequalification: pd.DataFrame | None = None,
    control: dict[str, Any] | None = None,
    target_year: int | None = None,
    input_frames: dict[str, pd.DataFrame] | None = None,
    sheet_suffix: str = "",
    img_dir: Path | None = None,
) -> dict[str, pd.DataFrame]:
    """Write raw and summarized results back to Excel, plus ACER FNA indicators.

    ``sheet_suffix`` (e.g. "_2030") is appended to every output sheet name so
    a multi-year run can write each target year's results side by side in the
    same workbook (see multi_year.py). Returns the extra ACER tables (RES
    integration, ramping, short-term) so a cross-year comparison can reuse
    them without recomputation.
    """
    extra_tables: dict[str, pd.DataFrame] = {}

    # Friendly summary table.
    ind = results["indicators"].copy()
    summary = ind[["metric", "value", "unit", "description"]] if set(["metric", "value", "unit", "description"]).issubset(ind.columns) else ind
    _write_df(wb, f"30_Summary_Tables{sheet_suffix}", summary)
    _write_df(wb, f"31_Dispatch_Raw{sheet_suffix}", results["dispatch"])
    _write_df(wb, f"32_Price{sheet_suffix}", results["price"])
    _write_df(wb, f"33_Residual{sheet_suffix}", results["residual"])
    _write_df(wb, f"34_Storage{sheet_suffix}", results["storage"])
    _write_df(wb, f"35_Reserve{sheet_suffix}", results["reserve"])

    # ACER-native indicator tables (sheets 40-43, 41b, 42b, 46).
    if rep_hours is not None and rep_days is not None:
        try:
            from fna_indicators import build_fna_indicators

            control = control or {}
            tables = build_fna_indicators(
                results["residual"], rep_hours, rep_days,
                market_time_unit_minutes=_num(control.get("market_time_unit_minutes", 60)),
                flex_availability=flex_availability, flex=flex,
                storage=results.get("storage"), prequalification=prequalification,
                target_year=target_year,
            )
            sheet_map = {
                "res_integration": "40_FNA_RES_Integration",
                "ramping": "41_FNA_Ramping",
                "ramping_capacity": "41b_FNA_Ramping_Capacity",
                "short_term": "42_FNA_ShortTerm",
                "short_term_by_season": "42b_FNA_ShortTerm_BySeason",
                "residual_duration": "43_FNA_Residual_Duration",
                "unavailability": "46_FNA_Unavailability_Needs",
            }
            for key, sheet in sheet_map.items():
                table = tables.get(key)
                if isinstance(table, pd.DataFrame) and not table.empty:
                    _write_df(wb, f"{sheet}{sheet_suffix}", table)
        except Exception as exc:
            log.warning("Could not build ACER FNA indicator sheets: %s", exc)

        # DSO / TSO / Article-14 fine-tuning needs (sheets 44, 45, 47).
        try:
            from network_needs import compute_dso_needs, compute_fine_tuning_needs, compute_tso_needs

            network_csv = results.get("network")
            dso_table = compute_dso_needs(results["residual"], rep_hours, dso_zones)
            if isinstance(dso_table, pd.DataFrame) and not dso_table.empty:
                _write_df(wb, f"44_FNA_DSO_Needs{sheet_suffix}", dso_table)

            if isinstance(network_csv, pd.DataFrame) and not network_csv.empty:
                tso_table = compute_tso_needs(network_csv, network_needs, rep_hours)
                if isinstance(tso_table, pd.DataFrame) and not tso_table.empty:
                    _write_df(wb, f"45_FNA_TSO_Needs{sheet_suffix}", tso_table)

                ft_table = compute_fine_tuning_needs(network_csv, network_needs, rep_hours)
                if isinstance(ft_table, pd.DataFrame) and not ft_table.empty:
                    _write_df(wb, f"47_FNA_FineTuning_Art14{sheet_suffix}", ft_table)
        except Exception as exc:
            log.warning("Could not build DSO/TSO/Article-14 need sheets: %s", exc)

        # Consolidated RES-integration report (sheet 14, ACER Art. 8).
        try:
            from fna_res_integration import write_res_integration_to_excel

            res_portfolios = (input_frames or {}).get("res")
            if isinstance(res_portfolios, pd.DataFrame) and not res_portfolios.empty:
                extra_tables["res_integration"] = write_res_integration_to_excel(
                    wb, results["dispatch"], results["residual"], res_portfolios, rep_hours,
                    control=control, sheet_suffix=sheet_suffix,
                )
        except Exception as exc:
            log.warning("Could not build RES-integration report: %s", exc)

        # Consolidated ramping-needs report (sheet 15, ACER Art. 9).
        try:
            from fna_ramping import write_ramping_to_excel

            frames = input_frames or {}
            extra_tables["ramping"] = write_ramping_to_excel(
                wb, results["residual"], results["reserve"], results["dispatch"], rep_hours,
                rep_days=rep_days, borders=frames.get("borders"), border_profiles=frames.get("border_profiles"),
                control=control, sheet_suffix=sheet_suffix,
            )
        except Exception as exc:
            log.warning("Could not build ramping-needs report: %s", exc)

        # Short-term flexibility needs report (sheet 16, ACER Art. 10).
        try:
            from fna_shortterm import build_historical_error_series, build_short_term, compute_scaling_factors

            frames = input_frames or {}
            res_portfolios = frames.get("res")
            reserve_forecast_error = frames.get("reserves")
            raw_dir = PROJECT_ROOT / "data" / "raw_be2023"
            load_path = raw_dir / "load.csv"
            res_actual_path = raw_dir / "res_generation_actual.csv"
            res_forecast_path = raw_dir / "res_generation_forecast.csv"
            cap_path = raw_dir / "installed_capacity.csv"

            if (
                load_path.exists() and res_actual_path.exists() and res_forecast_path.exists()
                and isinstance(res_portfolios, pd.DataFrame) and not res_portfolios.empty
            ):
                load_hist = pd.read_csv(load_path, index_col=0)
                load_hist.index = pd.to_datetime(load_hist.index, utc=True)
                res_actual_hist = pd.read_csv(res_actual_path, index_col=0)
                res_actual_hist.index = pd.to_datetime(res_actual_hist.index, utc=True)
                res_forecast_hist = pd.read_csv(res_forecast_path, index_col=0)
                res_forecast_hist.index = pd.to_datetime(res_forecast_hist.index, utc=True)

                historical_res_capacity_mw = None
                if cap_path.exists():
                    ic = pd.read_csv(cap_path, index_col=0)
                    if not ic.empty:
                        row = ic.iloc[0]
                        historical_res_capacity_mw = {
                            "wind": _num(row.get("Wind Offshore", 0.0)) + _num(row.get("Wind Onshore", 0.0)),
                            "solar": _num(row.get("Solar", 0.0)),
                        }

                control = control or {}
                target_year = int(_num(control.get("target_year", 2025))) or 2025
                up_pct = _num(control.get("shortterm_up_percentile", 99.9)) or 99.9
                dn_pct = _num(control.get("shortterm_dn_percentile", 0.1))
                load_improve = _num(control.get("load_forecast_error_improvement_pct", 0.0))
                wind_improve = _num(control.get("wind_forecast_error_improvement_pct", 0.0))
                solar_improve = _num(control.get("solar_forecast_error_improvement_pct", 0.0))
                very_fast_share = _num(control.get("shortterm_very_fast_share_pct", 10.0)) or 10.0
                fast_share = _num(control.get("shortterm_fast_share_pct", 30.0)) or 30.0
                interco_outage = _num(control.get("interconnector_outage_stress_mw", 0.0))

                load_scaling, wind_scaling, solar_scaling, scaling_notes = compute_scaling_factors(
                    rep_hours, res_portfolios, load_hist, historical_res_capacity_mw, target_year,
                )
                scaling_notes["load_forecast_error_improvement_pct"] = load_improve
                scaling_notes["wind_forecast_error_improvement_pct"] = wind_improve
                scaling_notes["solar_forecast_error_improvement_pct"] = solar_improve

                hist = build_historical_error_series(
                    load_hist, res_actual_hist, res_forecast_hist,
                    load_scaling_factor=load_scaling, wind_scaling_factor=wind_scaling, solar_scaling_factor=solar_scaling,
                    load_improvement_pct=load_improve, wind_improvement_pct=wind_improve, solar_improvement_pct=solar_improve,
                )

                shortterm_table = build_short_term(
                    historical_errors=hist, residual=results["residual"], reserve=results["reserve"], rep_hours=rep_hours,
                    reserve_forecast_error=reserve_forecast_error, up_percentile=up_pct, dn_percentile=dn_pct,
                    speed_shares_pct={"very_fast": very_fast_share, "fast": fast_share},
                    interconnector_outage_stress_mw=interco_outage, scaling_notes=scaling_notes,
                )
                if not shortterm_table.empty:
                    _write_df(wb, f"16_FNA_ShortTerm{sheet_suffix}", shortterm_table)
                    extra_tables["shortterm"] = shortterm_table
        except Exception as exc:
            log.warning("Could not build short-term flexibility report: %s", exc)

    # Data-quality / granularity manifest (sheet 19) and barriers/digitalisation
    # summary (sheet 48). Both are pure transparency reports over the inputs.
    if input_frames is not None:
        try:
            from data_quality_report import build_granularity_report

            sheet_names = {**SHEETS, **OPTIONAL_SHEETS}
            dq_table = build_granularity_report(input_frames, sheet_names)
            if not dq_table.empty:
                _write_df(wb, f"19_DataQuality_Report{sheet_suffix}", dq_table)
        except Exception as exc:
            log.warning("Could not build data-quality report: %s", exc)

        try:
            from barriers import summarise_barriers

            barriers_df = input_frames.get("barriers")
            if isinstance(barriers_df, pd.DataFrame) and not barriers_df.empty:
                barriers_table = summarise_barriers(barriers_df)
                if not barriers_table.empty:
                    _write_df(wb, f"48_FNA_Barriers_Summary{sheet_suffix}", barriers_table)
        except Exception as exc:
            log.warning("Could not build barriers/digitalisation summary: %s", exc)

    # Industry-standard charts for the FNA indicator tables (sheet 17).
    if img_dir is not None and extra_tables:
        try:
            from fna_charts import generate_fna_indicator_charts

            generate_fna_indicator_charts(extra_tables, img_dir, wb=wb, sheet_suffix=sheet_suffix)
        except Exception as exc:
            log.warning("Could not build FNA indicator charts: %s", exc)

    sort_xlwings_sheets(wb)
    return extra_tables


def write_mc_results_to_excel(
    wb: xw.Book,
    mc_results: dict[int, dict],
    requested_scenarios: int | None = None,
    sheet_suffix: str = "",
) -> None:
    """Write aggregated Monte Carlo result tables to Excel sheets 50-53."""

    if not mc_results:
        log.warning("No MC results to write")
        return

    all_dispatch = []
    all_prices = []
    all_reserves = []

    for scenario_id, result in mc_results.items():
        if "dispatch" in result:
            d = result["dispatch"].copy()
            d["scenario_id"] = scenario_id
            all_dispatch.append(d)
        if "price" in result:
            p = result["price"].copy()
            p["scenario_id"] = scenario_id
            all_prices.append(p)
        if "reserve" in result:
            r = result["reserve"].copy()
            r["scenario_id"] = scenario_id
            all_reserves.append(r)

    summary = pd.DataFrame(
        {
            "Metric": [
                "Successful scenarios",
                "Requested scenarios",
                "Mean total cost (EUR)",
                "Std dev total cost (EUR)",
                "Min total cost (EUR)",
                "Max total cost (EUR)",
                "Mean RES curtailment (MWh)",
                "Mean energy not served (MWh)",
            ],
            "Value": [
                len(mc_results),
                requested_scenarios if requested_scenarios is not None else len(mc_results),
                _safe_mean([_indicator_value(mc_results[i], "total_cost") for i in sorted(mc_results)]),
                _safe_std([_indicator_value(mc_results[i], "total_cost") for i in sorted(mc_results)]),
                _safe_min([_indicator_value(mc_results[i], "total_cost") for i in sorted(mc_results)]),
                _safe_max([_indicator_value(mc_results[i], "total_cost") for i in sorted(mc_results)]),
                _safe_mean([_indicator_value(mc_results[i], "res_curtailment") for i in sorted(mc_results)]),
                _safe_mean([_indicator_value(mc_results[i], "energy_not_served") for i in sorted(mc_results)]),
            ],
        }
    )
    _write_df(wb, f"50_MC_Summary{sheet_suffix}", summary)

    if all_dispatch:
        dispatch_combined = pd.concat(all_dispatch, ignore_index=True)
        dispatch_combined["dispatch_mw"] = pd.to_numeric(dispatch_combined["dispatch_mw"], errors="coerce")
        dispatch_range = (
            dispatch_combined.groupby(["period", "resource"])["dispatch_mw"]
            .agg(["min", "mean", "max", "std", "count"])
            .reset_index()
        )
        dispatch_range.columns = [
            "period",
            "resource",
            "dispatch_min_mw",
            "dispatch_mean_mw",
            "dispatch_max_mw",
            "dispatch_std_mw",
            "n_scenarios",
        ]
        _write_df(wb, f"51_MC_Dispatch_Range{sheet_suffix}", dispatch_range)

    if all_prices:
        price_combined = pd.concat(all_prices, ignore_index=True)
        price_combined["price_eur_mwh"] = pd.to_numeric(price_combined["price_eur_mwh"], errors="coerce")
        price_stats = (
            price_combined.groupby("period")["price_eur_mwh"]
            .agg(
                price_min="min",
                price_mean="mean",
                price_max="max",
                price_std="std",
                price_p5=lambda x: x.quantile(0.05),
                price_p25=lambda x: x.quantile(0.25),
                price_p75=lambda x: x.quantile(0.75),
                price_p95=lambda x: x.quantile(0.95),
            )
            .reset_index()
        )
        _write_df(wb, f"52_MC_Price_Distribution{sheet_suffix}", price_stats)

    if all_reserves:
        reserve_combined = pd.concat(all_reserves, ignore_index=True)
        for col in ["up_available_mw", "down_available_mw", "up_shortfall_mw", "down_shortfall_mw"]:
            reserve_combined[col] = pd.to_numeric(reserve_combined[col], errors="coerce")
        reserve_stats = (
            reserve_combined.groupby("period")
            .agg(
                up_avail_mean=("up_available_mw", "mean"),
                up_avail_std=("up_available_mw", "std"),
                down_avail_mean=("down_available_mw", "mean"),
                down_avail_std=("down_available_mw", "std"),
                up_short_mean=("up_shortfall_mw", "mean"),
                up_short_max=("up_shortfall_mw", "max"),
                down_short_mean=("down_shortfall_mw", "mean"),
                down_short_max=("down_shortfall_mw", "max"),
            )
            .reset_index()
        )
        _write_df(wb, f"53_MC_Reserve_Range{sheet_suffix}", reserve_stats)

    sort_xlwings_sheets(wb)
    log.info("Wrote MC results to Excel sheets 50-53")


def write_mc_charts_to_excel(
    wb: xw.Book,
    chart_paths: dict[str, Path],
    successful_scenarios: int,
    requested_scenarios: int,
    sheet_suffix: str = "",
) -> None:
    """Place generated MC chart PNGs on a workbook dashboard sheet."""

    sheet_name = f"54_MC_Charts{sheet_suffix}"
    if sheet_name not in [s.name for s in wb.sheets]:
        wb.sheets.add(name=sheet_name, after=wb.sheets[-1])
    sht = wb.sheets[sheet_name]
    _delete_pictures(sht)
    sht.clear()

    sht["A1"].value = "Monte Carlo Results Dashboard"
    sht["A2"].value = f"Successful scenarios: {successful_scenarios} / {requested_scenarios}"
    try:
        sht["A1"].font.bold = True
        sht["A1"].font.size = 16
        sht["A2"].font.italic = True
    except Exception:
        pass

    existing = [(name, Path(path)) for name, path in chart_paths.items() if Path(path).exists()]
    row_stride = 38
    col_positions = ["A", "M"]
    for index, (name, img) in enumerate(existing):
        row = 4 + (index // len(col_positions)) * row_stride
        column = col_positions[index % len(col_positions)]
        anchor = sht.range(f"{column}{row}")
        sht.range((anchor.row - 1, anchor.column)).value = name.replace("_", " ").title()
        try:
            sht.pictures.add(
                str(img),
                name=f"mc_chart_{index + 1}_{name}",
                update=True,
                left=anchor.left,
                top=anchor.top,
                width=640,
            )
        except Exception as exc:
            log.warning("Could not insert %s into Excel: %s", img, exc)

    try:
        sht.autofit()
    except Exception:
        pass



# ─────────────────────────────────────────────────────────────────────────────
# Writers
# ─────────────────────────────────────────────────────────────────────────────

def _write_set(fh, name: str, desc: str, labels: list[str]) -> None:
    fh.write(f"Set {name} '{desc}' /\n")
    for x in labels:
        fh.write(f"    {x}\n")
    fh.write("/;\n\n")

def _write_alias(fh, names: str) -> None:
     fh.write(f"Alias {names} ;\n\n")

def _write_pair_set(fh, name: str, desc: str, pairs: list[tuple[str, str]]) -> None:
    fh.write(f"Set {name} '{desc}' /\n")
    for a, b in pairs:
        if a and b:
            fh.write(f"    {a}.{b}\n")
    fh.write("/;\n\n")


def _write_triple_set(fh, name: str, desc: str, triples: list[tuple[str, str, str]]) -> None:
    """Write a 3-index set such as nwActive(nw,t,r)."""
    valid = [(a, b, c) for a, b, c in triples if a and b and c]
    if not valid:
        fh.write("$onEmpty\n")
        fh.write(f"Set {name} '{desc}' /\n")
        fh.write("/;\n")
        fh.write("$offEmpty\n\n")
        return

    fh.write(f"Set {name} '{desc}' /\n")
    for a, b, c in valid:
        fh.write(f"    {a}.{b}.{c}\n")
    fh.write("/;\n\n")


def _write_scalars(fh, vals: dict[str, float]) -> None:
    fh.write("Scalars\n")
    for k, v in vals.items():
        fh.write(f"    {k} / {_g(v)} /\n")
    fh.write(";\n\n")


def _write_param(fh, name: str, desc: str, values: dict[str, float]) -> None:
    fh.write(f"Parameter {name}(*) '{desc}' /\n")
    for k, v in values.items():
        fh.write(f"    {k}  {_g(v)}\n")
    fh.write("/;\n\n")


def _write_2d_param(fh, name: str, desc: str, values: dict[tuple[str, str], float]) -> None:
    fh.write(f"Parameter {name}(*,*) '{desc}' /\n")
    for (a, b), v in values.items():
        if abs(float(v)) > 1e-12:
            fh.write(f"    {a}.{b}  {_g(v)}\n")
    fh.write("/;\n\n")


# ─────────────────────────────────────────────────────────────────────────────
# Excel helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_csv_robust(path: Path) -> pd.DataFrame:
    path = Path(path)

    # First try normal comma CSV. This respects quoted text with commas.
    try:
        df = pd.read_csv(
            path,
            sep=",",
            engine="c",
            dtype=str,
            keep_default_na=False,
            quotechar='"',
            skipinitialspace=True,
        )
    except Exception:
        # Fallback for old GAMS files that are space-separated.
        df = pd.read_csv(
            path,
            sep=r"\s+",
            engine="python",
            dtype=str,
            keep_default_na=False,
        )

    df.columns = [str(c).strip().strip('"') for c in df.columns]

    for c in df.columns:
        df[c] = df[c].astype(str).str.strip().str.strip('"')

    return df


def _write_df(wb: xw.Book, sheet_name: str, df: pd.DataFrame) -> None:
    if sheet_name not in [s.name for s in wb.sheets]:
        wb.sheets.add(name=sheet_name, after=wb.sheets[-1])
    sht = wb.sheets[sheet_name]
    sht.clear()
    _write_dataframe_chunked(sht, df)
    try:
        sht.range("A1").expand("right").color = (31, 78, 121)
        sht.range("A1").expand("right").font.color = (255, 255, 255)
        sht.range("A1").expand("right").font.bold = True
        sht.autofit()
    except Exception:
        pass


def _write_dataframe_chunked(sht: xw.Sheet, df: pd.DataFrame) -> None:
    """Write a DataFrame in row chunks to avoid macOS Apple Event size limits."""

    columns = [str(col) for col in df.columns]
    sht.range("A1").value = columns
    if df.empty:
        return

    for start in range(0, len(df), EXCEL_WRITE_CHUNK_ROWS):
        chunk = df.iloc[start : start + EXCEL_WRITE_CHUNK_ROWS]
        values = _excel_safe_values(chunk)
        sht.range((start + 2, 1)).value = values


def _excel_safe_values(df: pd.DataFrame) -> list[list[Any]]:
    safe = df.astype(object).where(pd.notna(df), None)
    return safe.values.tolist()


def ensure_workbook_is_not_open(excel_path: Path) -> None:
    """Stop before writing if Excel has a lock file beside the workbook."""

    lock_file = excel_path.with_name(f"~${excel_path.name}")
    if lock_file.exists():
        raise RuntimeError(f"Excel lock file found: {lock_file}. Close the workbook in Excel and retry.")


def sort_openpyxl_sheets(workbook: Any) -> None:
    """Sort sheets by numeric prefix, then name."""

    workbook._sheets.sort(key=lambda sheet: sheet_sort_key(sheet.title))


def sort_xlwings_sheets(wb: Any) -> None:
    """Best-effort sort for an open xlwings workbook."""

    names = sorted([sheet.name for sheet in wb.sheets], key=sheet_sort_key)
    for index, name in enumerate(names):
        try:
            sheet = wb.sheets[name]
            if index == 0:
                sheet.api.Move(Before=wb.sheets[0].api)
            else:
                sheet.api.Move(After=wb.sheets[index - 1].api)
        except Exception:
            return


def sheet_sort_key(name: str) -> tuple[int, str]:
    import re

    match = re.match(r"^(\d+)_", str(name))
    if match:
        return int(match.group(1)), str(name).lower()
    return 999, str(name).lower()

def _clean_numeric(values: list[Any]) -> list[float]:
    return [float(v) for v in values if v is not None and not pd.isna(v) and isinstance(v, (int, float, np.number))]


def _safe_mean(values: list[Any]) -> float:
    clean = _clean_numeric(values)
    return float(np.mean(clean)) if clean else 0.0


def _safe_std(values: list[Any]) -> float:
    clean = _clean_numeric(values)
    return float(np.std(clean)) if len(clean) > 1 else 0.0


def _safe_min(values: list[Any]) -> float:
    clean = _clean_numeric(values)
    return float(np.min(clean)) if clean else 0.0


def _safe_max(values: list[Any]) -> float:
    clean = _clean_numeric(values)
    return float(np.max(clean)) if clean else 0.0


def _indicator_value(result: dict[str, Any], metric: str, default: Any = None) -> Any:
    indicators = result.get("indicators", {})
    if isinstance(indicators, dict):
        return indicators.get(metric, default)
    if isinstance(indicators, pd.DataFrame) and {"metric", "value"}.issubset(indicators.columns):
        rows = indicators[indicators["metric"].astype(str).str.strip().eq(metric)]
        if not rows.empty:
            return pd.to_numeric(rows.iloc[0]["value"], errors="coerce")
    return default


def _delete_pictures(sheet: xw.Sheet) -> None:
    try:
        for picture in list(sheet.pictures):
            picture.delete()
    except Exception:
        pass


def _clean_col(c: Any) -> str:
    return str(c).strip().replace(" ", "_").replace("-", "_").lower()


def _label(x: Any) -> str:
    s = str(x).strip()
    if s in ["", "nan", "None"]:
        return "NA"
    s = s.replace(" ", "_").replace("-", "_").replace("/", "_").replace(".", "_")
    return s


def _label_or_blank(x: Any) -> str:
    if x is None or str(x).strip().lower() in ["", "nan", "none"]:
        return ""
    return _label(x)


def _num(x: Any) -> float:
    try:
        if pd.isna(x):
            return 0.0
        return float(x)
    except Exception:
        return 0.0

def _share(x: Any) -> float:
    """
    Convert Excel percentage/share inputs safely.

    Accepts both:
      0.98  = 98%
      98    = 98%

    Returns:
      0.98
    """
    v = _num(x)
    if v > 1.0:
        return v / 100.0
    return v

def _num_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def _series(df: pd.DataFrame, id_col: str, val_col: str) -> dict[str, float]:
    if val_col not in df.columns:
        raise KeyError(f"Column '{val_col}' missing. Available columns: {list(df.columns)}")
    return {_label(row[id_col]): _num(row[val_col]) for _, row in df.iterrows()}


def _make_2d(
    df: pd.DataFrame,
    row_id: str,
    rows: list[str],
    col_id: str,
    cols: list[str],
    val_col: str,
    default: float = 0.0,
    as_share: bool = False,
) -> dict[tuple[str, str], float]:

    out = {(ri, ci): default for ri in rows for ci in cols}

    if val_col not in df.columns:
        return out

    tmp = df.copy()
    tmp[row_id] = tmp[row_id].map(_label)
    tmp[col_id] = tmp[col_id].map(_label)

    for _, row in tmp.iterrows():
        ri = row[row_id]
        ci = row[col_id]

        if ri in rows and ci in cols:
            raw = row[val_col]
            out[(ri, ci)] = _share(raw) if as_share else _num(raw)

    return out


def _z_score(percentile: float) -> float:
    """One-sided standard-normal quantile for a percentile in (0,100).

    P99.9 -> ~3.090, P95 -> ~1.645. Uses the inverse error function so no
    SciPy dependency is needed. Symmetric percentiles give the same |z|.
    """
    p = min(max(float(percentile), 1e-6), 100.0 - 1e-6) / 100.0
    # Two-sided tail probability relative to the median.
    tail = abs(p - 0.5) * 2.0
    return float(math.sqrt(2.0) * _erfinv(tail))


def _erfinv(x: float) -> float:
    """Rational approximation of the inverse error function (Winitzki)."""
    x = min(max(x, -0.999999), 0.999999)
    a = 0.147
    ln = math.log(1.0 - x * x)
    term = 2.0 / (math.pi * a) + ln / 2.0
    return math.copysign(math.sqrt(math.sqrt(term * term - ln / a) - term), x)


def _short_term_needs(
    reserve_df: pd.DataFrame,
    demand: dict[str, float],
    wind_av_by_t: dict[str, float],
    solar_av_by_t: dict[str, float],
    up_percentile: float,
    dn_percentile: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """ACER-style short-term flexibility need from forecast-error percentiles.

    Each *_forecast_error_pct column is read as the standard deviation of that
    component's residual-load forecast error (as a share of the relevant volume).
    Independent components combine in quadrature into a residual-load error sigma,
    which is scaled by the percentile z-score (e.g. P99.9 -> z~3.09). The largest
    unit/interconnector outage stress is added to the upward need only.
    """
    z_up = _z_score(up_percentile)
    z_dn = _z_score(dn_percentile)
    short_up: dict[str, float] = {}
    short_dn: dict[str, float] = {}
    for _, row in reserve_df.iterrows():
        ti = _label(row["time_id"])
        load_sd = _share(row.get("load_forecast_error_pct", 0)) * demand.get(ti, 0.0)
        wind_sd = _share(row.get("wind_forecast_error_pct", 0)) * wind_av_by_t.get(ti, 0.0)
        solar_sd = _share(row.get("solar_forecast_error_pct", 0)) * solar_av_by_t.get(ti, 0.0)
        sigma = math.sqrt(load_sd ** 2 + wind_sd ** 2 + solar_sd ** 2)
        outage = _num(row.get("largest_unit_outage_stress_mw", 0))
        short_up[ti] = z_up * sigma + outage
        short_dn[ti] = z_dn * sigma
    return short_up, short_dn


def _network_needs(
    network_df: pd.DataFrame | None,
    target_year: int,
    t: list[str],
    r: list[str],
    res: pd.DataFrame,
    hours: pd.DataFrame,
    active: bool,
) -> tuple[list[str], dict[str, float], dict[str, float], list[tuple[str, str, str]]]:
    """Parse 13_NetworkNeeds into GAMS-ready network fine-tuning objects.

    Returns (ids, downward hosting caps by need, upward need by hour, and
    nwActive(nw,t,r) links). When `active` is False or the sheet is missing,
    everything comes back empty so the model's network layer stays dormant.
    """
    empty: tuple[list[str], dict[str, float], dict[str, float], list[tuple[str, str, str]]] = ([], {}, {}, [])
    if not active or network_df is None or network_df.empty:
        return empty

    df = network_df.copy()
    df.columns = [_clean_col(c) for c in df.columns]
    if "active_in_run" in df.columns:
        df = df[pd.to_numeric(df["active_in_run"], errors="coerce").fillna(0) > 0]
    elif "active_in_v2" in df.columns:
        df = df[pd.to_numeric(df["active_in_v2"], errors="coerce").fillna(0) > 0]
    if "target_year" in df.columns:
        df = df[pd.to_numeric(df["target_year"], errors="coerce").fillna(target_year).astype(int) == target_year]
    if df.empty:
        return empty

    # Map workbook region -> RES portfolios via region_proxy/grid_level keywords.
    region_lookup = dict(zip(res["res_id"].map(_label), res.get("region_proxy", pd.Series(dtype=str)).astype(str).str.lower()))
    season_lookup = dict(zip(hours["time_id"].map(_label), hours.get("season", pd.Series(dtype=str)).astype(str).str.lower())) if "season" in hours.columns else {}

    ids: list[str] = []
    down_cap: dict[str, float] = {}
    up_req_t: dict[str, float] = {ti: 0.0 for ti in t}
    active_triples: list[tuple[str, str, str]] = []

    for _, row in df.iterrows():
        nw_id = _label(row.get("network_need_id"))
        direction = str(row.get("direction", "")).strip().lower()
        region = str(row.get("region", "")).strip().lower()
        value = _num(row.get("need_value", 0))
        block = str(row.get("time_block_or_rep_day", "")).strip().lower()
        ids.append(nw_id)

        affected_t = _network_hours(t, block, season_lookup)
        affected_r = [ri for ri in r if region in ("belgium", "", "all") or region in region_lookup.get(ri, "")]

        if direction == "downward":
            down_cap[nw_id] = value
            for ti in affected_t:
                for ri in affected_r:
                    active_triples.append((nw_id, ti, ri))
        elif direction == "upward":
            for ti in affected_t:
                up_req_t[ti] = up_req_t.get(ti, 0.0) + value

    return ids, down_cap, up_req_t, active_triples


def _network_hours(t: list[str], block: str, season_lookup: dict[str, str]) -> list[str]:
    """Resolve a network time-block string to representative-hour ids.

    Supports explicit rep-day prefixes (e.g. 'rd06/rd10 midday'), season names,
    and the keywords midday/noon/evening/peak. Falls back to all hours.
    """
    hits: list[str] = []
    rep_days = [token.upper() for token in block.replace("/", " ").split() if token.upper().startswith("RD")]
    midday = any(k in block for k in ("midday", "noon"))
    evening = any(k in block for k in ("evening", "peak"))
    seasons = [s for s in ("winter", "spring", "summer", "autumn") if s in block]

    for ti in t:
        ok = True
        if rep_days and not any(ti.upper().startswith(rd) for rd in rep_days):
            ok = False
        if seasons and season_lookup.get(ti, "") not in seasons:
            ok = False
        if midday and not any(ti.endswith(f"_H{h:02d}") for h in (11, 12, 13, 14)):
            ok = False
        if evening and not any(ti.endswith(f"_H{h:02d}") for h in (17, 18, 19, 20)):
            ok = False
        if ok:
            hits.append(ti)
    return hits or list(t)


def _g(x: Any) -> str:
    return f"{_num(x):.6f}"
