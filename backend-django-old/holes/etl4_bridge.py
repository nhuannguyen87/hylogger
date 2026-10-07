"""
Bridge to the restored ETL4 database (etl4_core, port 5434 by default) - real
per-depth VSWIR/TIR spectra and mineral calls for 5 holes, from the
database5553/ handoff package. Everything else in this app talks only to the
main `hylogger` database; this is the one place that also reaches etl4_core.

    from holes.etl4_bridge import ALLOWED, fetch_spectral_sample

Only holes in ALLOWED have this data - see views.hole_spectral_sample for how
that's turned into a 404 instead of an error for everyone else. If
database5553/ isn't restored on this machine, every call here fails with a
connection error, which the view also turns into a plain 404: the rest of the
site works normally either way.
"""

import logging
import sys
from pathlib import Path

import psycopg
from django.conf import settings
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent / "etl4"))
from _db_common import ALLOWED  # noqa: E402  (path bootstrap must run first)

try:
    from _media_reader import read_sample  # noqa: E402
except ImportError:
    # pyarrow/pillow (backend-django-old/requirements.txt) not installed - degrade to
    # "no full spectral data available" rather than breaking the whole site.
    read_sample = None


def get_connection():
    conn = psycopg.connect(settings.ETL4_READ_DSN, row_factory=dict_row, autocommit=True, connect_timeout=5)
    conn.execute("SET search_path TO core, public")
    conn.execute("SET statement_timeout TO '10s'")
    return conn


def hole_context(conn, hole_id):
    """dataset_id/axis_id/sample_count for this hole's one dataset in the fixed release, or None."""
    row = conn.execute(
        """
        SELECT rd.dataset_id, rd.dataset_revision_id, a.id AS axis_id,
               a.sample_count, a.depth_min_m, a.depth_max_m
        FROM core.release_dataset rd
        JOIN core.dataset d ON d.id = rd.dataset_id
        JOIN core.borehole b ON b.id = d.borehole_id
        JOIN core.sample_axis a ON a.dataset_revision_id = rd.dataset_revision_id
        WHERE rd.release_id = %s AND b.source_hole_id = %s
        """,
        (settings.ETL4_RELEASE_ID, hole_id),
    ).fetchall()
    return row[0] if len(row) == 1 else None


def nearest_sample_no(conn, axis_id, depth_m):
    row = conn.execute(
        "SELECT sample_no FROM core.scan_sample WHERE axis_id = %s ORDER BY abs(md_m - %s) LIMIT 1",
        (axis_id, depth_m),
    ).fetchone()
    return row["sample_no"] if row else 0


def simplify(raw):
    """The internal ETL4 schema down to what the frontend actually needs.

    metric_key=='mineral_name' is what actually marks a scalar log as a
    mineral identification, as opposed to the housekeeping scalars (Domain,
    HoleID, HyLogDiag, ...) that also happen to carry text values.
    """
    minerals, spectra = [], []
    for result in raw["results"]:
        if result["status"] != "available":
            continue
        if (result["log_kind"] == "scalar" and result["metric_key"] == "mineral_name"
                and isinstance(result["value"], dict) and result["value"].get("value_text")):
            minerals.append({
                "log_name": result["source_log_name"],
                "region": result["output_region"],
                "mineral": result["value"]["value_text"],
            })
        elif result["log_kind"] == "spectral":
            spectra.append({
                "region": result["region_code"],
                "wavelength": result["wavelength"],
                "values": result["spectra"],
                "wavelength_unit": result["wavelength_unit"],
            })
    return minerals, spectra


def read_and_simplify(conn, dataset_id, axis_id, sample_no):
    raw = read_sample(conn, settings.ETL4_RELEASE_ID, str(dataset_id), str(axis_id), sample_no)
    minerals, spectra = simplify(raw)
    return raw, minerals, spectra


def pick_informative_sample(conn, dataset_id, axis_id, sample_count):
    """
    No requested depth: find a sample that actually has a mineral call to show,
    rather than gambling on sample 0 or the exact midpoint. Real per-depth
    readings are often a non-fit - source_null, same "not every depth has a
    confident answer" reality the main site already models via quality_flag -
    so a single guess is unreliable (sample 0 of 07THD002 has none at all;
    neither does its exact midpoint). Checks ~9 candidates spread across the
    hole and returns the first with any available mineral_name result, or the
    middle one's honestly-empty result if none of them do.
    """
    candidates = sorted({min(int(sample_count * frac), sample_count - 1) for frac in (.1, .2, .3, .4, .5, .6, .7, .8, .9)})
    fallback_no = candidates[len(candidates) // 2]
    fallback = None
    for sample_no in candidates:
        raw, minerals, spectra = read_and_simplify(conn, dataset_id, axis_id, sample_no)
        if sample_no == fallback_no:
            fallback = (sample_no, raw, minerals, spectra)
        if minerals:
            return sample_no, raw, minerals, spectra
    return fallback


def fetch_spectral_sample(hole_id, depth_m=None):
    """
    None if this hole has no full spectral data. Otherwise a sample's mineral
    calls and full spectra: the one nearest depth_m if given, otherwise a
    search for one with something to show (see pick_informative_sample).
    """
    if hole_id not in ALLOWED or read_sample is None:
        return None

    try:
        with get_connection() as conn:
            context = hole_context(conn, hole_id)
            if context is None:
                return None

            if depth_m is not None:
                sample_no = nearest_sample_no(conn, context["axis_id"], depth_m)
                raw, minerals, spectra = read_and_simplify(conn, context["dataset_id"], context["axis_id"], sample_no)
            else:
                sample_no, raw, minerals, spectra = pick_informative_sample(
                    conn, context["dataset_id"], context["axis_id"], context["sample_count"],
                )

            return {
                "hole_id": hole_id,
                "sample_no": sample_no,
                "md_m": raw["md_m"],
                "sample_count": context["sample_count"],
                "depth_min_m": context["depth_min_m"],
                "depth_max_m": context["depth_max_m"],
                "minerals": minerals,
                "spectra": spectra,
                "source": "database5553",
            }
    except (psycopg.Error, OSError) as exc:
        # etl4_core isn't restored/reachable on this machine - the rest of the
        # site doesn't depend on it, so this is a 404, not a 500.
        logger.warning("etl4_bridge unavailable for %s: %s", hole_id, exc)
        return None
