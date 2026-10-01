"""
Real VSWIR/TIR spectra + mineral calls read directly from raw TSG packages
(.tsg header + .bip binary spectra) via pytsg, for the hand-collected
exploration holes in data5553/ - separate from both etl4_bridge.py (5 holes,
pre-restored into Postgres from database5553/) and nvcl_bridge.py (any other
hole, fetched live from the NVCL web service). This is the only one of the
three that's a plain local file read: no database, no network, so it's
the fastest and simplest, but only covers these 15 holes.

    from holes.tsg_bridge import ALLOWED, fetch_spectral_sample

Where the data comes from: data5553/<hole>/<hole>_tsg*.{tsg,bip,cal,ini} -
zipped HyLogger exports for exploration-stage holes that never went through
the public NVCL catalog (unlike the ~2,262 holes in load_catalog_holes.py),
handed to this project directly. All 15 packages present there parse cleanly
with pytsg 0.5.0 as of 2026-09-25 - see load_tsg_holes.py for how they were
added to the main holes table (collar/depth/orientation only, from
data5553/hylogger_ver1/collars.xlsx; the spectra themselves are never
imported into Postgres, only read on demand from here).
"""

import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Every hole confirmed (2026-09-25) to have a complete, pytsg-parseable TSG
# package directly under data5553/<hole>/ - see the module docstring.
ALLOWED = (
    "KD1", "GDD005", "GD13EA0009", "GD13EA0010", "GD13EA0015",
    "MPWD3", "MPWD6", "MPWD7", "MPWD8", "MPWD9", "MPWD10", "MPWD11", "MPWD12",
    "MPWD73", "MPWD74",
)

# TSG's own scalar column names for a per-sample mineral identification -
# same "SWIR beats VNIR, rank 1 beats 2 beats 3" preference files/extract.py
# uses for the main site's summary log, kept here for the same reason: a
# consistent "the site's best single call for this depth" story.
MINERAL_COLUMNS = (
    "Min1 sTSAS", "Min2 sTSAS", "Min3 sTSAS",
    "Min1 sTSAV", "Min2 sTSAV",
)

# TSG's own classification vocabulary includes "this wasn't rock" labels
# (equipment/calibration targets that occasionally get picked up, and
# explicit non-calls) alongside real mineral names - checked against
# KD1/GD13EA0009's actual class dictionaries (2026-09-25). None of these
# belong in a "mineral calls" list any more than files/extract.py's own
# quality_flag='missing' rows do.
NOT_A_MINERAL = {
    "", "Default", "Aspectral", "NotInLibrary", "Dark", "Noisy", "HighError", "MaskedOff",
    "Vegetation-Dry", "Vegetation-Green", "IsaWhite", "IsaYellow", "PlasticChipTray",
    "Teflon", "WhiteMarker", "YellowMarker", "Wood", "GalvanisedIron", "ODZincalume",
}


@lru_cache(maxsize=len(ALLOWED))
def _read_package(hole_id):
    """The parsed TSG package for one hole, cached: parsing takes real time
    (a full pass over tens of thousands of spectra), and the files on disk
    never change while the server runs."""
    from pytsg import parse_tsg  # deferred: only needed if a request actually gets here
    folder = Path(settings.TSG_DATA_DIR) / hole_id
    return parse_tsg.read_package(str(folder))


def _class_lookup(region):
    """{column_name: {code: class_name}} for every classified scalar column,
    straight from the package's own bandheaders/classes tables (not the
    literal-index-order in nir.classes - band order and class order aren't
    guaranteed to match).

    pytsg 0.5.0 already decodes *most* rows of a classified column straight
    to their class-name string, but not all: checked against KD1, whose
    Min1/2/3 sTSAS columns are 0% decoded (every row still a raw float code)
    while GD13EA0009's are ~30% decoded and ~70% still raw - a per-hole/
    per-column inconsistency in the export, not a scalars vs bandheaders
    naming mismatch. Decoding through this table ourselves, and only
    trusting a pre-decoded string when the raw value already is one, is what
    actually gets a real name out of either hole (see tsg_bridge investigation,
    2026-09-25: manual decoding raised KD1's Min1 sTSAS hit rate from 0% to
    63.2% - pytsg's own decoded strings alone would have hidden a real result).
    """
    return {
        bh.name: region.classes[bh.class_number].classes
        for bh in region.bandheaders
        if bh.class_number is not None and bh.class_number >= 0 and bh.class_number in region.classes
    }


def _decode(value, class_dict):
    if isinstance(value, str):
        name = value.strip()
    else:
        if class_dict is None:
            return None
        try:
            name = class_dict.get(int(value))
        except (TypeError, ValueError):
            return None
    return name if name and name not in NOT_A_MINERAL else None


def _minerals_at_row(scalars, class_map, row_index):
    minerals = []
    row = scalars.iloc[row_index]
    for column in MINERAL_COLUMNS:
        if column not in scalars.columns:
            continue
        name = _decode(row[column], class_map.get(column))
        if name:
            minerals.append({"log_name": column, "region": None, "mineral": name})
    return minerals


def _spectrum_at_row(region_name, wavelength, spectra, row_index):
    values = spectra[row_index]
    return {
        "region": region_name,
        "wavelength": [float(w) for w in wavelength],
        "values": [float(v) for v in values],
        "wavelength_unit": "nm",
    }


def _pick_informative_row(scalars, class_map, sample_count):
    """No requested depth: the same problem etl4_bridge.pick_informative_sample
    solves - row 0 is often aspectral/masked, so this checks ~9 candidates
    spread across the hole and returns the first with any mineral call."""
    candidates = sorted({min(int(sample_count * frac), sample_count - 1) for frac in (.1, .2, .3, .4, .5, .6, .7, .8, .9)})
    fallback = candidates[len(candidates) // 2]
    for row_index in candidates:
        if _minerals_at_row(scalars, class_map, row_index):
            return row_index
    return fallback


def _nearest_row(scalars, depth_m):
    return (scalars["Depth (m)"] - depth_m).abs().idxmin()


def fetch_spectral_sample(hole_id, depth_m=None):
    """None if this hole has no local TSG package. Otherwise a sample's
    mineral calls and full VSWIR/TIR spectra: the one nearest depth_m if
    given, otherwise a search for one with something to show."""
    if hole_id not in ALLOWED:
        return None

    try:
        package = _read_package(hole_id)
    except Exception as exc:  # noqa: BLE001 - a bad/missing local file must not break the page
        logger.warning("tsg_bridge unavailable for %s: %s", hole_id, exc)
        return None

    nir, tir = package.nir, package.tir
    reference = nir if nir is not None else tir
    if reference is None:
        return None
    scalars = reference.scalars
    class_map = _class_lookup(reference)
    sample_count = len(scalars)

    row_index = (
        int(_nearest_row(scalars, depth_m)) if depth_m is not None
        else _pick_informative_row(scalars, class_map, sample_count)
    )

    spectra = []
    if nir is not None:
        spectra.append(_spectrum_at_row("VSWIR", nir.wavelength, nir.spectra, row_index))
    if tir is not None:
        spectra.append(_spectrum_at_row("TIR", tir.wavelength, tir.spectra, row_index))

    depths = scalars["Depth (m)"]
    return {
        "hole_id": hole_id,
        "sample_no": row_index,
        "md_m": float(depths.iloc[row_index]),
        "sample_count": sample_count,
        "depth_min_m": float(depths.min()),
        "depth_max_m": float(depths.max()),
        "minerals": _minerals_at_row(scalars, class_map, row_index),
        "spectra": spectra,
        "source": "data5553_tsg",
    }
