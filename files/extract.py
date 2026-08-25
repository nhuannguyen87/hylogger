#!/usr/bin/env python
"""Step 3 - data/csv/measurements/*.csv  ->  data/csv/wells/<hole_id>.csv

Reads the tidy measurement tables written by etl.py, collapses the many spectral
readings inside every metre into one dominant mineral group, and records how far
that answer can be trusted.

Throwing the quality flags away here would make it impossible to be honest about
the data later, so every one of them is carried through to the viewer.

    python extract.py --out data                 # every hole under data/csv/measurements
    python extract.py KD1 MPWD3 --out data       # named holes only
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


HERE = Path(__file__).resolve().parent
BIN = 1.0  # metres per reported interval - site.py reads this from the manifest

# TSG writes this sentinel where a scalar is absent.
NODATA = float(np.finfo("float32").min)

# Columns in measurements.csv are named after the NVCL log_name, and the exact
# spelling varies between providers and datasets. Each entry is a list of
# normalised candidates tried in order; see resolve_column().
COLUMN_CANDIDATES = {
    "group": ["grp1stsas", "grp1utsas", "grp1stsat", "grp1utsat", "grp1stsav", "grp1utsav"],
    "mineral": ["min1stsas", "min1utsas", "min1stsat", "min1utsat", "min1stsav", "min1utsav"],
    "weight": ["wt1stsas", "wt1utsas", "wt1stsat", "wt1utsat", "wt1stsav", "wt1utsav"],
    "error": ["errorstsas", "errorutsas", "errorstsat", "errorutsat", "error"],
    "snr": ["snrstsas", "snrutsas", "snr"],
}

# Each rule that fires is one strike against the interval.
# "needs" names the input a rule depends on; rules whose input is missing from
# this provider's logs are dropped and reported, never silently passed.
CHECKS = [
    ("coverage", None, lambda d: d["coverage"] < 0.5,
     "over half this metre had no usable reading"),
    ("agreement", None, lambda d: d["agreement"] < 0.6,
     "the readings within this metre disagree with each other"),
    # .where(notna) lets a missing quality number propagate as NA, which score()
    # then counts as a strike. A metre we could not check is not a metre that passed.
    ("fit", "error", lambda d: (d["err"] > 400).where(d["err"].notna()),
     "the spectrum was a poor fit to any known mineral"),
    ("signal", "snr", lambda d: (d["snr"] < 200).where(d["snr"].notna()),
     "weak signal (low signal-to-noise)"),
    ("dominance", "weight", lambda d: (d["wt"] < 0.4).where(d["wt"].notna()),
     "no single mineral clearly dominates"),
]

# Some datasets store the group as a code and only the mineral name as text,
# so fall back to the mineral name and roll it up to its group.
MIN2GRP = {
    "Chlorite": "CHLORITE", "Kaolinite": "KAOLIN", "Dickite": "KAOLIN",
    "Nacrite": "KAOLIN", "Halloysite": "KAOLIN", "Muscovite": "WHITE-MICA",
    "Paragonite": "WHITE-MICA", "Illite": "WHITE-MICA", "Phengite": "WHITE-MICA",
    "Biotite": "DARK-MICA", "Phlogopite": "DARK-MICA", "Calcite": "CARBONATE",
    "Dolomite": "CARBONATE", "Ankerite": "CARBONATE", "Siderite": "CARBONATE",
    "Magnesite": "CARBONATE", "Gypsum": "SULPHATE", "Alunite": "SULPHATE",
    "Jarosite": "SULPHATE", "Epidote": "EPIDOTE", "Zoisite": "EPIDOTE",
    "Clinozoisite": "EPIDOTE", "Hornblende": "AMPHIBOLE", "Actinolite": "AMPHIBOLE",
    "Tremolite": "AMPHIBOLE", "Riebeckite": "AMPHIBOLE", "Talc": "OTHER-MGOH",
    "Serpentine": "SERPENTINE", "Montmorillonite": "SMECTITE",
    "Nontronite": "SMECTITE", "Saponite": "SMECTITE", "Tourmaline": "TOURMALINE",
    "Rubellite": "TOURMALINE", "Prehnite": "OTHER-MGOH", "Pyrophyllite": "OTHER",
    "Topaz": "OTHER", "Diaspore": "OTHER", "Gibbsite": "OTHER",
}

# Readings that are not rock at all, or that the classifier refused to call.
BLANK = {"", "NAN", "NONE", "INVALID", "ASPECTRAL", "NOTAROK", "NOT A ROCK",
         "WHITEMARKER", "WHITE MARKER", "WOOD", "VEGETATION", "NULL", "NA", "N/A"}

OUT_FIELDS = [
    "hole_id", "hole_name", "depth", "mineral", "conf_class", "confidence",
    "n_flags", "why", "coverage", "agreement", "wt", "err", "snr",
    "n_valid", "n_readings",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalise(name: object) -> str:
    """Fold a column name to letters and digits so 'Grp1 sTSAS' == 'grp1stsas'."""
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def resolve_column(columns: list[str], candidates: list[str]) -> str | None:
    """Find the real column name for a normalised candidate.

    etl.py disambiguates repeated log names by appending ' (2)', so an exact
    match is tried first and a prefix match second.
    """
    folded = {normalise(column): column for column in columns}
    for candidate in candidates:
        if candidate in folded:
            return folded[candidate]
    for candidate in candidates:
        for key, column in folded.items():
            if key.startswith(candidate):
                return column
    return None


def resolve_all(columns: list[str]) -> dict[str, str | None]:
    return {role: resolve_column(columns, names) for role, names in COLUMN_CANDIDATES.items()}


def active_checks(found: dict[str, str | None]) -> list[tuple]:
    return [item for item in CHECKS if item[1] is None or found.get(item[1])]


def numeric(frame: pd.DataFrame, column: str | None) -> pd.Series:
    """Pull a numeric quality column, turning TSG's sentinel value into NaN."""
    if not column or column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    values = pd.to_numeric(frame[column], errors="coerce")
    return values.mask(values <= NODATA / 1e10)


