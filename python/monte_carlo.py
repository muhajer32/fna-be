"""
monte_carlo.py - wind scenario generation for Belgium FNA-ED/UC v2.

PECD files contain capacity factors, not forecast errors. This module therefore
samples full weather years and maps their hourly capacity factors onto the
representative hours used by the GAMS model.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

TECH_FILE_TOKENS = {
    "onshore": ["LFOnshoreWind", "OnshoreWind", "Onshore"],
    "offshore": ["LFOffshoreWind", "OffshoreWind", "Offshore"],
    "solar": ["LFSolarPV", "SolarPV", "Solar"],
}


@dataclass(frozen=True)
class RepresentativeHourMap:
    """Mapping between GAMS representative hours and calendar month/day/hour."""

    time_id: str
    month: int
    day: int
    hour: int


class PECDReader:
    """Read PECD capacity-factor and demand files in common parquet layouts."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def read_wind_time_series(
        self,
        country_code: str,
        technology: str,
        target_year: int | None = None,
        weather_years: list[int] | None = None,
    ) -> pd.DataFrame:
        """
        Return columns: technology, weather_year, month, day, hour, cf.

        Supported inputs include PECD 2021.3 wide files such as
        ``PECD-2021.3-wide-LFOnshoreWind-2025.parquet`` and long files that
        already contain country/year/month/day/hour/cf-style columns.
        """
        return self._read_cf_time_series(country_code, technology, target_year, weather_years)

    def read_solar_time_series(
        self,
        country_code: str,
        target_year: int | None = None,
        weather_years: list[int] | None = None,
    ) -> pd.DataFrame:
        """Return PECD solar PV capacity factors."""

        return self._read_cf_time_series(country_code, "solar", target_year, weather_years)

    def read_demand_time_series(
        self,
        country_code: str,
        target_year: int,
        weather_years: list[int] | None = None,
    ) -> pd.DataFrame:
        """Return columns: weather_year, month, day, hour, demand_mw."""

        files = self._find_demand_files(target_year)
        frames = []
        for path in files:
            raw = pd.read_parquet(path)
            parsed = _parse_demand_frame(raw, country_code)
            if not parsed.empty:
                frames.append(parsed)

        if not frames:
            names = ", ".join(path.name for path in files) or "none"
            raise FileNotFoundError(
                f"No usable PECD demand data for {country_code} and target year {target_year}. "
                f"Matched files: {names}"
            )

        df = pd.concat(frames, ignore_index=True)
        if weather_years:
            df = df[df["weather_year"].isin(weather_years)].copy()
            if df.empty:
                raise ValueError(f"PECD demand data has none of the requested weather years: {weather_years}")

        df["demand_mw"] = pd.to_numeric(df["demand_mw"], errors="coerce")
        df = df.dropna(subset=["weather_year", "month", "day", "hour", "demand_mw"])
        df[["weather_year", "month", "day", "hour"]] = df[["weather_year", "month", "day", "hour"]].astype(int)
        df = df.groupby(["weather_year", "month", "day", "hour"], as_index=False)["demand_mw"].mean()
        log.info(
            "Loaded PECD demand data for %s: %d rows, %d weather years",
            country_code,
            len(df),
            df["weather_year"].nunique(),
        )
        return df[["weather_year", "month", "day", "hour", "demand_mw"]]

    def _read_cf_time_series(
        self,
        country_code: str,
        technology: str,
        target_year: int | None = None,
        weather_years: list[int] | None = None,
    ) -> pd.DataFrame:
        tech_key = _technology_key(technology)
        files = self._find_files(tech_key, target_year)
        frames = []

        for path in files:
            raw = pd.read_parquet(path)
            parsed = _parse_pecd_frame(raw, path, country_code, tech_key)
            if not parsed.empty:
                frames.append(parsed)

        if not frames:
            names = ", ".join(path.name for path in files) or "none"
            raise FileNotFoundError(
                f"No usable PECD {technology} capacity-factor data for {country_code}. "
                f"Matched files: {names}"
            )

        df = pd.concat(frames, ignore_index=True)
        if weather_years:
            df = df[df["weather_year"].isin(weather_years)].copy()
            if df.empty:
                raise ValueError(f"PECD {technology} data has none of the requested weather years: {weather_years}")

        df["cf"] = pd.to_numeric(df["cf"], errors="coerce").clip(0.0, 1.0)
        df = df.dropna(subset=["weather_year", "month", "day", "hour", "cf"])
        df[["weather_year", "month", "day", "hour"]] = df[["weather_year", "month", "day", "hour"]].astype(int)
        df["technology"] = tech_key
        df = df.groupby(["technology", "weather_year", "month", "day", "hour"], as_index=False)["cf"].mean()
        log.info(
            "Loaded PECD %s data for %s: %d rows, %d weather years",
            tech_key,
            country_code,
            len(df),
            df["weather_year"].nunique(),
        )
        return df[["technology", "weather_year", "month", "day", "hour", "cf"]]

    def _find_files(self, tech_key: str, target_year: int | None = None) -> list[Path]:
        tokens = TECH_FILE_TOKENS[tech_key]
        if target_year is not None:
            if tech_key == "solar":
                preferred = self.data_dir / f"PECD-2021.3-wide-LFSolarPV-{target_year}.parquet"
                if preferred.exists():
                    return [preferred]

            target_files: list[Path] = []
            for token in tokens:
                for pattern in [
                    f"PECD-2021.3-wide-{token}-{target_year}.parquet",
                    f"PECD-2021.3-country-{token}-{target_year}.parquet",
                    f"PECD-*{token}*{target_year}*.parquet",
                ]:
                    for path in self.data_dir.glob(pattern):
                        if path not in target_files:
                            target_files.append(path)
            if target_files:
                return sorted(target_files)

        patterns = []
        for token in tokens:
            patterns.extend(
                [
                    f"PECD-2021.3-wide-{token}-*.parquet",
                    f"PECD-*wide*{token}*.parquet",
                    f"PECD-*{token}*.parquet",
                    f"*{token}*.parquet",
                ]
            )

        files: list[Path] = []
        for pattern in patterns:
            for path in self.data_dir.glob(pattern):
                if path not in files:
                    files.append(path)
        return sorted(files)

    def _find_demand_files(self, target_year: int) -> list[Path]:
        preferred = self.data_dir / f"PECD-country-demand_national_estimates-{target_year}.parquet"
        if preferred.exists():
            return [preferred]

        patterns = [
            f"PECD-country-demand_national_estimates-{target_year}.parquet",
            f"*demand*{target_year}*.parquet",
            "*demand*.parquet",
        ]
        files: list[Path] = []
        for pattern in patterns:
            for path in self.data_dir.glob(pattern):
                if path not in files:
                    files.append(path)
        return sorted(files)


