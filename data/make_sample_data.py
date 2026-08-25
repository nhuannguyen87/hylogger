"""
Make sample data so the website works before your real GSWA data is ready.

Run:  python data/make_sample_data.py

Writes:
    data/holes.csv
    data/measurements.csv

WHEN YOUR REAL DATA IS READY
----------------------------
Delete these two CSVs and drop in your own with the SAME column names.
Nothing else in the project needs to change. The columns are:

holes.csv
    hole_id, hole_name, latitude, longitude, easting, northing,
    elevation_m, borehole_length_m, inclination_deg, azimuth_deg

measurements.csv
    hole_id, sample_no, depth_from_m, depth_to_m,
    mineral_1, mineral_1_pct, mineral_2, mineral_2_pct,
    confidence, quality_flag, and ANY number of band_* columns.

    Every column starting with "band_" is treated as a numeric feature and
    fed to the anomaly model. Add or remove bands freely - the model adapts.
"""

import csv
import math
import random
from pathlib import Path

random.seed(7)  # same fake data every run - change this for a different set

HERE = Path(__file__).resolve().parent

N_HOLES = 60
BANDS = [500, 900, 1000, 1400, 1900, 2200, 2250, 2350]  # nm, fake but plausible

# Mineral -> rough band "fingerprint". Purely illustrative.
MINERAL_PROFILES = {
    "Quartz":     {2200: 0.15, 2250: 0.10, 1400: 0.05, 1900: 0.05},
    "Kaolinite":  {2200: 0.75, 1400: 0.60, 1900: 0.35},
    "Muscovite":  {2200: 0.65, 2350: 0.40, 1400: 0.30},
    "Chlorite":   {2250: 0.70, 2350: 0.65, 900: 0.30},
    "Hematite":   {500: 0.80, 900: 0.70, 1000: 0.55},
    "Carbonate":  {2350: 0.80, 2250: 0.25},
}
MINERALS = list(MINERAL_PROFILES)

# Rough WA bounding box (Yilgarn-ish spread)
LAT_RANGE = (-32.8, -25.5)
LON_RANGE = (116.0, 122.5)


def latlon_to_mga(lat, lon):
    """Very rough easting/northing so the columns aren't empty. Not survey grade."""
    lon0 = 117.0
    easting = 500000 + (lon - lon0) * 111320 * math.cos(math.radians(lat))
    northing = 10000000 + lat * 110540
    return round(easting, 1), round(northing, 1)


def make_holes():
    rows = []
    for i in range(1, N_HOLES + 1):
        lat = round(random.uniform(*LAT_RANGE), 6)
        lon = round(random.uniform(*LON_RANGE), 6)
        easting, northing = latlon_to_mga(lat, lon)
        # most holes near-vertical, a few angled
        inclination = -90.0 if random.random() < 0.6 else round(random.uniform(-85, -55), 1)
        rows.append({
            "hole_id": f"H{i:03d}",
            "hole_name": f"{random.choice(['Bindi','Karlkurla','Menzies','Yalgoo','Norseman','Laverton'])} {i:03d}",
            "latitude": lat,
            "longitude": lon,
            "easting": easting,
            "northing": northing,
            "elevation_m": round(random.uniform(180, 520), 1),
            "borehole_length_m": round(random.uniform(60, 260), 1),
            "inclination_deg": inclination,
            "azimuth_deg": round(random.uniform(0, 359), 1),
        })
    return rows


def band_values(mineral, noise=0.05, weird=False):
    """Turn a mineral into fake band readings."""
    profile = MINERAL_PROFILES[mineral]
    values = {}
    for band in BANDS:
        base = profile.get(band, 0.08)
        value = base + random.gauss(0, noise)
        if weird:
            # an odd interval: one band spikes, another collapses
            value += random.choice([0.0, 0.0, 0.45, -0.35])
        values[f"band_{band}"] = round(max(0.0, min(1.5, value)), 4)
    return values


def make_measurements(holes):
    rows = []
    for hole in holes:
        length = hole["borehole_length_m"]
        # each hole has 2-4 geological "zones" so the strip log looks layered
        n_zones = random.randint(2, 4)
        edges = sorted(random.sample(range(5, int(length) - 5), n_zones - 1))
        edges = [0] + edges + [int(length)]
        zone_minerals = [random.choice(MINERALS) for _ in range(n_zones)]

        sample_no = 0
        for zone_index in range(n_zones):
            top, bottom = edges[zone_index], edges[zone_index + 1]
            mineral = zone_minerals[zone_index]
            second = random.choice([m for m in MINERALS if m != mineral])
            for depth in range(top, bottom):
                sample_no += 1

                # 6% of intervals are bad data, 2% are genuinely odd readings
                roll = random.random()
                quality = "ok"
                weird = False
                if roll < 0.06:
                    quality = random.choice(["low_signal", "missing", "low_signal"])
                elif roll < 0.08:
                    weird = True

                if quality == "missing":
                    bands = {f"band_{b}": "" for b in BANDS}
                    m1, p1, m2, p2 = "", "", "", ""
                    confidence = 0.0
                else:
                    bands = band_values(mineral, weird=weird)
                    p1 = round(random.uniform(0.45, 0.9), 3)
                    p2 = round(min(1 - p1, random.uniform(0.05, 0.4)), 3)
                    m1, m2 = mineral, second
                    confidence = round(random.uniform(0.75, 0.99), 3)
                    if quality == "low_signal":
                        confidence = round(random.uniform(0.10, 0.45), 3)

                row = {
                    "hole_id": hole["hole_id"],
                    "sample_no": sample_no,
                    "depth_from_m": depth,
                    "depth_to_m": depth + 1,
                    "mineral_1": m1,
                    "mineral_1_pct": p1,
                    "mineral_2": m2,
                    "mineral_2_pct": p2,
                    "confidence": confidence,
                    "quality_flag": quality,
                }
                row.update(bands)
                rows.append(row)
    return rows


def write_csv(path, rows):
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows):>6} rows -> {path}")


if __name__ == "__main__":
    holes = make_holes()
    measurements = make_measurements(holes)
    write_csv(HERE / "holes.csv", holes)
    write_csv(HERE / "measurements.csv", measurements)