def mineral_series(frame: pd.DataFrame, found: dict[str, str | None]) -> pd.Series:
    """Return a text mineral-group series from whichever column holds text."""
    group_column = found.get("group")
    if group_column:
        text = frame[group_column].astype(str).str.strip()
        looks_like_words = text.str.fullmatch(r"[A-Za-z][A-Za-z0-9 _/-]*").mean()
        if looks_like_words > 0.2:
            return text.str.upper()
    mineral_column = found.get("mineral")
    if mineral_column:
        head = frame[mineral_column].astype(str).str.strip().str.split("-").str[0]
        return head.map(lambda name: MIN2GRP.get(name, str(name).upper()))
    raise ValueError("no usable mineral group or mineral name column")


def summarise_bin(frame: pd.DataFrame) -> dict:
    """Collapse every reading inside one metre into a single honest row."""
    valid = frame[frame["ok"]]
    if len(valid) == 0:
        mineral, agreement = "NO DATA", 0.0
    else:
        counts = valid["mineral"].value_counts()
        mineral = str(counts.index[0])
        agreement = float(counts.iloc[0]) / len(valid)
    return {
        "mineral": mineral,
        "n_readings": int(len(frame)),
        "n_valid": int(frame["ok"].sum()),
        "coverage": float(frame["ok"].mean()),
        "agreement": float(agreement),
        "wt": float(valid["wt"].median()) if len(valid) else float("nan"),
        "err": float(valid["err"].median()) if len(valid) else float("nan"),
        "snr": float(valid["snr"].median()) if len(valid) else float("nan"),
    }


