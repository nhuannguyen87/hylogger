#!/usr/bin/env python
"""Cross-hole anomaly detection for NVCL drill holes.

Reads the per-hole measurement tables written by etl.py, builds one comparable
feature space across every hole, and scores two things:

  1. interval anomalies - which depth slices look unlike the rest of the field
  2. hole anomalies     - which whole holes look unlike their neighbours

The model is deliberately a transparent baseline: robust scaling -> PCA ->
Mahalanobis distance in principal-component space, plus PCA reconstruction
error for structure the components cannot explain. Isolation Forest is added
as a third opinion when scikit-learn is installed. Every score carries a
coverage figure so a thin depth slice is never presented as confidently as a
well-measured one.

Usage:
    python anomaly.py --out files/data
    python anomaly.py --out files/data --bin-size 2 --top 15
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest import signals
from flask import signals

import numpy as np
import pandas as pd

try:
    from sklearn.ensemble import IsolationForest
    from sklearn.neighbors import LocalOutlierFactor

    HAVE_SKLEARN = True
except ImportError:  # optional third signal
    HAVE_SKLEARN = False

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


HERE = Path(__file__).resolve().parent
ID_COLUMNS = {
    "hole_id",
    "holeid",
    "sample_no",
    "depth_from_m",
    "depth_to_m",
}
MAX_CATEGORIES = 12
MIN_NUMERIC_FRACTION = 0.80
VARIANCE_TARGET = 0.95
INLIER_FRACTION = 0.80
PRIOR_WEIGHT = 10.0

EARTH_RADIUS_KM = 6371.0088
LOCAL_NEIGHBOURS = 5

# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load_measurements(csv_root: Path) -> pd.DataFrame:
    """Concatenate every per-hole measurement table into one frame."""
    folder = csv_root / "measurements"
    if not folder.is_dir():
        raise SystemExit(
            f"no measurement tables at {folder}\n"
            "Run download.py then etl.py first, or point --csv-root at the right root."
        )
    paths = sorted(folder.glob("*.csv"))
    if not paths:
        raise SystemExit(f"no CSV files in {folder}")

    frames = []
    for path in paths:
        frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        if "hole_id" not in frame.columns:
            frame["hole_id"] = path.stem
        frames.append(frame)
    data = pd.concat(frames, ignore_index=True, sort=False)
    data["hole_id"] = data["hole_id"].astype(str)
    for column in ("depth_from_m", "depth_to_m"):
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    return data.dropna(subset=["depth_from_m"])


def split_column_types(data: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Sort measurement columns into numeric and categorical."""
    numeric, categorical = [], []

    for column in data.columns:
        lower = column.lower()

        if lower in ID_COLUMNS:
            continue

        if lower.startswith(("date", "holeid", "tray", "secsamp", "subpix")):
            continue

        values = data[column]
        present = values.notna() & (values.astype(str).str.strip() != "")

        if not present.any():
            continue

        parsed = pd.to_numeric(values[present], errors="coerce")

        if parsed.notna().mean() >= MIN_NUMERIC_FRACTION:
            numeric.append(column)
        else:
            categorical.append(column)

    return numeric, categorical

