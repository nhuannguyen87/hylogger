"""Test failure-only selection, assignment and reprocessing boundaries."""

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import failure_rows, read_json, source_metadata, write_json
from diagnose import diagnose_report
from prepare import process_group, select_group


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        for name in ("bad", "good", "multi"):
            folder = self.raw / name
            folder.mkdir(parents=True)
            write_json(folder / "datasets.json", [{"dataset_id": name}])
        self.failures = self.root / "normal_results.json"
        self.report = {"status": "complete", "raw_dir": str(self.raw), "results": [
            {"hole": "bad", "status": "failed", "failed_step": "4_align_samples.py", "error": "Source issue"},
            {"hole": "good", "status": "passed"},
            {"hole": "multi", "status": "requires_other_route"},
        ]}
        write_json(self.failures, self.report)

    def tearDown(self):
        self.temp.cleanup()

    def inspect_result(self):
        return {"hole": "bad", "dataset_ids": ["bad"], "suggested_cases": ["01", "02", "06"],
                "route": "single_dataset", "evidence": {}, "unresolved": []}

    def diagnosis(self):
        with patch("diagnose.inspect", return_value=self.inspect_result()) as inspect:
            result = diagnose_report(self.failures, self.raw, self.root / "diagnosis")
        self.assertEqual(inspect.call_count, 1)
        self.assertEqual(inspect.call_args.args[0].name, "bad")
        return result

    def test_only_failed_holes_are_diagnosed_and_assigned_once(self):
        result = self.diagnosis()
        self.assertEqual(result["excluded_nonfailed_count"], 2)
        self.assertEqual(result["groups"], {"02_tray_local_sections": ["bad"]})
        self.assertEqual(result["holes"][0]["cases"], ["01", "02", "06"])

    def test_unknown_error_stays_pending(self):
        with patch("diagnose.inspect", side_effect=ValueError("Invalid source relationship")):
            result = diagnose_report(self.failures, self.raw, self.root / "pending")
        self.assertEqual(result["groups"], {})
        self.assertEqual(result["holes"][0]["status"], "needs_review")

    def test_environment_error_does_not_enter_known_case(self):
        self.report["results"][0]["error"] = "ModuleNotFoundError: numpy"
        write_json(self.failures, self.report)
        self.assertEqual(self.diagnosis()["groups"], {})

    def test_running_batch_is_not_diagnosed(self):
        with self.assertRaisesRegex(ValueError, "Finish"):
            failure_rows({**self.report, "status": "running"})

    def test_duplicate_failure_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            failure_rows({"results": [self.report["results"][0]] * 2})

    def test_wrong_group_and_successful_hole_are_rejected(self):
        diagnosis = self.diagnosis()
        for case, names in (("01", None), ("02", ["good"]), ("02", ["bad", "bad"])):
            with self.assertRaises(ValueError):
                select_group(diagnosis, case, names)
        changed = copy.deepcopy(diagnosis)
        changed["holes"][0]["failure"]["status"] = "passed"
        with self.assertRaises(ValueError):
            select_group(changed, "02")

    def test_source_change_stops_before_processing(self):
        self.diagnosis()
        write_json(self.raw / "bad/datasets.json", [{"dataset_id": "changed"}])
        with patch("prepare.build_copy") as build:
            result = process_group(self.root / "diagnosis/diagnosis.json", self.raw, self.root / "special", "02")
        build.assert_not_called()
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["results"][0]["applied_cases"], [])
        self.assertEqual(len(read_json(self.root / "special/failed_holes.json")["results"]), 1)

    def test_empty_failure_list_creates_no_processing_group(self):
        write_json(self.failures, {**self.report, "results": self.report["results"][1:]})
        with patch("diagnose.inspect") as inspect:
            result = diagnose_report(self.failures, self.raw, self.root / "empty")
        inspect.assert_not_called()
        self.assertEqual(result["holes"], [])

    def test_repeat_failure_is_not_sent_through_same_rules_forever(self):
        self.report["results"][0]["applied_cases"] = ["01", "02", "06"]
        write_json(self.failures, self.report)
        result = self.diagnosis()
        self.assertEqual(result["groups"], {})
        self.assertIn("already failed", result["holes"][0]["unresolved"][-1])


if __name__ == "__main__":
    unittest.main()