def score(rows: pd.DataFrame, checks: list[tuple]) -> pd.DataFrame:
    """Apply the active checks, record which fired, and grade each interval."""
    if not checks:
        raise ValueError("no confidence checks are available for this hole")
    fired = pd.DataFrame(
        {name: rule(rows).fillna(True) for name, _, rule, _ in checks},
        index=rows.index,
    )
    reasons = {name: reason for name, _, _, reason in checks}
    rows = rows.copy()
    rows["why"] = [
        " ; ".join(reasons[name] for name in fired.columns if bool(row[name]))
        for _, row in fired.iterrows()
    ]
    rows["n_flags"] = fired.sum(axis=1).astype(int)
    rows["confidence"] = (1 - rows["n_flags"] / len(checks)).round(2)
    rows["conf_class"] = np.where(
        rows["n_flags"] == 0, "high", np.where(rows["n_flags"] == 1, "medium", "low")
    )
    return rows


def safe_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "hole"


def hole_names(csv_root: Path) -> dict[str, str]:
    """hole_id -> hole_name, from the holes.csv that etl.py rolls up."""
    path = csv_root / "holes.csv"
    if not path.is_file():
        return {}
    table = pd.read_csv(path, dtype=str).fillna("")
    if "hole_id" not in table:
        return {}
    return {
        str(row["hole_id"]).strip(): (str(row.get("hole_name") or "").strip()
                                      or str(row["hole_id"]).strip())
        for _, row in table.iterrows()
        if str(row["hole_id"]).strip()
    }


