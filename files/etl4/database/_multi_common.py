"""Read the explicit source scope and prepared results for multiple datasets.
多 dataset 的明确来源范围和准备结果读取。
"""

import importlib.util
from _db_common import CODE, ETL
from _db_files import inside, read, sha
from _settings import settings, validate_hole_names, raw_path, resolve_reference

MULTI_HOLES = ("12CADD001", "14KDD001", "15EIS001")


PROCESSING = CODE.parent / "processing"
SERVICE = "https://geossdi.dmp.wa.gov.au/NVCLDataServices/"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def raw_metadata(hole):
    validate_hole_names([hole])
    from processing._etl_common import METADATA

    folder = raw_path(hole, "borehole.json").parent
    result = {n: read(inside(folder, folder / n)) for n in METADATA}
    require(len(result["datasets.json"]) > 1, "The multiple dataset workflow requires more than one dataset in a hole")
    return result


def audit():
    return read(settings.multi_work_dir / "31_source_audit.json")


def prepared(*, verify_files=True):
    summary = read(settings.multi_work_dir / "32_prepared.json")
    require(summary["status"] == "passed", "Dataset preparation incomplete")
    result = []
    for u in summary["datasets"]:
        folder = resolve_reference(u["prepared_directory"])
        if verify_files:
            for rel, checksum in u["prepared_hashes"].items():
                require(sha(folder / rel) == checksum, f"Prepared artifact changed: {rel}")
        result.append(
            {
                "prepared_hashes": u["prepared_hashes"],
                "summary": u,
                "folder": folder,
                "metadata": read(folder / "3_normalized_metadata.json"),
                "alignment": read(folder / "4_alignment_report.json"),
                "storage": read(folder / "5_storage_report.json"),
            }
        )
    return result
