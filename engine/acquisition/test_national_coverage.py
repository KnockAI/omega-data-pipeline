import json
import tempfile
import unittest
from pathlib import Path

from .national_coverage import build_registry


class NationalCoverageTest(unittest.TestCase):
    def test_source_proof_and_coverage_gaps_are_separate(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "NAD_R23_NATIONAL_MANIFEST.json").write_text(json.dumps({
                "run_sha": "abc", "release": "R23", "source_edit": 1,
                "source_coverage": "PARTIAL_PROVEN", "additional_territories": ["VI"],
                "records_by_state": {"UT": 2, "VI": 1},
            }))
            (root / "MI_ADDRESS_COVERAGE.json").write_text(json.dumps({"jurisdiction": "MI", "rights": "HOLD_RIGHTS_OR_ACCESS"}))
            result = build_registry(root)
            self.assertTrue(result["source_ingest_proven"])
            self.assertFalse(result["us_51_coverage_complete"])
            self.assertIn("MI", result["coverage_gaps"])
            self.assertEqual(result["additional_territories"][0]["jurisdiction"], "VI")


if __name__ == "__main__":
    unittest.main()