def transform_hole(path: Path, hole_id: str, hole_name: str) -> tuple[pd.DataFrame, list[str]]:
    frame = pd.read_csv(path, low_memory=False)
    if "depth_from_m" not in frame.columns:
        raise ValueError("measurements.csv has no depth_from_m column")

    found = resolve_all(list(frame.columns))
    checks = active_checks(found)
    if not found.get("group") and not found.get("mineral"):
        raise ValueError(
            "no TSA mineral column found - looked for "
            + ", ".join(COLUMN_CANDIDATES["group"] + COLUMN_CANDIDATES["mineral"])
        )

    depth = pd.to_numeric(frame["depth_from_m"], errors="coerce")
    raw = pd.DataFrame({
        "depth": (depth // BIN) * BIN,
        "mineral": mineral_series(frame, found),
        "wt": numeric(frame, found.get("weight")),
        "err": numeric(frame, found.get("error")),
        "snr": numeric(frame, found.get("snr")),
    })
    raw = raw[raw["depth"].notna()]
    if raw.empty:
        raise ValueError("no rows with a usable depth")
    raw["mineral"] = raw["mineral"].fillna("")
    raw["ok"] = ~raw["mineral"].str.upper().str.strip().isin(BLANK)

    binned = pd.DataFrame(
        [summarise_bin(group) | {"depth": float(key)}
         for key, group in raw.groupby("depth", sort=True)]
    )
    binned = score(binned, checks)
    binned["hole_id"] = hole_id
    binned["hole_name"] = hole_name
    return binned[OUT_FIELDS].round(3), [item[0] for item in checks]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Turn etl.py measurement tables into per-metre mineral calls with confidence."
    )
    parser.add_argument("hole_ids", nargs="*",
                        help="hole ids to process; omit to do every hole found")
    parser.add_argument("--out", default=str(HERE / "data"),
                        help="data root (default: ./data)")
    parser.add_argument("--csv-root", help="override <out>/csv")
    parser.add_argument("--no-combined", action="store_true",
                        help="skip writing the concatenated data/csv/wells.csv")
    args = parser.parse_args()

    data_root = Path(args.out).resolve()
    csv_root = Path(args.csv_root).resolve() if args.csv_root else data_root / "csv"
    source_dir = csv_root / "measurements"
    if not source_dir.is_dir():
        parser.error(
            f"no measurement tables at {source_dir}\n"
            "Run download.py then etl.py first, or point --out at the right data root."
        )

    if args.hole_ids:
        sources = [(hole, source_dir / f"{hole}.csv") for hole in args.hole_ids]
        missing = [str(path) for _, path in sources if not path.is_file()]
        if missing:
            parser.error("missing measurement tables:\n  " + "\n  ".join(missing))
    else:
        sources = [(path.stem, path) for path in sorted(source_dir.glob("*.csv"))]
    if not sources:
        parser.error(f"no *.csv found in {source_dir}")

    names = hole_names(csv_root)
    target_dir = csv_root / "wells"
    target_dir.mkdir(parents=True, exist_ok=True)

    combined = None
    if not args.no_combined:
        combined_path = csv_root / "wells.csv"
        combined = combined_path.open("w", encoding="utf-8-sig", newline="")
        writer = csv.writer(combined, lineterminator="\n")
        writer.writerow(OUT_FIELDS)

    # A check only counts as available if every hole had the column it needs -
    # taking the union here would hide a gap in most of the survey.
    done, failed, class_totals = [], [], {"high": 0, "medium": 0, "low": 0}
    checks_seen: set[str] | None = None
    try:
        for index, (hole_id, path) in enumerate(sources, start=1):
            label = f"[{index}/{len(sources)}] {hole_id}"
            try:
                rows, checks = transform_hole(path, hole_id, names.get(hole_id, hole_id))
            except Exception as exc:  # one bad hole must not stop the batch
                failed.append({"hole_id": hole_id, "error": f"{type(exc).__name__}: {exc}"})
                print(f"{label} FAIL {type(exc).__name__}: {exc}", flush=True)
                continue

            checks_seen = set(checks) if checks_seen is None else checks_seen & set(checks)
            out_path = target_dir / f"{safe_stem(hole_id)}.csv"
            rows.to_csv(out_path, index=False, encoding="utf-8-sig", lineterminator="\n")
            if combined is not None:
                rows.to_csv(combined, index=False, header=False, lineterminator="\n")

            counts = rows["conf_class"].value_counts().to_dict()
            for key in class_totals:
                class_totals[key] += int(counts.get(key, 0))
            low_share = int(counts.get("low", 0)) / max(len(rows), 1)
            done.append({
                "hole_id": hole_id,
                "hole_name": names.get(hole_id, hole_id),
                "intervals": int(len(rows)),
                "max_depth_m": float(rows["depth"].max()),
                "low": int(counts.get("low", 0)),
                "medium": int(counts.get("medium", 0)),
                "high": int(counts.get("high", 0)),
                "checks": checks,
            })
            print(f"{label} {len(rows):5d} intervals  {rows['depth'].max():7.1f} m  "
                  f"{low_share:5.0%} low-confidence", flush=True)
    finally:
        if combined is not None:
            combined.close()

    available = checks_seen or set()
    dropped = sorted({name for name, _, _, _ in CHECKS} - available)
    summary = {
        "generated_at": utc_now(),
        "bin_m": BIN,
        "checks_total": len(CHECKS),
        "checks_used": sorted(available),
        "checks_unavailable": dropped,
        "holes_complete": len(done),
        "holes_failed": len(failed),
        "intervals": sum(item["intervals"] for item in done),
        "conf_class_totals": class_totals,
        "holes": done,
        "failures": failed,
    }
    (csv_root / "extract-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    total = sum(class_totals.values()) or 1
    print(f"\nwrote {len(done)} holes, {summary['intervals']} intervals -> {target_dir}")
    print("  " + "  ".join(f"{key}={value} ({value / total:.0%})"
                           for key, value in class_totals.items()))
    if dropped:
        print("  confidence checks unavailable in this data: " + ", ".join(dropped))
        print("  (the viewer says so on screen - it does not pretend they passed)")
    if failed:
        print(f"  {len(failed)} holes failed; see {csv_root / 'extract-summary.json'}")
    return 1 if failed and not done else 0


if __name__ == "__main__":
    raise SystemExit(main())
