"""Seal the prepared media package and complete the first local acceptance; step 37 imports the full version.
封存媒体准备包并完成第一次本地验收，完整入库由步骤 37 执行。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _run_reports import main_guard, report
from _db_files import read
from _media_import import prepare_package, check_package, accept_prepared_package
from _settings import StepParser
from _settings import configure, path_reference


def main():
    parser = StepParser(description=__doc__.splitlines()[0])
    parser.add_argument("--package", type=Path, help="Existing sealed package directory; supports reruns without the media work directory")
    parser.add_argument("--package-only", action="store_true", help="Compatibility flag; this step seals and verifies packages by default, then use step 37")
    parser.add_argument("--prepared-work-dir", type=Path, help="Preparation work directory for older packages; new batches share --work-dir")
    parser.add_argument("--media-work-dir", type=Path, help="Media step directory; use multi_work/media for multiple datasets")
    parser.add_argument("--evidence-dir", type=Path, help="Media evidence directory; use multi_work/evidence for multiple datasets")
    args = parser.parse_args()
    configure(args)
    if args.package:
        destination = args.package.resolve()
        manifest = read(destination / "manifest.json")
        package = check_package(destination, manifest)
    else:
        package = prepare_package(
            work=args.media_work_dir.resolve() if args.media_work_dir else None,
            evidence_dir=args.evidence_dir.resolve() if args.evidence_dir else None,
        )
    acceptance_path, acceptance = accept_prepared_package(
        package, args.prepared_work_dir.resolve() if args.prepared_work_dir else None)
    package_path = path_reference(package["directory"])
    report("28_media_package.json", {
        "status": "prepared",
        "package_object_key": package_path,
        "local_acceptance": 1,
        "acceptance_status": acceptance["status"],
        "acceptance_report": path_reference(acceptance_path),
    })
    print(f"First local acceptance passed: {package_path}; run step 37 next to import the full version.", flush=True)


if __name__ == "__main__":
    main_guard(main)