def report_channel_agreement(data: pd.DataFrame, min_overlap: int = 100) -> None:
    """Compare related HyLogger mineral classification channels."""

    feature_groups = (
        "Min1",
        "Min2",
        "Min3",
        "Grp1",
        "Grp2",
        "Grp3",
    )

    channel_types = (
        "sTSAS",
        "uTSAS",
        "sjCLST",
        "ujCLST",
        "sTSAV",
    )

    missing_values = {
        "",
        "nan",
        "none",
        "null",
        "na",
        "n/a",
        "invalid",
    }

    results = []

    for feature_group in feature_groups:
        columns = [
            f"{feature_group} {channel}"
            for channel in channel_types
            if f"{feature_group} {channel}" in data.columns
        ]

        for i in range(len(columns)):
            for j in range(i + 1, len(columns)):
                column_a = columns[i]
                column_b = columns[j]

                values_a = data[column_a].astype("string").str.strip()
                values_b = data[column_b].astype("string").str.strip()

                valid = (
                    values_a.notna()
                    & values_b.notna()
                    & ~values_a.str.lower().isin(missing_values)
                    & ~values_b.str.lower().isin(missing_values)
                )

                overlap = int(valid.sum())

                if overlap < min_overlap:
                    continue

                agreement = (
                    values_a[valid].str.casefold()
                    == values_b[valid].str.casefold()
                ).mean()

                results.append(
                    {
                        "feature": feature_group,
                        "channel_a": column_a,
                        "channel_b": column_b,
                        "overlap": overlap,
                        "agreement_pct": agreement * 100,
                    }
                )

    if not results:
        print("\nchannel agreement: no comparable channel pairs found")
        return

    report = pd.DataFrame(results)

    report = report.sort_values(
        ["feature", "agreement_pct"],
        ascending=[True, False],
    )

    print("\nHyLogger channel agreement")
    print(
        report.to_string(
            index=False,
            formatters={
                "agreement_pct": lambda value: f"{value:.1f}%"
            },
        )
    )


def report_channel_disagreements(
    data: pd.DataFrame,
    limit: int = 5,
) -> None:
    """Show the most common disagreements between CLST channel pairs."""

    feature_groups = (
        "Min1",
        "Min2",
        "Min3",
        "Grp1",
        "Grp2",
        "Grp3",
    )

    missing_values = {
        "",
        "nan",
        "none",
        "null",
        "na",
        "n/a",
        "invalid",
    }

    print("\nHyLogger CLST disagreement examples")

    for feature_group in feature_groups:
        column_a = f"{feature_group} sjCLST"
        column_b = f"{feature_group} ujCLST"

        if column_a not in data.columns or column_b not in data.columns:
            continue

        values_a = data[column_a].astype("string").str.strip()
        values_b = data[column_b].astype("string").str.strip()

        valid = (
            values_a.notna()
            & values_b.notna()
            & ~values_a.str.lower().isin(missing_values)
            & ~values_b.str.lower().isin(missing_values)
        )

        different = valid & (
            values_a.str.casefold() != values_b.str.casefold()
        )

        count = int(different.sum())

        print(f"\n{feature_group}: {count:,} disagreements")

        if count == 0:
            continue

        pairs = (
            pd.DataFrame(
                {
                    "sjCLST": values_a[different],
                    "ujCLST": values_b[different],
                }
            )
            .value_counts()
            .head(limit)
        )

        print(pairs.to_string())



def remove_redundant_classification_channels(
    data: pd.DataFrame,
    categorical: list[str],
    agreement_threshold: float = 0.999,
    min_overlap: int = 1000,
) -> tuple[list[str], list[str]]:
    """
    Remove classification channels that are effectively duplicates.
    """

    feature_groups = (
        "Min1",
        "Min2",
        "Min3",
        "Grp1",
        "Grp2",
        "Grp3",
    )

    candidate_pairs = (
        ("sTSAS", "uTSAS"),
        ("sjCLST", "ujCLST"),
    )

    missing_values = {
        "",
        "nan",
        "none",
        "null",
        "na",
        "n/a",
        "invalid",
    }

    dropped = set()

    for feature_group in feature_groups:
        for keep_channel, candidate_channel in candidate_pairs:
            column_a = f"{feature_group} {keep_channel}"
            column_b = f"{feature_group} {candidate_channel}"

            if column_a not in categorical or column_b not in categorical:
                continue

            values_a = data[column_a].astype("string").str.strip()
            values_b = data[column_b].astype("string").str.strip()

            valid = (
                values_a.notna()
                & values_b.notna()
                & ~values_a.str.lower().isin(missing_values)
                & ~values_b.str.lower().isin(missing_values)
            )

            overlap = int(valid.sum())

            if overlap < min_overlap:
                continue

            agreement = (
                values_a[valid].str.casefold()
                == values_b[valid].str.casefold()
            ).mean()

            if agreement >= agreement_threshold:
                dropped.add(column_b)

                print(
                    f"dropping redundant channel {column_b} "
                    f"(matches {column_a}: {agreement * 100:.1f}% "
                    f"over {overlap:,} samples)"
                )

    filtered = [
        column
        for column in categorical
        if column not in dropped
    ]

    return filtered, sorted(dropped)