class MonteCarloScenarios:
    """Generate uncertainty scenarios from sampled PECD years or workbook profiles."""

    def __init__(self, n_scenarios: int = 100, seed: int | None = None):
        if n_scenarios < 1:
            raise ValueError("n_scenarios must be >= 1")
        self.n_scenarios = int(n_scenarios)
        self.seed = seed
        self.scenarios: dict[int, pd.DataFrame] = {}
        self.sampled_weather_years: dict[int, int | None] = {}

    def generate_from_pecd(
        self,
        pecd_cf: pd.DataFrame,
        representative_hours: list[RepresentativeHourMap],
        wind_portfolios: pd.DataFrame,
    ) -> dict[int, pd.DataFrame]:
        """
        Sample complete weather years and produce scenario CF per wind resource.

        Returned DataFrames have columns: time_id, res_id, cf_scenario_{id}.
        """
        _require_columns(pecd_cf, {"technology", "weather_year", "month", "day", "hour", "cf"}, "pecd_cf")
        _require_columns(wind_portfolios, {"res_id", "wind_type"}, "wind_portfolios")
        if not representative_hours:
            raise ValueError("No representative-hour calendar mapping was provided.")

        available_years = sorted(int(y) for y in pecd_cf["weather_year"].dropna().unique())
        if not available_years:
            raise ValueError("PECD data contains no weather years.")

        rng = np.random.default_rng(self.seed)
        replace = len(available_years) < self.n_scenarios
        sampled_years = rng.choice(available_years, size=self.n_scenarios, replace=replace)
        if replace:
            log.warning("Only %d PECD weather years available; sampling with replacement.", len(available_years))

        lookup = _pecd_lookup(pecd_cf)
        scenarios: dict[int, pd.DataFrame] = {}

        for scenario_id, weather_year in enumerate(sampled_years):
            rows = []
            for portfolio in wind_portfolios.itertuples(index=False):
                wind_type = str(portfolio.wind_type)
                for rep_hour in representative_hours:
                    cf = lookup.get((wind_type, int(weather_year), rep_hour.month, rep_hour.day, rep_hour.hour))
                    if cf is None:
                        cf = lookup.get((wind_type, int(weather_year), rep_hour.month, rep_hour.day, 24 if rep_hour.hour == 0 else rep_hour.hour))
                    if cf is None:
                        raise KeyError(
                            "Missing PECD CF for "
                            f"{wind_type}, weather_year={weather_year}, "
                            f"{rep_hour.month:02d}-{rep_hour.day:02d} hour {rep_hour.hour}"
                        )
                    rows.append(
                        {
                            "time_id": rep_hour.time_id,
                            "res_id": portfolio.res_id,
                            f"cf_scenario_{scenario_id}": float(np.clip(cf, 0.0, 1.0)),
                        }
                    )

            scenarios[scenario_id] = pd.DataFrame(rows)
            self.sampled_weather_years[scenario_id] = int(weather_year)
            log.debug("Scenario %s sampled PECD weather year %s", scenario_id, weather_year)

        self.scenarios = scenarios
        log.info("Generated %d wind scenarios from %d PECD weather years", self.n_scenarios, len(available_years))
        return scenarios

    def add_pecd_load_solar(
        self,
        inputs: dict[str, Any],
        representative_hours: list[RepresentativeHourMap],
        demand_pecd: pd.DataFrame,
        solar_pecd: pd.DataFrame,
        existing_scenarios: dict[int, pd.DataFrame] | None = None,
    ) -> dict[int, pd.DataFrame]:
        """
        Add PECD-derived load multipliers and solar CFs to scenario tables.

        Demand is applied as a multiplier relative to the mean PECD demand for
        the same month/day/hour, preserving the workbook demand level while
        using PECD weather-year variability.
        """

        frames = inputs["frames"]
        res = frames["res"].copy()
        _require_columns(res, {"res_id", "technology"}, "07_RES_Portfolios")
        if not representative_hours:
            raise ValueError("No representative-hour calendar mapping was provided.")

        solar_ids = set(
            res.loc[
                res["technology"].astype(str).str.lower().str.contains("solar|pv", regex=True, na=False),
                "res_id",
            ].astype(str)
        )
        demand_lookup = _demand_lookup(demand_pecd)
        demand_mean_lookup = _mean_demand_lookup(demand_pecd)
        solar_lookup = _pecd_lookup(solar_pecd) if solar_ids else {}
        sampled_years = self._scenario_weather_years(demand_pecd, solar_pecd)
        scenarios = {sid: df.copy() for sid, df in (existing_scenarios or {}).items()}

        for scenario_id, weather_year in sampled_years.items():
            rows = []
            base = scenarios.get(scenario_id)
            if isinstance(base, pd.DataFrame) and not base.empty:
                rows.extend(base.to_dict("records"))

            demand_col = f"demand_multiplier_scenario_{scenario_id}"
            cf_col = f"cf_scenario_{scenario_id}"
            for rep_hour in representative_hours:
                demand = _lookup_hour_value(
                    demand_lookup,
                    int(weather_year),
                    rep_hour.month,
                    rep_hour.day,
                    rep_hour.hour,
                )
                mean_demand = _lookup_mean_hour_value(
                    demand_mean_lookup,
                    rep_hour.month,
                    rep_hour.day,
                    rep_hour.hour,
                )
                multiplier = demand / mean_demand if mean_demand and mean_demand > 0 else 1.0
                rows.append(
                    {
                        "time_id": rep_hour.time_id,
                        "res_id": None,
                        demand_col: float(max(0.0, multiplier)),
                    }
                )

                for res_id in solar_ids:
                    cf = _lookup_pecd_cf(solar_lookup, "solar", int(weather_year), rep_hour)
                    rows.append(
                        {
                            "time_id": rep_hour.time_id,
                            "res_id": res_id,
                            cf_col: float(np.clip(cf, 0.0, 1.0)),
                        }
                    )

            scenarios[scenario_id] = pd.DataFrame(rows)
            self.sampled_weather_years[scenario_id] = int(weather_year)

        self.scenarios = scenarios
        log.info("Added PECD-derived load and solar to %d scenarios", len(scenarios))
        return scenarios

    def _scenario_weather_years(self, *frames: pd.DataFrame) -> dict[int, int]:
        existing = {
            scenario_id: year
            for scenario_id, year in self.sampled_weather_years.items()
            if year is not None
        }
        if len(existing) >= self.n_scenarios:
            return {scenario_id: int(existing[scenario_id]) for scenario_id in range(self.n_scenarios)}

        available_sets = []
        for frame in frames:
            if isinstance(frame, pd.DataFrame) and "weather_year" in frame.columns and not frame.empty:
                available_sets.append(set(int(y) for y in frame["weather_year"].dropna().unique()))
        if not available_sets:
            raise ValueError("No PECD weather years available for load/solar scenarios.")

        available_years = sorted(set.intersection(*available_sets))
        if not available_years:
            available_years = sorted(set.union(*available_sets))
            log.warning("PECD load/solar years do not fully overlap; sampling from union.")

        rng = np.random.default_rng(self.seed)
        sampled = rng.choice(available_years, size=self.n_scenarios, replace=len(available_years) < self.n_scenarios)
        return {scenario_id: int(existing.get(scenario_id, sampled[scenario_id])) for scenario_id in range(self.n_scenarios)}

    def get_scenario(self, scenario_id: int) -> pd.DataFrame:
        if scenario_id not in self.scenarios:
            raise KeyError(f"Scenario {scenario_id} not found")
        return self.scenarios[scenario_id].copy()


