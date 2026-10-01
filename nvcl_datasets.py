#!/usr/bin/env python
"""Which NVCL dataset in a hole folder to read - one rule for every reader.

NVCL answers getDatasetCollection for a hole name with every dataset filed
under it, and download.py writes all their logs into the one
data/raw/<hole>/scalars/ folder. Sometimes that is two scans of the same hole
(EGD002: 0.5-99.5 m of chips, 98.8-333.4 m of core); sometimes it is
different holes that share a name - data/raw/BH01 holds BH01_Behemoth
(163-562 m in the Officer Basin, the hole borehole.json and the tray photos
describe) and "bh01", a 3.6 m Bunbury bore 1,250 km away. etl.py used to
keep whichever depth axis most logs shared, which for BH01 was the Bunbury
bore.

choose_dataset() is the rule etl.py and holes_3d.py share: the dataset whose
TSG header puts it at borehole.json's collar (the hole the folder is about -
etl.py's holes.csv row and holes_3d.py's collar both come from that file),
then the longest logged depth range, so a core scan beats the chips run from
the same hole. Longest alone would be wrong: BBDD0002, BH02 and MWDD002 each
share their name with a longer hole somewhere else.

It relies on download.py recording each log's dataset_id; for packages
downloaded before it did, `python download.py --backfill-datasets` adds
them from the cached NVCL response.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from common import read_json

# A dataset whose header collar is this close to borehole.json's is the same
# hole. Datum slips (AGD84 vs GDA94 is ~200 m) and rounded coordinates stay
# inside it; the closest different holes found under one name (MWDD002 at
# Mombo Bore and at Mick Well) are 1.9 km apart.
SAME_HOLE_KM = 0.5

# The download.py metadata files whose entries carry a dataset_id.
LOG_FILES = ("logs_scalar.json", "logs_spectral.json", "logs_profilometer.json", "logs_image.json")


class DatasetsNotRecorded(ValueError):
    """The folder holds several datasets, but no log says which one it came from."""


@dataclass
class DatasetChoice:
    dataset: dict  # the datasets.json entry to read ({} if the folder lists none)
    reason: str
    log_ids: set[str] | None = None  # that dataset's logs; None = every log in the folder
    skipped: list[dict] = field(default_factory=list)  # the other datasets, best first

    @property
    def name(self) -> str:
        return str(self.dataset.get("dataset_name") or self.dataset.get("dataset_id") or "")

    def owns(self, log_id: object) -> bool:
        return self.log_ids is None or str(log_id or "") in self.log_ids


def choose_dataset(folder: Path) -> DatasetChoice:
    """The dataset to read from one download.py hole folder. Raises
    DatasetsNotRecorded if there are several and the logs don't say whose they are."""
    path = folder / "datasets.json"
    datasets = read_json(path) if path.is_file() else []
    if len(datasets) <= 1:
        return DatasetChoice(datasets[0] if datasets else {}, "the only dataset")

    collar = lat_lon(*(read_json(folder / "borehole.json").get(key) for key in ("y", "x")))
    order = sorted(range(len(datasets)), key=lambda index: rank(datasets[index], collar, index))
    chosen, skipped = datasets[order[0]], [datasets[index] for index in order[1:]]

    owners = {}
    for name in LOG_FILES:
        if (folder / name).is_file():
            owners.update((str(entry.get("log_id") or ""), str(entry["dataset_id"]))
                          for entry in read_json(folder / name) if entry.get("dataset_id"))
    if not owners:
        raise DatasetsNotRecorded(
            f"{folder.name} has {len(datasets)} NVCL datasets but its logs don't say which one they "
            f"came from - run: python download.py --backfill-datasets {folder.name}"
        )
    dataset_id = str(chosen.get("dataset_id") or "")
    return DatasetChoice(
        chosen,
        f"{describe(chosen, collar)} over " + "; ".join(describe(item, collar) for item in skipped),
        {log_id for log_id, owner in owners.items() if owner == dataset_id},
        skipped,
    )


def rank(dataset: dict, collar: tuple[float, float] | None, index: int) -> tuple:
    """Sort key, best first: at the collar, then position unknown, then elsewhere
    (nearest first); within each, the longest logged depth range; then NVCL's order."""
    km = distance_km(collar, header_collar(dataset))
    place = 1 if km is None else 0 if km <= SAME_HOLE_KM else 2
    return place, km if place == 2 else 0.0, -depth_span(dataset), index


def describe(dataset: dict, collar: tuple[float, float] | None) -> str:
    """'BH01 (1,250 km from borehole.json's collar, 18.0-21.6 m)'"""
    km = distance_km(collar, header_collar(dataset))
    where = ("position unknown" if km is None
             else "at borehole.json's collar" if km <= SAME_HOLE_KM
             else f"{km:,.{1 if km < 10 else 0}f} km from borehole.json's collar")
    top, bottom = dataset.get("depth_from_m"), dataset.get("depth_to_m")
    depths = f", {top:.1f}-{bottom:.1f} m" if top is not None and bottom is not None else ""
    return f"{dataset.get('dataset_name') or dataset.get('dataset_id')} ({where}{depths})"


def header_collar(dataset: dict) -> tuple[float, float] | None:
    """(lat, lon) from the TSG header NVCL keeps in a dataset's description."""
    try:
        header = ET.fromstring(str(dataset.get("description") or ""))
    except ET.ParseError:
        return None
    return lat_lon(header.findtext("Latitude"), header.findtext("Longitude"))


def lat_lon(lat: object, lon: object) -> tuple[float, float] | None:
    """A usable (lat, lon), or None. 0 counts as missing - it's what an empty
    TSG header field holds (EGD001_chips has Latitude 0.000000)."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or not lat or not lon:
        return None
    return (lat, lon) if abs(lat) <= 90 and abs(lon) <= 180 else None


def distance_km(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float | None:
    """Great-circle distance, or None if either end is unknown."""
    if a is None or b is None:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def depth_span(dataset: dict) -> float:
    """Metres NVCL's DepthRange says the dataset covers; download.py records it."""
    try:
        return max(0.0, float(dataset["depth_to_m"]) - float(dataset["depth_from_m"]))
    except (KeyError, TypeError, ValueError):
        return 0.0