# ---------------------------------------------------------------------------
# feature building
# ---------------------------------------------------------------------------

def build_interval_features(
    data: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
    bin_size: float,
) -> tuple[pd.DataFrame, np.ndarray, list[str], set[str]]:
    """Aggregate samples into fixed depth bins, one row per hole per bin.

    Binning is what makes holes comparable: raw sample spacing differs
    between instruments and runs, so scoring raw rows would compare a 1 cm
    slice in one hole against a 10 cm slice in another.
    """
    frame = data.copy()
    frame["bin_start"] = np.floor(frame["depth_from_m"] / bin_size) * bin_size
    frame["bin_end"] = frame["bin_start"] + bin_size

    keys = ["hole_id", "bin_start", "bin_end"]
    grouped = frame.groupby(keys, sort=True)
    sizes = grouped.size()

    parts = [sizes.rename("n_samples")]
    prescaled: set[str] = set()

    # Within-bin spread is informative texture, but it is pure noise when a
    # bin holds only a handful of samples, and noisy features drown real
    # signal. Only include it once bins are thick enough to mean something.
    include_spread = float(sizes.median()) >= 5

    for column in numeric:
        values = pd.to_numeric(frame[column], errors="coerce")
        by_bin = values.groupby([frame[k] for k in keys])
        parts.append(by_bin.mean().rename(f"{column} [mean]"))
        if include_spread:
            parts.append(by_bin.std().rename(f"{column} [sd]"))

    for column in categorical:
        text = frame[column].astype(str).str.strip()
        blank = text.str.lower().isin(["", "nan", "none", "null", "na", "n/a", "invalid"])
        text = text.where(~blank)
        top = text.value_counts().head(MAX_CATEGORIES).index.tolist()
        for category in top:
            indicator = (text == category).astype(float)
            counts = indicator.groupby([frame[k] for k in keys]).sum()
            prior = float(indicator.mean())
            # A proportion measured from a handful of samples is not a
            # confident estimate: with four samples and three minerals, all
            # four landing on one mineral happens by chance several percent of
            # the time, and a raw proportion of 1.0 would score as a 4-sigma
            # event. Shrinking toward the field-wide rate, weighted by how many
            # samples the bin actually holds, keeps thin bins honest while
            # leaving well-sampled bins essentially untouched.
            # Score the proportion against its own sampling uncertainty
            # rather than its empirical spread. A bin where all four samples
            # happen to share one mineral is unremarkable - that occurs by
            # chance several percent of the time - while the same proportion
            # over a hundred samples is a real finding. The binomial standard
            # error encodes exactly that difference, and unlike a rescaled
            # proportion it cannot be flattened back out by robust scaling.
            expected = sizes * prior
            error = np.sqrt(np.maximum(sizes * prior * (1.0 - prior), 1e-9))
            deviate = (counts - expected) / error
            name = f"{column} = {category}"
            parts.append(deviate.rename(name))
            prescaled.add(name)

    table = pd.concat(parts, axis=1).reset_index()
    feature_names = [c for c in table.columns if c not in {*keys, "n_samples"}]
    matrix = table[feature_names].to_numpy(dtype=float)
    return table, matrix, feature_names, prescaled


