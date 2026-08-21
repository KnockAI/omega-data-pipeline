import json
import tempfile
import unittest
from pathlib import Path

from engine.acquisition.rights_queue import pending_jurisdictions, process_grant


class RightsQueueTests(unittest.TestCase):
    def test_grant_event_activates_without_architecture_change(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            registry = root / "registry.json"
            registry.write_text(json.dumps({"rows": [{"jurisdiction": "HI", "coverage_status": "DEGRADED", "rights": "UNRESOLVED"}]}))
            grant = root / "grant.json"
            grant.write_text(json.dumps({"jurisdiction": "HI", "source": "hi-open", "granting_authority": "HI GIS", "exact_dataset": "addresses", "commercial_use": True, "storage_permission": True, "transformation_permission": True, "effective_date": "2026-08-21", "evidence_sha256": "sha"}))
            self.assertEqual(pending_jurisdictions(registry), ["HI"])
            result = process_grant(registry, grant)
            self.assertTrue(result["activated"])
            self.assertEqual(pending_jurisdictions(registry), [])


if __name__ == "__main__":
    unittest.main()