def _parse_pecd_frame(raw: pd.DataFrame, path: Path, country_code: str, tech_key: str) -> pd.DataFrame:
    df = raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    lower = {c.lower(): c for c in df.columns}

    if {"country", "year", "month", "day", "hour", "cf"}.issubset(lower):
        out = pd.DataFrame(
            {
                "country": df[lower["country"]],
                "weather_year": df[lower["year"]],
                "month": df[lower["month"]],
                "day": df[lower["day"]],
                "hour": df[lower["hour"]],
                "cf": df[lower["cf"]],
            }
        )
        return out[out["country"].astype(str).str.upper().eq(country_code.upper())].drop(columns=["country"])

    year_columns = [column for column in df.columns if re.fullmatch(r"(?:19|20)\d{2}", str(column))]
    if {"area", "month", "day", "hour"}.issubset(lower) and year_columns:
        country_rows = df[lower["area"]].astype(str).str.upper().str.startswith(country_code.upper())
        wide = df.loc[country_rows, [lower["month"], lower["day"], lower["hour"], *year_columns]].copy()
        if wide.empty:
            log.warning("Skipping %s: no area rows for %s", path.name, country_code)
            return pd.DataFrame()

        long = wide.melt(
            id_vars=[lower["month"], lower["day"], lower["hour"]],
            value_vars=year_columns,
            var_name="weather_year",
            value_name="cf",
        )
        long = long.rename(
            columns={
                lower["month"]: "month",
                lower["day"]: "day",
                lower["hour"]: "hour",
            }
        )
        # Wide PECD may have multiple zones for one country. Use the country
        # average unless the source has already been pre-aggregated.
        return (
            long.groupby(["weather_year", "month", "day", "hour"], as_index=False)["cf"]
            .mean()
        )

    cf_col = _country_column(df, country_code)
    if cf_col is None:
        log.warning("Skipping %s: no country column for %s", path.name, country_code)
        return pd.DataFrame()

    time_parts = _time_parts(df, lower, path)
    out = time_parts.copy()
    out["cf"] = df[cf_col]
    return out


