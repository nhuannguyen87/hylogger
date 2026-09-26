"""
Train the anomaly model on everything in the database, then write a score back
onto every measurement.

    python manage.py detect_anomalies
    python manage.py detect_anomalies --contamination 0.05 --components 8
    python manage.py detect_anomalies --threshold 0.8

Rows whose quality_flag is "missing" are excluded from training - a gap in the
data is a data-quality problem, not a geological oddity. They still get a score
of 0 so the UI has something to show.

The trained model is saved to backend/ml_models/anomaly.joblib so you can score
new holes later without retraining.
"""

import joblib
import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand

from holes.ml import anomaly
from holes.models import Measurement

UPDATE_BATCH = 2000


class Command(BaseCommand):
    help = "Fit PCA + Isolation Forest on the measurements and store anomaly scores."

    def add_arguments(self, parser):
        parser.add_argument("--contamination", type=float, default=anomaly.CONTAMINATION,
                            help="Expected fraction of odd rows (default %(default)s)")
        parser.add_argument("--components", type=int, default=anomaly.N_COMPONENTS,
                            help="Number of PCA components (default %(default)s)")
        parser.add_argument("--threshold", type=float, default=None,
                            help="Score above which a row is flagged. Default: let "
                                 "Isolation Forest decide via contamination.")

    def handle(self, *args, **options):
        rows = list(
            Measurement.objects
            .exclude(quality_flag="missing")
            .exclude(features={})
            .only("id", "features")
        )
        if not rows:
            raise SystemExit("No measurements with features. Run `load_data` first.")

        self.stdout.write(f"Training on {len(rows)} intervals...")

        frame = anomaly.build_feature_frame([row.features for row in rows])
        model, scores, recon = anomaly.fit(
            frame,
            contamination=options["contamination"],
            n_components=options["components"],
        )

        threshold = options["threshold"]
        if threshold is None:
            # flag the top `contamination` share, matching what the forest expects
            threshold = float(np.quantile(scores, 1 - options["contamination"]))

        explained = model.pca.explained_variance_ratio_.sum()
        self.stdout.write(
            f"PCA keeps {model.pca.n_components_} components "
            f"({explained:.1%} of the variation). Flag threshold: {threshold:.3f}"
        )

        for row, score, error in zip(rows, scores, recon):
            row.anomaly_score = round(float(score), 4)
            row.is_anomaly = bool(score >= threshold)
        Measurement.objects.bulk_update(
            rows, ["anomaly_score", "is_anomaly"], batch_size=UPDATE_BATCH
        )

        # rows we skipped still need a value
        Measurement.objects.filter(anomaly_score__isnull=True).update(
            anomaly_score=0.0, is_anomaly=False
        )

        path = settings.MODEL_DIR / "anomaly.joblib"
        joblib.dump(model, path)

        flagged = sum(1 for row in rows if row.is_anomaly)
        self.stdout.write(self.style.SUCCESS(
            f"Flagged {flagged} of {len(rows)} intervals ({flagged / len(rows):.1%}). "
            f"Model saved to {path}"
        ))
        self.stdout.write(
            "Remember: a flag means 'statistically unusual', not 'mineralised'."
        )
