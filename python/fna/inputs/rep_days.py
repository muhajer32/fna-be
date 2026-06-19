"""
compress.py - build a representative-day workbook from a full-year workbook.

``build_rep_days_workbook(country, year, n_days)`` reads
``data/inputs/excel/<CC>_FullYear<year>.xlsx`` (full-year hourly), clusters its calendar days on
their demand + wind + solar shapes into ``n_days`` representative days (or an
*optimal* number chosen by the elbow method when ``n_days`` is None), then writes
``data/inputs/excel/<CC>_RepDays<year>.xlsx``:

* time-keyed sheets (02_RepHours, 05_IntercoProfiles, 08_RES_CF_Profiles,
  10_FlexAvailability, 11_Availability_Outages, 12_Reserve_ForecastError) are
  subset to the selected days and relabelled D### -> RD##;
* 03_RepDays is rebuilt with each representative day's weight (cluster size) so
  weighted annual energy still reflects a full year;
* structural sheets (fleet, flex, interconnectors, control, ...) are copied
  unchanged, then assumption rows/sheets are colour-coded (see styling.py).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fna.config import EXCEL_DIR, PROJECT_ROOT
from fna.inputs.styling import highlight_assumptions, print_attention_notice

# Sheets that carry an hourly time_id and must be subset to the selected days.
TIME_SHEETS = (
    "02_RepHours", "05_IntercoProfiles", "08_RES_CF_Profiles",
    "10_FlexAvailability", "11_Availability_Outages", "12_Reserve_ForecastError",
)
_TID = re.compile(r"^D(\d+)_H(\d+)$")


def _day_of(time_id: Any) -> str | None:
    m = _TID.match(str(time_id))
    return f"D{int(m.group(1)):03d}" if m else None


def _hour_of(time_id: Any) -> int | None:
    m = _TID.match(str(time_id))
    return int(m.group(2)) if m else None


# ---------------------------------------------------------------------------
# Feature matrix (one row per calendar day)
# ---------------------------------------------------------------------------

def _demand_col(rep_hours: pd.DataFrame) -> str:
    cols = [c for c in rep_hours.columns if str(c).startswith("gross_demand_MW")]
    if not cols:
        raise ValueError("02_RepHours has no gross_demand_MW_* column.")
    return cols[0]


def _daily_features(sheets: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, list[str]]:
    """Return (features indexed by day_id, ordered day list).

    Features per day = 24 h demand + 24 h capacity-weighted wind CF + 24 h
    solar CF, so clustering captures load level/shape and RES availability."""

    hours = sheets["02_RepHours"].copy()
    hours["day"] = hours["time_id"].map(_day_of)
    hours["h"] = hours["time_id"].map(_hour_of)
    demand_col = _demand_col(hours)
    demand = hours.pivot_table(index="day", columns="h", values=demand_col, aggfunc="mean")

    # Classify RES portfolios into wind / solar from 07_RES_Portfolios.
    res = sheets["07_RES_Portfolios"]
    cap_col = next((c for c in res.columns if str(c).startswith("capacity_MW")), None)
    tech = res.set_index("res_id")["technology"].astype(str).str.lower()
    cap = pd.to_numeric(res.set_index("res_id")[cap_col], errors="coerce").fillna(0.0) if cap_col else None

    cf = sheets["08_RES_CF_Profiles"].copy()
    cf["day"] = cf["time_id"].map(_day_of)
    cf["h"] = cf["time_id"].map(_hour_of)
    cf["tech"] = cf["res_id"].map(tech)
    cf["w"] = cf["res_id"].map(cap) if cap is not None else 1.0
    cf["capacity_factor"] = pd.to_numeric(cf["capacity_factor"], errors="coerce").fillna(0.0)

    def _cf_block(mask) -> pd.DataFrame:
        sub = cf[mask].copy()
        if sub.empty:
            return pd.DataFrame(0.0, index=demand.index, columns=demand.columns)
        sub["wcf"] = sub["capacity_factor"] * sub["w"]
        num = sub.pivot_table(index="day", columns="h", values="wcf", aggfunc="sum")
        den = sub.pivot_table(index="day", columns="h", values="w", aggfunc="sum").replace(0.0, np.nan)
        return (num / den).reindex(index=demand.index, columns=demand.columns).fillna(0.0)

    wind = _cf_block(cf["tech"].str.contains("wind", na=False))
    solar = _cf_block(cf["tech"].str.contains("solar|pv", na=False))

    feats = pd.concat(
        [demand.add_prefix("d"), wind.add_prefix("w"), solar.add_prefix("s")], axis=1
    ).fillna(0.0)
    feats = feats.sort_index()
    return feats, list(feats.index)


# ---------------------------------------------------------------------------
# Clustering + optimal-k (elbow)
# ---------------------------------------------------------------------------

def _scale(features: pd.DataFrame) -> np.ndarray:
    from sklearn.preprocessing import StandardScaler

    return StandardScaler().fit_transform(features.to_numpy(dtype=float))


def _elbow_k(x: np.ndarray, seed: int, k_min: int = 4, k_max: int = 30) -> tuple[int, list[tuple[int, float]]]:
    """Pick k by the knee of the inertia curve (max distance to the chord)."""
    from sklearn.cluster import KMeans

    k_max = min(k_max, x.shape[0] - 1)
    ks = list(range(k_min, k_max + 1))
    inertias = [KMeans(n_clusters=k, random_state=seed, n_init=10).fit(x).inertia_ for k in ks]

    # Kneedle: distance of each point to the line (first -> last).
    x0, y0 = ks[0], inertias[0]
    x1, y1 = ks[-1], inertias[-1]
    denom = np.hypot(x1 - x0, y1 - y0) or 1.0
    dist = [abs((y1 - y0) * k - (x1 - x0) * i + x1 * y0 - y1 * x0) / denom for k, i in zip(ks, inertias)]
    best_k = ks[int(np.argmax(dist))]
    return best_k, list(zip(ks, inertias))


def _cluster(features: pd.DataFrame, n_days: int | None, seed: int):
    from sklearn.cluster import KMeans

    x = _scale(features)
    if n_days is None:
        n_days, curve = _elbow_k(x, seed)
        print(f"  optimal representative days (elbow): {n_days}")
    else:
        curve = None
        n_days = max(1, min(int(n_days), x.shape[0]))

    km = KMeans(n_clusters=n_days, random_state=seed, n_init=10).fit(x)
    labels = km.labels_
    days = list(features.index)

    # Medoid day per cluster (closest scaled point to the centroid).
    selected: list[tuple[str, float, int]] = []  # (day_id, weight_days, label)
    total = len(days)
    for c in range(n_days):
        idx = np.where(labels == c)[0]
        if len(idx) == 0:
            continue
        d = np.linalg.norm(x[idx] - km.cluster_centers_[c], axis=1)
        medoid = days[idx[int(np.argmin(d))]]
        selected.append((medoid, float(len(idx)), c))

    # Order representative days chronologically for readable RD01..RDN labels.
    selected.sort(key=lambda t: t[0])
    return selected, curve


# ---------------------------------------------------------------------------
# Sheet transforms
# ---------------------------------------------------------------------------

def _relabel(sheets: dict[str, pd.DataFrame], selected: list[tuple[str, float, int]]) -> dict[str, pd.DataFrame]:
    day_to_rd = {day: f"RD{i:02d}" for i, (day, _w, _c) in enumerate(selected, start=1)}
    weight = {f"RD{i:02d}": w for i, (_day, w, _c) in enumerate(selected, start=1)}
    keep_days = set(day_to_rd)
    out: dict[str, pd.DataFrame] = {}

    def remap_time(tid: Any) -> Any:
        m = _TID.match(str(tid))
        if not m:
            return tid
        day = f"D{int(m.group(1)):03d}"
        return f"{day_to_rd[day]}_H{int(m.group(2)):02d}" if day in day_to_rd else tid

    for name, df in sheets.items():
        if name in TIME_SHEETS and "time_id" in df.columns:
            d = df.copy()
            d["__day"] = d["time_id"].map(_day_of)
            d = d[d["__day"].isin(keep_days)].copy()
            rd = d["__day"].map(day_to_rd)
            d["time_id"] = d["time_id"].map(remap_time)
            if "rep_day_id" in d.columns:
                d["rep_day_id"] = rd
            if "next_time_id" in d.columns:
                d["next_time_id"] = d["next_time_id"].map(remap_time)
            if "chronology_group" in d.columns:
                d["chronology_group"] = rd
            for wcol in ("weight_days", "weight_hours"):
                if wcol in d.columns:
                    d[wcol] = rd.map(weight)
            out[name] = d.drop(columns="__day")
        else:
            out[name] = df

    out["03_RepDays"] = _build_rep_days(sheets.get("03_RepDays"), selected, day_to_rd, weight)
    return out


def _build_rep_days(src: pd.DataFrame | None, selected, day_to_rd, weight) -> pd.DataFrame:
    src_by_day = src.set_index("rep_day_id") if (src is not None and "rep_day_id" in src.columns) else None
    total = sum(w for _d, w, _c in selected)
    rows = []
    for i, (day, w, _c) in enumerate(selected, start=1):
        rd = f"RD{i:02d}"
        base = src_by_day.loc[day].to_dict() if (src_by_day is not None and day in src_by_day.index) else {}
        rows.append({
            "rep_day_id": rd,
            "description": base.get("description", f"Representative day from {day}"),
            "season": base.get("season", ""),
            "day_type": base.get("day_type", ""),
            "weight_days": w,
            "selection_reason": f"K-means medoid of {int(w)} clustered days (source {day})",
            "probability_pct": w / total * 100.0,
            "source_id": base.get("source_id", "CLUSTER"),
            "data_quality": "clustered (representative day)",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_rep_days_workbook(
    country: str,
    year: int,
    n_days: int | None = None,
    seed: int = 42,
    full_year_path: Path | None = None,
    out_path: Path | None = None,
) -> Path:
    cc = str(country).strip().upper()
    src = Path(full_year_path) if full_year_path else EXCEL_DIR / f"{cc}_FullYear{year}.xlsx"
    dst = Path(out_path) if out_path else EXCEL_DIR / f"{cc}_RepDays{year}.xlsx"
    if not src.exists():
        raise FileNotFoundError(
            f"Full-year workbook not found: {src}. Run `build-full-year --country {cc} --year {year}` first."
        )

    print(f"Reading full-year workbook {src.name} ...")
    sheets = pd.read_excel(src, sheet_name=None)

    print("Clustering 365 days on demand + wind + solar shapes ...")
    features, _days = _daily_features(sheets)
    selected, _curve = _cluster(features, n_days, seed)
    print(f"  selected {len(selected)} representative days (weights sum to {sum(w for _d,w,_c in selected):.0f} days)")

    out_sheets = _relabel(sheets, selected)

    # Mark the workbook's resolution in 01_Control if present.
    ctl = out_sheets.get("01_Control")
    if ctl is not None and {"parameter", "value"}.issubset(ctl.columns):
        mask = ctl["parameter"].astype(str) == "time_resolution"
        if mask.any():
            ctl.loc[mask, "value"] = f"representative_days_{len(selected)}_from_{year}"

    print(f"Writing {dst.name} ...")
    with pd.ExcelWriter(dst, engine="openpyxl") as writer:
        for name, df in out_sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)

    summary = highlight_assumptions(dst)
    print_attention_notice(summary)
    print(f"Done. Wrote {dst}")
    return dst