def _country_column(df: pd.DataFrame, country_code: str) -> str | None:
    target = country_code.upper()
    for column in df.columns:
        normalized = re.sub(r"[^A-Za-z0-9]", "", str(column)).upper()
        if normalized == target or normalized.endswith(target):
            return column
    return None


def _time_parts(df: pd.DataFrame, lower: dict[str, str], path: Path) -> pd.DataFrame:
    if {"year", "month", "day", "hour"}.issubset(lower):
        return pd.DataFrame(
            {
                "weather_year": df[lower["year"]],
                "month": df[lower["month"]],
                "day": df[lower["day"]],
                "hour": df[lower["hour"]],
            }
        )

    datetime_col = next((lower[name] for name in ["datetime", "timestamp", "time", "date"] if name in lower), None)
    if datetime_col is not None:
        dt = pd.to_datetime(df[datetime_col], errors="coerce")
        return pd.DataFrame(
            {
                "weather_year": dt.dt.year,
                "month": dt.dt.month,
                "day": dt.dt.day,
                "hour": dt.dt.hour,
            }
        )

    if "hour" in lower:
        year_match = re.search(r"(19|20)\d{2}", path.name)
        if not year_match:
            raise KeyError(f"Cannot infer weather year from {path.name}")
        hour_index = pd.to_numeric(df[lower["hour"]], errors="coerce")
        return _parts_from_hour_index(hour_index, int(year_match.group(0)))

    year_match = re.search(r"(19|20)\d{2}", path.name)
    if year_match:
        hour_index = pd.Series(np.arange(len(df)), index=df.index)
        return _parts_from_hour_index(hour_index, int(year_match.group(0)))

    raise KeyError(f"Cannot infer time columns in {path.name}. Columns: {list(df.columns)}")


