import json
import tempfile
import unittest
from pathlib import Path

from engine.acquisition.omega_coverage import (ACTIVE, PENDING, CanonicalAddressIndex,
                                               CoverageDenied, CoverageRegistry,
                                               activate_rights_artifact,
                                               validate_rights_artifact)


def registry():
    return {"rows": [
        {"jurisdiction": "IN", "coverage_status": "COVERED_AUTHORITATIVE", "primary_source": "nad", "rights": "CLEAR_FOR_NON_MAILING_USE", "release": "R23", "freshness": 1, "proof_sha": "p"},
        {"jurisdiction": "DC", "coverage_status": "COVERED_AUTHORITATIVE", "primary_source": "nad", "rights": "CLEAR_FOR_NON_MAILING_USE", "release": "R23", "freshness": 1, "proof_sha": "p"},
        {"jurisdiction": "VI", "coverage_status": "COVERED_AUTHORITATIVE", "primary_source": "nad", "rights": "CLEAR_FOR_NON_MAILING_USE", "release": "R23", "freshness": 1, "proof_sha": "p"},
        {"jurisdiction": "MI", "coverage_status": "DEGRADED", "primary_source": "mi", "rights": "UNRESOLVED"},
    ], "coverage_gaps": ["MI"]}


class CoverageTests(unittest.TestCase):
    def test_real_registry_decision_shape(self):
        reg = CoverageRegistry(registry())
        self.assertEqual(reg.decide("IN").status, ACTIVE)
        self.assertEqual(reg.decide("MI").status, PENDING)
        self.assertEqual(reg.decide("NH").status, PENDING)

    def test_query_attaches_provenance_and_pending_fails_closed(self):
        index = CanonicalAddressIndex(CoverageRegistry(registry()))
        index.load([{"jurisdiction": "IN", "normalized_address": "1 MAIN", "source_record_id": "x"}, {"jurisdiction": "VI", "normalized_address": "2 MAIN", "source_record_id": "y"}])
        got = index.query("IN", address="1 MAIN")[0]
        self.assertEqual(got["rights_status"], "CLEAR_FOR_NON_MAILING_USE")
        self.assertEqual(got["coverage_status"], ACTIVE)
        with self.assertRaises(CoverageDenied):
            index.query("MI")
        with self.assertRaises(CoverageDenied):
            index.load([{"jurisdiction": "MI", "normalized_address": "3 MAIN"}])

    def test_rights_fixture_activation_is_one_step(self):
        artifact = {"jurisdiction": "MI", "source": "mi-open", "granting_authority": "MI", "exact_dataset": "addresses", "commercial_use": True, "storage_permission": True, "transformation_permission": True, "redistribution_restrictions": [], "attribution_requirements": "MI", "effective_date": "2026-08-21", "evidence_sha256": "fixture-sha"}
        ok, missing = validate_rights_artifact(artifact)
        self.assertTrue(ok); self.assertEqual(missing, [])
        with tempfile.TemporaryDirectory() as d:
            rp, ap = Path(d)/"registry.json", Path(d)/"rights.json"
            rp.write_text(json.dumps(registry())); ap.write_text(json.dumps(artifact))
            result = activate_rights_artifact(rp, ap)
            self.assertTrue(result["activated"])
            self.assertEqual(CoverageRegistry.from_file(rp).decide("MI").status, ACTIVE)


if __name__ == "__main__":
    unittest.main()