def coverage_of(matrix: np.ndarray) -> np.ndarray:
    """Fraction of features actually observed for each row."""
    if matrix.shape[1] == 0:
        return np.zeros(matrix.shape[0])
    return np.isfinite(matrix).mean(axis=1)


def robust_scale(
    matrix: np.ndarray,
    names: list[str] | None = None,
    prescaled: set[str] | None = None,
) -> tuple[np.ndarray, list[int]]:
    """Median/IQR scaling, dropping features with no spread.

    Median and IQR are used rather than mean and standard deviation because
    the anomalies themselves would otherwise inflate the scale and hide.
    """
    prescaled = prescaled or set()
    keep, columns = [], []
    for index in range(matrix.shape[1]):
        column = matrix[:, index]
        finite = column[np.isfinite(column)]
        if finite.size < 3:
            continue
        if names is not None and names[index] in prescaled:
            # already expressed in standard errors - rescaling would destroy
            # the sample-size information that makes it trustworthy
            values = np.where(np.isfinite(column), column, 0.0)
            columns.append(np.clip(values, -25, 25))
            keep.append(index)
            continue
        centre = np.median(finite)
        q75, q25 = np.percentile(finite, [75, 25])
        spread = (q75 - q25) / 1.349
        # A quantized feature - a mineral proportion over a thin bin can only
        # take a few values - can have a near-zero IQR, which would turn
        # ordinary variation into enormous z-scores. Floor the spread against
        # the ordinary standard deviation to stop that artefact.
        spread = max(spread, 0.5 * float(finite.std()))
        if not np.isfinite(spread) or spread <= 1e-12:
            spread = finite.std()
        if not np.isfinite(spread) or spread <= 1e-12:
            continue
        scaled = (column - centre) / spread
        scaled = np.where(np.isfinite(scaled), scaled, 0.0)
        columns.append(np.clip(scaled, -25, 25))
        keep.append(index)
    if not columns:
        raise SystemExit("no usable features - every column was constant or empty")
    return np.column_stack(columns), keep


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def robust_norm(scaled: np.ndarray) -> np.ndarray:
    """Distance from the field's typical value, in robust standard deviations.

    Because the columns were scaled by median and IQR, this is simply how far
    out a row sits overall. It is the signal that catches a depth slice with
    an obviously extreme value in one measurement.
    """
    return np.linalg.norm(scaled, axis=1) / np.sqrt(max(scaled.shape[1], 1))


