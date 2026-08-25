"""
The anomaly model.

It is deliberately boring: scale -> PCA -> Isolation Forest. Nothing here is a
neural network, and that is on purpose. You have no labels telling you which
intervals are "wrong", so this is unsupervised: the model learns what a typical
spectrum looks like across the whole dataset and flags the ones that don't fit.

    band values  ->  StandardScaler  ->  PCA  ->  Isolation Forest  ->  score
                                          |
                                          +--> reconstruction error (second opinion)

WHAT THE SCORE MEANS
    0.0  looks like everything else
    1.0  looks nothing like anything else

It means "statistically unusual", NOT "there is ore here". Keep that wording in
your UI and in your report - it is the difference between a defensible result
and an overclaim.

TUNING
    CONTAMINATION   roughly what fraction of rows you expect to be odd
    N_COMPONENTS    how many PCA components to keep
Both are arguments, so you can sweep them from the command line without editing code.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

CONTAMINATION = 0.03  # expect ~3% odd intervals
N_COMPONENTS = 5


@dataclass
class AnomalyModel:
    """Everything needed to score new data later. Saved to disk with joblib."""
    feature_names: list
    scaler: StandardScaler
    pca: PCA
    forest: IsolationForest
    score_min: float
    score_max: float


def build_feature_frame(records, feature_prefix="band_"):
    """
    records: list of dicts, each being one Measurement's `features` JSON.
    Returns a DataFrame with one column per band, missing values filled with the
    column median (a missing reading shouldn't count as an anomaly by itself -
    that is what the quality flag is for).
    """
    frame = pd.DataFrame(records)
    columns = [c for c in frame.columns if c.startswith(feature_prefix)]
    if not columns:
        raise ValueError(
            f"No feature columns starting with '{feature_prefix}'. "
            "Check that your CSV has band_* columns and that load_data ran."
        )
    frame = frame[sorted(columns)].apply(pd.to_numeric, errors="coerce")
    return frame.fillna(frame.median(numeric_only=True)).fillna(0.0)


def fit(frame, contamination=CONTAMINATION, n_components=N_COMPONENTS, random_state=42):
    """Train on the whole dataset and return (model, scores, reconstruction_errors)."""
    feature_names = list(frame.columns)

    scaler = StandardScaler()
    scaled = scaler.fit_transform(frame.values)

    n_components = min(n_components, scaled.shape[1])
    pca = PCA(n_components=n_components, random_state=random_state)
    components = pca.fit_transform(scaled)

    forest = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        random_state=random_state,
        n_jobs=-1,
    )
    forest.fit(components)

    # sklearn gives higher = more normal, so flip it to read the natural way
    raw = -forest.score_samples(components)
    model = AnomalyModel(
        feature_names=feature_names,
        scaler=scaler,
        pca=pca,
        forest=forest,
        score_min=float(raw.min()),
        score_max=float(raw.max()),
    )
    return model, normalise(raw, model), reconstruction_error(scaled, pca)


def score(model, frame):
    """Score new rows with an already-trained model."""
    frame = frame.reindex(columns=model.feature_names, fill_value=0.0)
    scaled = model.scaler.transform(frame.values)
    components = model.pca.transform(scaled)
    raw = -model.forest.score_samples(components)
    return normalise(raw, model), reconstruction_error(scaled, model.pca)


def normalise(raw, model):
    """Squash raw scores to 0..1 so the frontend can colour them directly."""
    spread = model.score_max - model.score_min
    if spread <= 0:
        return np.zeros_like(raw)
    return np.clip((raw - model.score_min) / spread, 0.0, 1.0)


def reconstruction_error(scaled, pca):
    """
    How badly PCA fails to rebuild each spectrum from its components.
    A spectrum PCA can't reproduce is unusual in a way the forest may miss,
    so it is a useful cross-check when you're deciding whether to trust a flag.
    """
    rebuilt = pca.inverse_transform(pca.transform(scaled))
    return np.sqrt(((scaled - rebuilt) ** 2).mean(axis=1))
