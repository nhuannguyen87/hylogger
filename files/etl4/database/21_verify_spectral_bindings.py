"""Check spectral order and sample numbers against cached official sample records.
用缓存的官方样本记录核对光谱排列和样本号。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from _run_reports import main_guard, now
from _db_files import read, sha, write
from _media_common import require, save_stage, source_folder, source_path, stage, prepare_contract_evidence
from _settings import StepParser
from _settings import settings, configure

URL = "https://geossdi.dmp.wa.gov.au/NVCLDataServices/getspectraldata.html"
COMMIT = "f67726970e492583febf4343b0e886f981bc0a27"


def verify_one(a, fetch, evidence_dir=None):
    evidence_dir = settings.media_evidence_dir if evidence_dir is None else evidence_dir
    if not a["file"]:
        return {
            **a,
            "verification_status": "metadata_only",
            "per_sample_publication_allowed": False,
        }
    u = a
    n = a["sample_count"]
    c = a["band_count"]
    sections = [
        s
        for s in read(source_folder(u) / "4_core_intervals.json")
        if s["interval_kind"] == "section"
    ]
    edge = sections[0]["sample_no_to"]
    starts = sorted({0, edge, max(0, n // 2 - 1), max(0, n - 2), max(0, min(4095, n - 2))})
    proofs = []
    matrix = np.memmap(source_path(u, a["file"]), dtype="<f4", mode="r", shape=(n, c))
    for start in starts:
        end = min(start + 1, n - 1)
        p = evidence_dir / "spectra" / a["dataset_id"] / a["log_id"] / f"{start}_{end}.json"
        params = {
            "speclogid": a["log_id"],
            "startsampleno": start,
            "endsampleno": end,
            "outputformat": "json",
        }
        if not p.exists():
            require(fetch, f"Missing reference {p.name}; run step 21 --fetch-reference once")
            with httpx.Client(timeout=25, follow_redirects=True) as client:
                response = client.get(URL, params=params)
                response.raise_for_status()
            value = response.json()
            require(isinstance(value, list), "Unexpected official spectral response")
            write(
                p,
                {
                    "url": str(response.url),
                    "retrieved_at": now(),
                    "source_sha256": a["sha256"],
                    "parameters": params,
                    "response": value,
                },
            )
        evidence = read(p)
        require(
            evidence["source_sha256"] == a["sha256"] and evidence["parameters"] == params,
            "Reference identity mismatch",
        )
        rows = evidence["response"]
        require(
            [r["sampleNo"] for r in rows] == list(range(start, end + 1)),
            "Official sample numbers differ",
        )
        for row in rows:
            actual = np.asarray(row["floatspectraldata"], dtype="<f4")
            require(actual.shape == (c,), "Reference channels differ")
            # 把 JSON 小数还原为 float32 后逐位比较，保留零和非有限值。 / Restore JSON decimals to float32 for bitwise comparison, preserving zeros and nonfinite values.
            require(
                actual.tobytes() == matrix[row["sampleNo"]].tobytes(),
                "Official sample differs from local spectrum",
            )
        proofs.append(
            {
                "path": p.relative_to(settings.database_dir).as_posix(),
                "sha256": sha(p),
                "sample_no_from": start,
                "sample_no_to": end,
            }
        )
    del matrix
    require(
        n == read(source_folder(u) / "4_alignment_report.json")["sample_count"],
        "Axis length mismatch",
    )
    return {
        **a,
        "layout_status": "verified_sample_major",
        "binding_status": "verified",
        "verification_status": "passed",
        "per_sample_publication_allowed": True,
        "binding_basis": "official ordered sample-number service contract + source complete-file manifest + explicit sampleNo reference comparisons; no depth interpolation",
        "matrix_order": "C",
        "dtype_code": "<f4",
        "shape": [n, c],
        "references": proofs,
        "contract_commit": COMMIT,
        "verification_scope": "All local bytes checked by source hash; selected first/boundary/middle/last spectra independently compared across all channels, not an exhaustive redownload",
    }


def main():
    p = StepParser()
    p.add_argument("--fetch-reference", action="store_true")
    args = p.parse_args()
    configure(args)
    prepare_contract_evidence(settings.media_evidence_dir)
    with ThreadPoolExecutor(max_workers=4) as pool:
        verified = list(
            pool.map(lambda a: verify_one(a, args.fetch_reference), stage(20)["spectra"])
        )
    official = {
        p.name: sha(p) for p in (settings.media_evidence_dir / "official").glob("*") if p.is_file()
    }
    require(
        {"NVCLDataSvcDao.java", "SpectralDataVo.java", "getspectraldatausage.html"}
        <= official.keys(),
        "Missing official contract evidence",
    )
    save_stage(
        21,
        {
            "status": "passed",
            "spectra": verified,
            "official_evidence_sha256": official,
            "official_commit": COMMIT,
        },
    )


if __name__ == "__main__":
    main_guard(main)