def pca_scores(scaled: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Mahalanobis distance and reconstruction error, fitted on inliers only.

    Fitting on every row is what breaks naive anomaly detection: a cluster of
    outliers inflates the covariance along its own direction, so whitening
    divides their distance away and they score as ordinary. This is the
    masking problem, and it is why an unguarded version of this function
    missed a planted anomaly entirely.

    The fix is to fit the subspace on the least extreme INLIER_FRACTION of
    rows, then measure every row against that clean subspace. Outliers cannot
    then hide inside a covariance they defined themselves.

    The two returned scores catch different things. Mahalanobis flags rows far
    out along directions the field does vary in. Reconstruction error flags
    rows whose pattern the components cannot express at all - a combination
    nothing else in the field shows.
    """
    if scaled.shape[0] < 10:
        distance = robust_norm(scaled)
        return distance, np.zeros_like(distance), 0

    extremity = robust_norm(scaled)
    cutoff = np.quantile(extremity, INLIER_FRACTION)
    inliers = scaled[extremity <= cutoff]
    if inliers.shape[0] < max(5, scaled.shape[1]):
        inliers = scaled

    centre = np.median(inliers, axis=0)
    reference = inliers - centre
    _, singular, vectors = np.linalg.svd(reference, full_matrices=False)

    variance = singular**2
    if variance.sum() <= 0:
        distance = robust_norm(scaled)
        return distance, np.zeros_like(distance), 0
    cumulative = np.cumsum(variance) / variance.sum()
    n_components = int(np.searchsorted(cumulative, VARIANCE_TARGET) + 1)
    n_components = max(1, min(n_components, reference.shape[0] - 1, reference.shape[1]))

    components = vectors[:n_components]
    centred = scaled - centre
    projected = centred @ components.T

    deviation = singular[:n_components] / np.sqrt(max(reference.shape[0] - 1, 1))
    deviation = np.where(deviation > 1e-9, deviation, 1e-9)
    mahalanobis = np.sqrt(((projected / deviation) ** 2).sum(axis=1))

    reconstructed = projected @ components
    residual = np.linalg.norm(centred - reconstructed, axis=1)
    return mahalanobis, residual, n_components


def isolation_scores(scaled: np.ndarray, seed: int = 0) -> np.ndarray | None:
    """Isolation Forest, higher meaning more anomalous. None without sklearn."""
    if not HAVE_SKLEARN or scaled.shape[0] < 20:
        return None
    model = IsolationForest(
        n_estimators=300,
        contamination="auto",
        random_state=seed,
        n_jobs=-1,
    )
    model.fit(scaled)
    return -model.score_samples(scaled)

def lof_scores(scaled: np.ndarray) -> np.ndarray | None:
    """Local Outlier Factor score, higher meaning more locally unusual."""
    if not HAVE_SKLEARN or scaled.shape[0] < 20:
        return None

    n_neighbors = min(20, scaled.shape[0] - 1)

    model = LocalOutlierFactor(
        n_neighbors=n_neighbors,
        contamination="auto",
    )

    model.fit_predict(scaled)

    return -model.negative_outlier_factor_

def to_percentile(values: np.ndarray) -> np.ndarray:
    """Rank-transform to 0-100 so different scorers can be averaged."""
    if values.size == 0:
        return values
    order = values.argsort().argsort().astype(float)
    return 100.0 * order / max(len(values) - 1, 1)


def combine(signals: list[np.ndarray]) -> np.ndarray:
    """Average the percentile ranks of each available signal."""
    return np.mean([to_percentile(signal) for signal in signals], axis=0)


def flag_of(score: float, coverage: float, max_z: float) -> str:
    """Combine the relative rank with an absolute test.

    A percentile alone would flag the top 5% of intervals even in a perfectly
    uniform field, which is a dishonest way to present a result. The max_z
    term asks a separate question - is any single measurement here actually
    extreme - so "high" cannot be earned by rank alone.
    """
    if coverage < 0.30:
        return "insufficient data"
    if score < 95.0:
        return "normal"
    if max_z >= 6.0:
        return "high"
    if max_z >= 4.0:
        return "elevated"
    return "watch"


def score_reliability_of(coverage: float, n_samples: int) -> str:
    if coverage < 0.30 or n_samples < 2:
        return "low"
    if coverage < 0.60 or n_samples < 4:
        return "medium"
    return "high"


def feature_group_name(name: str) -> str:
    """
    Collapse related HyLogger mineral-call features into a common group.

    Examples:
      'Min1 sTSAS = Talc'      -> 'Min1 = Talc'
      'Min1 uTSAS = Talc'      -> 'Min1 = Talc'
      'Min1 sjCLST = Talc'     -> 'Min1 = Talc'
      'Grp1 uTSAS = SMECTITE'  -> 'Grp1 = SMECTITE'
    """
    parts = name.split(" = ", 1)

    if len(parts) != 2:
        return name

    column, category = parts
    tokens = column.split()

    if len(tokens) >= 2 and tokens[0].startswith(("Min", "Grp")):
        return f"{tokens[0]} = {category}"

    return name




def top_contributors(row: np.ndarray, names: list[str], limit: int = 3) -> str:
    """Which scaled features drove this row's distance, for explainability."""
    if row.size == 0:
        return ""

    order = np.argsort(-np.abs(row))
    parts = []
    seen_groups = set()

    for index in order:
        if abs(row[index]) < 1.0:
            continue

        grouped_name = feature_group_name(names[index])

        if grouped_name in seen_groups:
            continue

        seen_groups.add(grouped_name)

        direction = "high" if row[index] > 0 else "low"
        value = abs(row[index])

        if value >= 24.99:
            magnitude = ">=25.0 sd"
        else:
            magnitude = f"{value:.1f} sd"

        parts.append(f"{grouped_name} ({direction}, {magnitude})")

        if len(parts) >= limit:
            break

    return "; ".join(parts)


def load_hole_locations(csv_root: Path) -> pd.DataFrame | None:
    """Load drill-hole coordinates produced by ETL."""
    path = csv_root / "holes.csv"

    if not path.is_file():
        print("warning: holes.csv not found; geographic anomaly scoring disabled")
        return None

    locations = pd.read_csv(path)

    required = {"hole_id", "latitude", "longitude"}
    if not required.issubset(locations.columns):
        print("warning: holes.csv has no latitude/longitude; geographic scoring disabled")
        return None

    locations = locations[list(required)].copy()
    locations["hole_id"] = locations["hole_id"].astype(str)
    locations["latitude"] = pd.to_numeric(locations["latitude"], errors="coerce")
    locations["longitude"] = pd.to_numeric(locations["longitude"], errors="coerce")

    return locations.dropna(subset=["latitude", "longitude"])


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between coordinates in kilometres."""
    lat1, lon1, lat2, lon2 = map(
        np.radians, [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    )

    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))



# ---------------------------------------------------------------------------
# hole level
# ---------------------------------------------------------------------------

def score_holes(
    table: pd.DataFrame,
    scaled: np.ndarray,
    names: list[str],
    locations: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Reduce each hole to a profile, then compare profiles to each other."""
    profiles, hole_ids = [], []
    for hole_id, index in table.groupby("hole_id").groups.items():
        rows = scaled[table.index.get_indexer(index)]
        profiles.append(
            np.concatenate([np.median(rows, axis=0), rows.std(axis=0)])
        )
        hole_ids.append(hole_id)
    profile_matrix = np.vstack(profiles)
    profile_names = [f"{n} (hole level)" for n in names] + [f"{n} (hole spread)" for n in names]

    # Profiles are already in robust-scaled units, so distance from the field
    # median is directly interpretable. With only a handful of holes there is
    # no covariance worth estimating, so PCA is skipped rather than faked.
    centred = profile_matrix - np.median(profile_matrix, axis=0)
    distance = np.linalg.norm(centred, axis=1) / np.sqrt(max(centred.shape[1], 1))
    components = 0
    if len(hole_ids) >= 12:
        _, residual, components = pca_scores(profile_matrix)
        score = combine([distance, residual])
    else:
        score = to_percentile(distance)

    # nearest neighbour tells a geologist which hole this one most resembles
    gram = ((profile_matrix[:, None, :] - profile_matrix[None, :, :]) ** 2).sum(axis=2)
    np.fill_diagonal(gram, np.inf)
    nearest = gram.argmin(axis=1)

    local_score = np.full(len(hole_ids), np.nan)
    nearest_geo = [None] * len(hole_ids)
    nearest_geo_km = np.full(len(hole_ids), np.nan)

    if locations is not None:
        location_map = locations.set_index("hole_id")

        for i, hole_id in enumerate(hole_ids):
            if hole_id not in location_map.index:
                continue

            current = location_map.loc[hole_id]

            candidates = []

            for j, other_id in enumerate(hole_ids):
                if i == j or other_id not in location_map.index:
                    continue

                other = location_map.loc[other_id]

                distance_km = haversine_km(
                    current["latitude"],
                    current["longitude"],
                    other["latitude"],
                    other["longitude"],
                )

                candidates.append((distance_km, j, other_id))

            candidates.sort(key=lambda x: x[0])
            neighbours = candidates[:LOCAL_NEIGHBOURS]

            if not neighbours:
                continue

            nearest_geo[i] = neighbours[0][2]
            nearest_geo_km[i] = neighbours[0][0]

            feature_distances = [
                np.linalg.norm(profile_matrix[i] - profile_matrix[j])
                / np.sqrt(max(profile_matrix.shape[1], 1))
                for _, j, _ in neighbours
            ]

            local_score[i] = float(np.mean(feature_distances))

    result = pd.DataFrame(
        {
            "hole_id": hole_ids,
            "n_intervals": [int((table["hole_id"] == h).sum()) for h in hole_ids],
            "profile_distance": np.round(distance, 4),
            "anomaly_score": np.round(score, 2),
            "rank": (-score).argsort().argsort() + 1,
            "most_like": [hole_ids[i] for i in nearest],
            "nearest_geographic_hole": nearest_geo,
            "nearest_geographic_km": np.round(nearest_geo_km, 2),
            "local_anomaly_distance": np.round(local_score, 4),
            "distinctive_features": [
                top_contributors(centred[i], profile_names) for i in range(len(hole_ids))
            ],
        }
    )
    result.attrs["components"] = components
    return result.sort_values("anomaly_score", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------

def write_outputs(
    data_root: Path,
    intervals: pd.DataFrame,
    holes: pd.DataFrame,
    meta: dict,
) -> list[Path]:
    written = []

    folder = data_root / "csv" / "anomalies"
    folder.mkdir(parents=True, exist_ok=True)
    for hole_id, group in intervals.groupby("hole_id"):
        path = folder / f"{hole_id}.csv"
        group.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")
        written.append(path)

    combined = data_root / "csv" / "anomalies.csv"
    intervals.to_csv(combined, index=False, encoding="utf-8-sig", lineterminator="\n")
    written.append(combined)

    holes_path = data_root / "csv" / "hole_anomalies.csv"
    holes.to_csv(holes_path, index=False, encoding="utf-8-sig", lineterminator="\n")
    written.append(holes_path)

    # compact JSON for the viewer to colour strip logs without parsing CSV
    payload = {
        "meta": meta,
        "holes": {
            row.hole_id: {
                "anomaly_score": float(row.anomaly_score),
                "rank": int(row.rank),
                "most_like": row.most_like,
                "distinctive_features": row.distinctive_features,
            }
            for row in holes.itertuples()
        },
        "intervals": {
            hole_id: [
                {
                    "depth_from_m": float(r.depth_from_m),
                    "depth_to_m": float(r.depth_to_m),
                    "score": float(r.anomaly_score),
                    "flag": r.flag,
                    "score_reliability": r.score_reliability,
                    "why": r.why,
                    "max_z": float(r.max_z),
                    "coverage": float(r.coverage),
                }
                for r in group.itertuples()
            ]
            for hole_id, group in intervals.groupby("hole_id")
        },
    }
    json_path = data_root / "anomalies.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    written.append(json_path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect anomalous depth intervals and holes by comparing holes to each other"
    )
    parser.add_argument("--out", default=str(HERE / "data"), help="data root (default ./data)")
    parser.add_argument("--csv-root", help="CSV root (default <out>/csv)")
    parser.add_argument("--bin-size", type=float, default=1.0, help="depth bin in metres")
    parser.add_argument("--top", type=int, default=10, help="how many findings to print")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    data_root = Path(args.out).resolve()
    csv_root = Path(args.csv_root).resolve() if args.csv_root else data_root / "csv"

    data = load_measurements(csv_root)
    locations = load_hole_locations(csv_root)
    hole_count = data["hole_id"].nunique()
    if hole_count < 2:
        raise SystemExit(
            f"only {hole_count} hole found - cross-hole comparison needs at least 2, "
            "and is only meaningful with several"
        )

    numeric, categorical = split_column_types(data)

    print(
        f"loaded {len(data):,} samples from {hole_count} holes "
        f"({len(numeric)} numeric, {len(categorical)} categorical columns)",
        flush=True,
    )

    report_channel_agreement(data)
    report_channel_disagreements(data)
    categorical, dropped_channels = remove_redundant_classification_channels(
    data,
    categorical,
)
    print(
    f"using {len(categorical)} categorical columns after redundancy filtering "
    f"({len(dropped_channels)} removed)",
    flush=True,
)
    
    

    table, matrix, names, prescaled = build_interval_features(
        data, numeric, categorical, args.bin_size
    )
    cover = coverage_of(matrix)
    scaled, kept = robust_scale(matrix, names, prescaled)
    kept_names = [names[i] for i in kept]
    print(f"built {len(table):,} depth intervals x {scaled.shape[1]} usable features", flush=True)

    mahalanobis, residual, components = pca_scores(scaled)
    extremity = robust_norm(scaled)
    signals = [extremity, mahalanobis, residual]
    forest = isolation_scores(scaled, args.seed)
    if forest is not None:
        signals.append(forest)
    lof = lof_scores(scaled)

    score = combine(signals)

    intervals = pd.DataFrame(
        {
            "hole_id": table["hole_id"],
            "depth_from_m": table["bin_start"],
            "depth_to_m": table["bin_end"],
            "n_samples": table["n_samples"],
            "coverage": np.round(cover, 3),
            "robust_distance": np.round(extremity, 4),
            "mahalanobis": np.round(mahalanobis, 4),
            "reconstruction_error": np.round(residual, 4),
            "anomaly_score": np.round(score, 2),
        }
    )
    if forest is not None:
        intervals["isolation_score"] = np.round(forest, 4)

    if lof is not None:
        intervals["lof_score"] = np.round(lof, 4)


    max_z = np.abs(scaled).max(axis=1)
    intervals["max_z"] = np.round(max_z, 2)
    intervals["flag"] = [flag_of(s, c, z) for s, c, z in zip(score, cover, max_z)]
    intervals["score_reliability"] = [
        score_reliability_of(c, n) for c, n in zip(cover, table["n_samples"])
    ]
    intervals["why"] = [top_contributors(scaled[i], kept_names) for i in range(len(intervals))]
    intervals = intervals.sort_values(["hole_id", "depth_from_m"]).reset_index(drop=True)

    holes = score_holes(
    table.reset_index(drop=True),
    scaled,
    kept_names,
    locations,
)

    meta = {
        "holes": int(hole_count),
        "intervals": int(len(intervals)),
        "bin_size_m": args.bin_size,
        "features_used": int(scaled.shape[1]),
        "pca_components": int(components),
        "variance_target": VARIANCE_TARGET,
        "signals": ["robust_distance", "mahalanobis", "reconstruction_error"] + (["isolation_forest"] if forest is not None else []),
        "note": (
            "anomaly_score is a 0-100 percentile within this batch, not an absolute "
            "measure; flag combines that rank with max_z, an absolute test, so a "
            "uniform field yields no high flags"
        ),
    }

    written = write_outputs(data_root, intervals, holes, meta)

    print("\nmost anomalous holes")
    print(holes.head(args.top).to_string(index=False))

    flagged = intervals[intervals["flag"].isin({"high", "elevated", "watch"})]
    print(f"\nmost anomalous intervals ({len(flagged)} flagged)")
    columns = ["hole_id", "depth_from_m", "depth_to_m", "anomaly_score", "max_z", "flag", "score_reliability", "why"]
    print(
        flagged.sort_values("anomaly_score", ascending=False)
        .head(args.top)[columns]
        .to_string(index=False)
    )

    low = int((intervals["score_reliability"] == "low").sum())
    if low:
        print(f"\n{low} intervals scored at low reliability - treat those scores as provisional")

    print("\nwrote:")
    for path in written[-3:]:
        print(f"  {path}")
    print(f"  {len(written) - 3} per-hole files in {data_root / 'csv' / 'anomalies'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