def _parse_demand_frame(raw: pd.DataFrame, country_code: str) -> pd.DataFrame:
    df = raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    lower = {c.lower(): c for c in df.columns}
    demand_col = next((lower[name] for name in ["dem_mw", "demand_mw", "demand", "load_mw", "load"] if name in lower), None)
    if demand_col is None:
        raise KeyError(f"Cannot infer demand column. Columns: {list(df.columns)}")

    required = {"country", "year", "month", "day", "hour"}
    if required.issubset(lower):
        out = pd.DataFrame(
            {
                "country": df[lower["country"]],
                "weather_year": df[lower["year"]],
                "month": df[lower["month"]],
                "day": df[lower["day"]],
                "hour": df[lower["hour"]],
                "demand_mw": df[demand_col],
            }
        )
        return out[out["country"].astype(str).str.upper().eq(country_code.upper())].drop(columns=["country"])

    raise KeyError(f"Cannot infer PECD demand columns. Columns: {list(df.columns)}")


def _parts_from_hour_index(hour_index: pd.Series, weather_year: int) -> pd.DataFrame:
    hour_index = pd.to_numeric(hour_index, errors="coerce")
    start_at_one = bool((hour_index.dropna().min() or 0) >= 1)
    zero_based = hour_index - 1 if start_at_one else hour_index
    dt = pd.Timestamp(weather_year, 1, 1) + pd.to_timedelta(zero_based, unit="h")
    return pd.DataFrame(
        {
            "weather_year": weather_year,
            "month": dt.dt.month,
            "day": dt.dt.day,
            "hour": dt.dt.hour,
        }
    )


def _technology_key(value: Any) -> str:
    text = str(value).lower()
    if "solar" in text or "pv" in text:
        return "solar"
    if "offshore" in text:
        return "offshore"
    if "onshore" in text:
        return "onshore"
    raise ValueError(f"Unknown wind technology: {value!r}")


def _pecd_lookup(pecd_cf: pd.DataFrame) -> dict[tuple[str, int, int, int, int], float]:
    lookup = {}
    for row in pecd_cf.itertuples(index=False):
        key = (str(row.technology), int(row.weather_year), int(row.month), int(row.day), int(row.hour))
        lookup[key] = float(row.cf)
    return lookup


def _demand_lookup(demand_pecd: pd.DataFrame) -> dict[tuple[int, int, int, int], float]:
    lookup = {}
    for row in demand_pecd.itertuples(index=False):
        key = (int(row.weather_year), int(row.month), int(row.day), int(row.hour))
        lookup[key] = float(row.demand_mw)
    return lookup


def _mean_demand_lookup(demand_pecd: pd.DataFrame) -> dict[tuple[int, int, int], float]:
    grouped = demand_pecd.groupby(["month", "day", "hour"], as_index=False)["demand_mw"].mean()
    lookup = {}
    for row in grouped.itertuples(index=False):
        lookup[(int(row.month), int(row.day), int(row.hour))] = float(row.demand_mw)
    return lookup


def _lookup_hour_value(
    lookup: dict[tuple[int, int, int, int], float],
    weather_year: int,
    month: int,
    day: int,
    hour: int,
) -> float:
    value = lookup.get((weather_year, month, day, hour))
    if value is None:
        value = lookup.get((weather_year, month, day, 24 if hour == 0 else hour))
    if value is None:
        raise KeyError(f"Missing PECD demand for weather_year={weather_year}, {month:02d}-{day:02d} hour {hour}")
    return float(value)


def _lookup_mean_hour_value(
    lookup: dict[tuple[int, int, int], float],
    month: int,
    day: int,
    hour: int,
) -> float:
    value = lookup.get((month, day, hour))
    if value is None:
        value = lookup.get((month, day, 24 if hour == 0 else hour))
    if value is None:
        raise KeyError(f"Missing mean PECD demand for {month:02d}-{day:02d} hour {hour}")
    return float(value)


def _lookup_pecd_cf(
    lookup: dict[tuple[str, int, int, int, int], float],
    technology: str,
    weather_year: int,
    rep_hour: RepresentativeHourMap,
) -> float:
    value = lookup.get((technology, weather_year, rep_hour.month, rep_hour.day, rep_hour.hour))
    if value is None:
        value = lookup.get((technology, weather_year, rep_hour.month, rep_hour.day, 24 if rep_hour.hour == 0 else rep_hour.hour))
    if value is None:
        raise KeyError(
            "Missing PECD CF for "
            f"{technology}, weather_year={weather_year}, "
            f"{rep_hour.month:02d}-{rep_hour.day:02d} hour {rep_hour.hour}"
        )
    return float(value)


def _require_columns(df: pd.DataFrame, required: set[str], name: str) -> None:
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"{name} missing required columns: {sorted(missing)}")
