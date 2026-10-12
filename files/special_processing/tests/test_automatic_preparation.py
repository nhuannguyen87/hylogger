"""Check automatic retries preserve prior rules and stop on unresolved failures."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import write_json
from run_failed import run_prepared


class AutomaticPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        write_json(self.raw / "A/package_manifest.json", {})

    def tearDown(self):
        self.temp.cleanup()

    def failed(self):
        return {"hole": "A", "status": "failed", "steps": [], "error": "Test failure"}

    def test_success_does_not_trigger_diagnosis(self):
        with patch("run_failed.run_hole", return_value={"hole": "A", "status": "passed", "steps": []}), \
                patch("run_failed.diagnose_report") as diagnose:
            result = run_prepared(self.raw, "A", self.root / "run")
        diagnose.assert_not_called()
        self.assertEqual(result["next_step"], 7)

    def test_retry_keeps_prior_rules_when_a_later_failure_reveals_another_case(self):
        diagnoses = iter([["02"], ["06"]])
        used = []

        def diagnose(failure, raw, output):
            cases = next(diagnoses)
            result = {"holes": [{"status": "routed", "cases": cases,
                                 "assigned_case": cases[0], "unresolved": []}]}
            write_json(output / "diagnosis.json", result)
            return result

        def process(diagnosis, raw, work, group, names):
            from _support import read_json
            cases = read_json(diagnosis)["holes"][0]["cases"]
            used.append(cases)
            return {"results": [{"hole": "A", "status": "passed" if len(used) == 2 else "failed",
                                  "steps": [], "code_subdirectory": "code", "work_subdirectory": "data"}]}

        with patch("run_failed.run_hole", return_value=self.failed()), \
                patch("run_failed.diagnose_report", side_effect=diagnose), \
                patch("run_failed.process_group", side_effect=process):
            result = run_prepared(self.raw, "A", self.root / "run")
        self.assertEqual(used, [["02"], ["02", "06"]])
        self.assertEqual(result["status"], "passed")

    def test_unresolved_problem_stops_without_retry_or_timeout(self):
        diagnosis = {"holes": [{"status": "needs_review", "cases": [], "unresolved": ["Conflicting source"]}]}
        with patch("run_failed.run_hole", return_value=self.failed()), \
                patch("run_failed.diagnose_report", return_value=diagnosis), \
                patch("run_failed.process_group") as process:
            result = run_prepared(self.raw, "A", self.root / "run")
        process.assert_not_called()
        self.assertEqual(result["unresolved"], ["Conflicting source"])


if __name__ == "__main__":
    unittest.main()
