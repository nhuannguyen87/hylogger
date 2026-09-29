"""Step 1: inventory only the five approved packages; never modify source files."""
import hashlib
from collections import Counter

from _etl_common import (
    METADATA, OUTPUT, SOURCE, baseline_for, digest, fail_main, pipeline_id,
    run_holes, sha_file, source_json, source_path, write_json, write_text,
)


def inventory_hole(hole):
    expected = baseline_for(hole)
    metadata = {name: source_json(hole, name) for name in METADATA}
    datasets = metadata["datasets.json"]
    if len(datasets) != 1:
        raise ValueError("This approved processing phase requires one dataset per input hole")
    package = metadata["package_manifest.json"]
    if package["content_fingerprint"] != expected["source_package_fingerprint"]:
        raise ValueError("Source package fingerprint differs from the reviewed baseline")
    references = {}
    for kind, name in (("scalar", "scalar_manifest.json"),
                       ("spectral", "spectral_manifest.json"),
                       ("profile", "profilometer_manifest.json"),
                       ("image", "images_manifest.json")):
        entries = metadata[name]["files"] if kind == "image" else metadata[name]
        for entry in entries:
            relative = entry.get("file")
            if not relative:
                continue
            source_path(hole, relative)
            if relative in references:
                raise ValueError(f"Payload listed more than once: {relative}")
            if not entry.get("sha256") or len(entry["sha256"]) != 64:
                raise ValueError(f"Missing payload SHA256: {relative}")
            references[relative] = {"kind": kind, "manifest": name, "entry": entry}
    files, seen = [], set()
    for path in sorted((SOURCE / hole).rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not permitted in raw packages: {path.name}")
        if not path.is_file():
            continue
        relative = path.relative_to(SOURCE / hole).as_posix()
        path = source_path(hole, relative)
        ref = references.get(relative)
        if ref is None and relative not in (*METADATA, "download-report.txt"):
            raise ValueError(f"Unlisted source file needs review: {relative}")
        stat = path.stat()
        if ref and stat.st_size != ref["entry"]["nbytes"]:
            raise ValueError(f"File size differs from manifest: {relative}")
        files.append({"path": relative, "size_bytes": stat.st_size,
                      "mtime_ns": stat.st_mtime_ns,
                      "kind": ref["kind"] if ref else "metadata",
                      "expected_sha256": ref["entry"]["sha256"] if ref else sha_file(path),
                      "manifest_reference": ref})
        seen.add(relative)
    missing = set(references) - seen
    if missing:
        raise ValueError(f"Manifest payload missing: {sorted(missing)}")
    if sum(x["size_bytes"] for x in files) != expected["source_bytes"]:
        raise ValueError("Package bytes differ from the reviewed baseline")
    source_id = digest([{k: f[k] for k in ("path", "size_bytes", "expected_sha256")}
                        for f in files])
    code_id = pipeline_id()
    folder = OUTPUT / hole / source_id[:16] / code_id[:16]
    result = {"step": 1, "hole_id": hole, "status": "passed",
              "source_snapshot_id": source_id, "pipeline_id": code_id,
              "snapshot_id_basis": "manifest payload hashes plus actual metadata hashes; payload hashes verified in step 2",
              "source_package_fingerprint": package["content_fingerprint"],
              "source_dataset_id": datasets[0]["dataset_id"],
              "file_count": len(files), "file_counts_by_kind": dict(Counter(f["kind"] for f in files)),
              "source_bytes": sum(f["size_bytes"] for f in files), "files": files}
    write_json(folder / "1_inventory.json", result)
    write_json(OUTPUT / hole / "latest_processing.json", {
        "relative_directory": folder.relative_to(OUTPUT).as_posix(),
        "source_snapshot_id": source_id, "pipeline_id": code_id,
        "purpose": "processing workspace pointer, not a production publication pointer",
    })
    write_text(folder / "1_inventory.md", f"# {hole} 输入清单\n\n"
               f"状态：通过。共 {len(files)} 个文件，{result['source_bytes']:,} 字节。\n\n"
               "本步骤核对来源范围、文件清单和大小；载荷哈希与结构由步骤 2 验证。\n")
    print(f"  {len(files)} files; {result['source_bytes']:,} bytes; {folder}", flush=True)


if __name__ == "__main__":
    fail_main(lambda: run_holes("step 1 inventory", inventory_hole))
