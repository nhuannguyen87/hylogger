"""Apply this case only to a fresh processing copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import replace_once, case_main


def apply(code_dir):
    replace_once(code_dir, 'database/31_audit_multi_dataset_sources.py',
        r'''        entries = response["SpectralLogCollection"]''',
        r'''        # Select the collection key for the actual endpoint.
        collection = "logCollection" if kind == "scalar" else "SpectralLogCollection"
        entries = response[collection]
        require(isinstance(entries, list), "Official collection must be a list")''', count=1)
    replace_once(code_dir, 'database/31_audit_multi_dataset_sources.py',
        r'''import json
''',
        r'''import json
import math
''', count=1)
    replace_once(code_dir, 'database/31_audit_multi_dataset_sources.py',
        r'''    body = evidence["body"]
''',
        r'''    body = evidence["body"]
    require(hashlib.sha256(body.encode("utf-8")).hexdigest() == evidence["body_sha256"], "Membership response content changed")
''', count=1)
    replace_once(code_dir, 'database/31_audit_multi_dataset_sources.py',
        r'''def main():
''',
        r'''def profile_membership(hole, profiles, scalar_manifest, scalar_ids, sample_count):
    # Compare all rows; equal counts cannot establish membership.
    references = [e for e in scalar_manifest if e["log_id"] in scalar_ids and e["log_name"] == "prof_min"]
    require(references, "Missing dataset-specific prof_min membership evidence")
    reference_values = []
    for entry in references:
        path = raw_path(hole, entry["file"])
        require(sha(path) == entry["sha256"], "Profile reference content changed")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            values = [float(row[2]) for row in list(csv.reader(stream))[1:]]
        require(len(values) == sample_count and all(math.isfinite(x) for x in values), "Invalid profile reference axis")
        reference_values.append((entry, values))
    candidates, matches = [], []
    for entry in profiles:
        if entry.get("sample_count") != sample_count:
            continue
        path = raw_path(hole, entry["file"])
        require(sha(path) == entry["sha256"], "Profile content changed")
        minima = []
        with path.open(encoding="utf-8") as stream:
            for index, line in enumerate(stream):
                row = json.loads(line)
                require(row["sampleNo"] == index, "Profile sample index mismatch")
                channels = row["floatprofdata"]
                require(len(channels) == entry["channel_count"] and all(math.isfinite(x) for x in channels), "Invalid profile channels")
                minima.append(min((x for x in channels if x > 0), default=0.0))
        require(len(minima) == sample_count, "Profile row count mismatch")
        comparisons = []
        for reference, values in reference_values:
            differences = [abs(x - y) for x, y in zip(minima, values, strict=True)]
            comparisons.append({"scalar_log_id": reference["log_id"], "scalar_sha256": reference["sha256"], "matched_samples": sum(x <= 1e-4 for x in differences), "max_absolute_difference": max(differences)})
        matched = all(c["matched_samples"] == sample_count for c in comparisons)
        candidates.append({"profile_log_id": entry["log_id"], "profile_sha256": entry["sha256"], "matches": matched, "comparisons": comparisons})
        if matched:
            matches.append(entry["log_id"])
    require(len(matches) == 1, "Profile membership requires exactly one complete value match")
    return matches, {"basis": "all profile minima match dataset-filtered official prof_min logs", "sample_count": sample_count, "tolerance": 1e-4, "candidates": candidates, "status": "passed; independently checked again in step 32"}


def main():
''', count=1)
    replace_once(code_dir, 'database/31_audit_multi_dataset_sources.py',
        r'''            require(
                list(counts.values()).count(n) == 1,
                "Profile membership ambiguous: explicit source evidence required",
            )
            selected["profile"] = [
                e["log_id"] for e in manifests["profile"] if e["sample_count"] == n
            ]
            require(
                len(selected["profile"]) == 1,
                "Expected one uniquely sized profile; inspect new source structure",
            )
            seen["profile"].update(selected["profile"])
            evidence["profile"] = {
                "basis": "unique dataset sample count; requires full prof_min cross-check in step 32",
                "status": "pending_full_value_verification",
            }
''',
        r'''            selected["profile"], evidence["profile"] = profile_membership(
                hole, manifests["profile"], manifests["scalar"], selected["scalar"], n
            )
            require(not seen["profile"].intersection(selected["profile"]), "Profile belongs to multiple datasets")
            seen["profile"].update(selected["profile"])
''', count=1)


if __name__ == "__main__":
    case_main('11_multi_dataset_profile')
