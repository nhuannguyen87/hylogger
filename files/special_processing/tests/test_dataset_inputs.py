"""Check dataset isolation, failure routing, safe copying and collar boundaries."""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import quote
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import read_json, source_metadata, write_json
from dataset_inputs import (METADATA, SPLITS, copy_payload, prepare_hole,
                            process_inputs, selection_plan, source_collection, split_collar)
from diagnose import diagnose_report


class DatasetInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.cache = self.root / "cache"
        self.out = self.root / "selected"
        self.out.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def package(self, split=False, all_cuttings=False, missing_coordinates=False):
        name = "BH01" if split else "Test1"
        folder = self.raw / name
        folder.mkdir(parents=True)
        records = list(SPLITS[name].items()) if split else [("core", ("", "")), ("cuttings", ("", ""))]
        raw = {key: [] for key in METADATA}
        raw["borehole.json"] = {"nvcl_id": name, "name": name, "x": 150, "y": -20, "operator": "mixed"}
        raw["package_manifest.json"] = {"provider": "wa", "hole_id": name, "content_fingerprint": "old"}
        raw["images_manifest.json"] = {"logs": [], "files": []}
        raw["tray_depths.json"] = {}
        root = ET.Element("DatasetCollection")
        for index, (did, (project, label)) in enumerate(records):
            dname = name + ("_" + label if split else ("_cuttings" if index or all_cuttings else ""))
            coordinates = "" if missing_coordinates and index else (
                f"<Longitude>{120 + index}</Longitude><Latitude>-30</Latitude>")
            datum = "GDA94" if index == 0 else ""
            description = f"<Metadata><Project>{project}</Project>{coordinates}<Datum>{datum}</Datum></Metadata>"
            dataset = {"dataset_id": did, "dataset_name": dname, "description": description,
                       "tray_id": did + "-tray", "section_id": did + "-section", "domain_id": "",
                       "borehole_uri": "https://example.invalid/" + name}
            raw["datasets.json"].append(dataset)
            node = ET.SubElement(root, "Dataset")
            for local, remote in (("dataset_id", "DatasetID"), ("dataset_name", "DatasetName"),
                                  ("description", "description"), ("borehole_uri", "boreholeURI"),
                                  ("tray_id", "trayID"), ("section_id", "sectionID"), ("domain_id", "domainID")):
                ET.SubElement(node, remote).text = dataset[local]
            for kind, listname, manifest, xmlname, entryname, idtag in (
                ("scalar", "logs_scalar.json", "scalar_manifest.json", "Logs", "Log", "LogID"),
                ("spectral", "logs_spectral.json", "spectral_manifest.json", "SpectralLogs", "SpectralLog", "logID"),
                ("profile", "logs_profilometer.json", "profilometer_manifest.json", "ProfilometerLogs", "ProfLog", "logID"),
                ("image", "logs_image.json", None, "ImageLogs", "Log", "LogID"),
            ):
                collection = ET.SubElement(node, xmlname)
                ids = [dataset["tray_id"], dataset["section_id"]] if kind == "scalar" else [did + "-" + kind]
                for lid in ids:
                    ET.SubElement(ET.SubElement(collection, entryname), idtag).text = lid
                    metadata = {"log_id": lid, "log_name": lid}
                    raw[listname].append(metadata)
                    relative = kind + "/" + lid + ".bin"
                    payload = (did + ":" + kind).encode()
                    path = folder / relative
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(payload)
                    entry = {**metadata, "file": relative, "nbytes": len(payload),
                             "sha256": hashlib.sha256(payload).hexdigest(), "status": "complete"}
                    if manifest:
                        raw[manifest].append(entry)
                    else:
                        raw["image_logs_by_dataset.json"].append({**metadata, "dataset_id": did})
                        raw["images_manifest.json"]["logs"].append({**metadata, "dataset_id": did})
                        raw["images_manifest.json"]["files"].append({**entry, "dataset_id": did})
                        raw["tray_depths.json"][lid] = []
        for filename, value in raw.items():
            write_json(folder / filename, value)
        cache = self.cache / name
        cache.mkdir(parents=True)
        filename = quote("https://geossdi.dmp.wa.gov.au/NVCLDataServices//getDatasetCollection.html?holeidentifier=" + name, safe="") + ".txt"
        (cache / filename).write_bytes(ET.tostring(root))
        case = "13" if split else "12"
        row = {"hole": name, "metadata_sha256": source_metadata(folder), "assigned_case": case,
               "status": "routed", "unresolved": [], "cases": [case], "failure": {"status": "failed"}}
        return folder, raw, row

    def prepare(self, source, row, collars=None):
        return prepare_hole(source, self.out, self.root / "provenance", row, self.cache, collars or {})

    def test_cuttings_removed_and_core_payloads_unchanged(self):
        source, raw, row = self.package()
        before = source_metadata(source)
        result = self.prepare(source, row)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["excluded"][0]["reason"], "out_of_scope_cuttings")
        target = self.out / "Test1"
        self.assertEqual([d["dataset_id"] for d in read_json(target / "datasets.json")], ["core"])
        for path in target.rglob("*.bin"):
            self.assertIn(b"core:", path.read_bytes())
            self.assertEqual(path.read_bytes(), (source / path.relative_to(target)).read_bytes())
        self.assertEqual(source_metadata(source), before)
        self.assertTrue((source / "scalar/cuttings-tray.bin").exists())

    def test_missing_excluded_payload_does_not_block_core(self):
        source, _, row = self.package()
        (source / "scalar/cuttings-tray.bin").unlink()
        self.assertEqual(self.prepare(source, row)["status"], "prepared")

    def test_changed_retained_bytes_same_size_are_rejected(self):
        source, _, row = self.package()
        path = source / "scalar/core-tray.bin"
        path.write_bytes(b"x" * path.stat().st_size)
        result = self.prepare(source, row)
        self.assertEqual(result["status"], "failed")
        self.assertIn("content differs", result["pending_outputs"][0]["reason"])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_missing_retained_file_is_rejected(self):
        source, _, row = self.package()
        (source / "scalar/core-tray.bin").unlink()
        result = self.prepare(source, row)
        self.assertIn("Missing payload", result["pending_outputs"][0]["reason"])

    def test_cross_dataset_image_reference_is_rejected(self):
        source, raw, row = self.package()
        raw["images_manifest.json"]["files"][0]["log_id"] = "cuttings-image"
        write_json(source / "images_manifest.json", raw["images_manifest.json"])
        row["metadata_sha256"] = source_metadata(source)
        result = self.prepare(source, row)
        self.assertIn("Image association", result["pending_outputs"][0]["reason"])

    def test_missing_selected_manifest_entry_is_rejected(self):
        source, raw, row = self.package()
        write_json(source / "scalar_manifest.json", raw["scalar_manifest.json"][1:])
        row["metadata_sha256"] = source_metadata(source)
        result = self.prepare(source, row)
        self.assertIn("Incomplete", result["pending_outputs"][0]["reason"])

    def test_all_cuttings_produces_no_package(self):
        source, _, row = self.package(all_cuttings=True)
        self.assertEqual(self.prepare(source, row)["status"], "excluded")
        self.assertEqual(list(self.out.iterdir()), [])

    def test_unknown_remaining_type_requires_review(self):
        _, raw, _ = self.package()
        raw["datasets.json"][0]["dataset_name"] = "unclassified"
        with self.assertRaisesRegex(ValueError, "needs review"):
            selection_plan("Test1", raw["datasets.json"])

    def test_missing_source_coordinates_stop_only_the_affected_output(self):
        source, raw, row = self.package(split=True, missing_coordinates=True)
        result = self.prepare(source, row)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["outputs"][0]["hole"], "BH01-Behemoth")
        self.assertEqual(result["pending_outputs"][0]["hole"], "BH01-unnamed")
        b = read_json(self.out / "BH01-Behemoth/borehole.json")
        self.assertEqual((b["nvcl_id"], b["x"], b["y"]), ("BH01-Behemoth", 120, -30))
        self.assertIsNone(b["operator"])
        self.assertFalse((self.out / "BH01-unnamed").exists())

    def test_both_split_identities_are_distinct_and_dataset_ids_stay(self):
        source, raw, row = self.package(split=True)
        result = self.prepare(source, row)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual({o["hole"] for o in result["outputs"]}, {"BH01-Behemoth", "BH01-unnamed"})
        for output in result["outputs"]:
            datasets = read_json(self.out / output["hole"] / "datasets.json")
            self.assertEqual([d["dataset_id"] for d in datasets], [output["dataset_id"]])
        b1 = read_json(self.out / "BH01-Behemoth/borehole.json")
        b2 = read_json(self.out / "BH01-unnamed/borehole.json")
        self.assertEqual((b1["x"], b2["x"]), (120, 121))
        self.assertEqual(b1["source_coordinate_datum"], "GDA94")
        self.assertIsNone(b2["source_coordinate_datum"])
        for b in (b1, b2):
            self.assertIn("assumed_shared_coordinate_system", b["coordinate_basis"])
            for key in ("operator", "drillStartDate", "boreholeLength_m"):
                self.assertIsNone(b[key])

    def test_optional_override_cannot_borrow_another_sites_coordinates(self):
        _, raw, _ = self.package(split=True)
        dataset = raw["datasets.json"][0]
        did = dataset["dataset_id"]
        output = selection_plan("BH01", raw["datasets.json"])["outputs"][0]
        record = {"crs": "GDA94", "source": "Fixture", "borehole": {"x": 120, "y": -30}}
        self.assertEqual(split_collar(output, dataset, {did: record})["x"], 120)
        dataset["description"] = "<Metadata><Longitude>125</Longitude></Metadata>"
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            split_collar(output, dataset, {did: record})

    def test_invalid_numeric_coordinates_are_rejected(self):
        _, raw, _ = self.package(split=True)
        dataset = raw["datasets.json"][0]
        output = selection_plan("BH01", raw["datasets.json"])["outputs"][0]
        for longitude in ("NaN", "181", "Infinity"):
            dataset["description"] = f"<Metadata><Longitude>{longitude}</Longitude><Latitude>-30</Latitude></Metadata>"
            with self.assertRaisesRegex(ValueError, "Invalid dataset coordinate"):
                split_collar(output, dataset)

    def test_one_corrupt_dataset_does_not_cancel_the_other_output(self):
        source, raw, row = self.package(split=True)
        first = raw["datasets.json"][0]["dataset_id"]
        (source / f"scalar/{first}-tray.bin").unlink()
        result = self.prepare(source, row)
        self.assertEqual(result["status"], "partial")
        self.assertEqual([o["hole"] for o in result["outputs"]], ["BH01-unnamed"])
        self.assertFalse((self.out / "BH01-Behemoth").exists())
        self.assertTrue((self.out / "BH01-unnamed").is_dir())

    def test_modified_metadata_and_existing_output_are_rejected(self):
        source, raw, row = self.package()
        (self.out / "Test1").mkdir()
        with self.assertRaisesRegex(ValueError, "collision"):
            self.prepare(source, row)
        write_json(source / "borehole.json", {**raw["borehole.json"], "name": "changed"})
        with self.assertRaisesRegex(ValueError, "changed since"):
            self.prepare(source, row)

    def test_path_escape_is_rejected(self):
        source, _, _ = self.package()
        with self.assertRaisesRegex(ValueError, "escapes"):
            copy_payload(source, self.out, {"file": "../other.bin"})

    def test_official_membership_mismatch_is_rejected(self):
        source, raw, _ = self.package()
        raw["datasets.json"][0]["tray_id"] = "other-dataset-log"
        with self.assertRaisesRegex(ValueError, "metadata differs"):
            source_collection(self.cache, source.name, raw["datasets.json"])

    def test_failures_are_routed_to_new_handler_and_success_is_excluded(self):
        source, _, _ = self.package()
        failure = self.root / "failures.json"
        write_json(failure, {"status": "complete", "raw_dir": str(self.raw), "results": [
            {"hole": source.name, "status": "failed", "error": "Requires one dataset"},
            {"hole": "passed", "status": "passed"}]})
        report = diagnose_report(failure, self.raw, self.root / "diagnosis")
        self.assertEqual(report["groups"], {"12_core_only": [source.name]})
        result = process_inputs(self.root / "diagnosis/diagnosis.json", self.root / "processed", "12", self.cache)
        self.assertEqual(result["failed_count"], 0)
        with self.assertRaisesRegex(ValueError, "only failed"):
            process_inputs(self.root / "diagnosis/diagnosis.json", self.root / "bad", "12", self.cache, holes=["passed"])

    def test_new_input_handler_can_replace_the_missing_legacy_membership_cache(self):
        source, _, _ = self.package(split=True)
        failure = self.root / "failures.json"
        write_json(failure, {"status": "complete", "results": [
            {"hole": source.name, "status": "failed", "error": "Missing source membership evidence"}]})
        report = diagnose_report(failure, self.raw, self.root / "diagnosis")
        self.assertEqual(report["groups"], {"13_split_boreholes": [source.name]})
        result = process_inputs(self.root / "diagnosis/diagnosis.json", self.root / "processed", "13", self.cache)
        self.assertEqual(result["results"][0]["status"], "prepared")
        self.assertEqual(len(result["results"][0]["outputs"]), 2)


if __name__ == "__main__":
    unittest.main()
