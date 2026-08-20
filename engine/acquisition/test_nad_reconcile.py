import json
import tempfile
import unittest
from pathlib import Path

from .nad_reconcile import STATES, build_matrix, write_matrix


class NADReconcileTest(unittest.TestCase):
    def test_no_unknown_states_when_artifact_is_pending(self):
        rows = build_matrix(None)
        self.assertEqual(len(rows), 51)
        self.assertTrue(all(row["refresh_status"] == "DEGRADED_PENDING_ACQUISITION" for row in rows))
        self.assertTrue(all(row["source_precedence"] == "NAD_R23" for row in rows))

    def test_manifest_counts_flow_into_matrix(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "manifest.json"
            output = Path(temp) / "matrix.json"
            manifest.write_text(json.dumps({"compiled": "2026-06-30", "records_by_state": {"UT": 2}}))
            result = write_matrix(manifest, output)
            self.assertEqual(result["acquired_states"], 1)
            data = json.loads(output.read_text())
            self.assertEqual(next(row for row in data["matrix"] if row["state"] == "UT")["nad_records"], 2)


if __name__ == "__main__":
    unittest.main()
