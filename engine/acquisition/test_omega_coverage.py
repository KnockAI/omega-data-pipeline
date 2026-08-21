import json
import tempfile
import time
import unittest
from pathlib import Path

from engine.acquisition.omega_coverage import (ACTIVE, PENDING, CanonicalAddressIndex,
                                               CoverageDenied, CoverageRegistry,
                                               OmegaLocationQuery,
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
        self.assertEqual(reg.decide("VI").status, ACTIVE)
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

    def test_location_queries_and_knock_projection_are_deterministic(self):
        index = CanonicalAddressIndex(CoverageRegistry(registry()))
        index.load([
            {"jurisdiction": "IN", "canonical_address_id": "in-1", "source_record_id": "src-1", "normalized_address": "1 MAIN", "latitude": 40.0, "longitude": -86.0, "h3_cell": "h3-a"},
            {"jurisdiction": "DC", "canonical_address_id": "dc-1", "source_record_id": "src-2", "normalized_address": "1 MAIN", "latitude": 38.9, "longitude": -77.0, "h3_cell": "h3-b"},
            {"jurisdiction": "VI", "canonical_address_id": "vi-1", "source_record_id": "src-3", "normalized_address": "1 BAY", "latitude": 18.3, "longitude": -64.9, "h3_cell": "h3-c"},
        ])
        service = OmegaLocationQuery(index)
        self.assertEqual(service.query(jurisdictions=["IN"], h3_cell="h3-a")["count"], 1)
        self.assertEqual(service.query(bbox=(-87, 39, -85, 41))["count"], 1)
        mixed = service.query(jurisdictions=["IN", "MI"])
        self.assertEqual(mixed["count"], 1)
        self.assertEqual(mixed["unavailable_jurisdictions"], ["MI"])
        self.assertEqual(service.query(jurisdictions=["IN"], address="1 MAIN"), service.query(jurisdictions=["IN"], address="1 MAIN"))
        projected = service.knock_candidates(jurisdictions=["IN", "DC"])
        self.assertEqual(projected["candidate_count"], 2)
        self.assertNotIn("owner_name", projected["candidates"][0])

    def test_query_benchmark_budget(self):
        index = CanonicalAddressIndex(CoverageRegistry(registry()))
        index.load([{"jurisdiction": "IN", "canonical_address_id": f"in-{i:06d}", "source_record_id": str(i), "normalized_address": f"{i} MAIN", "latitude": 39.0 + (i % 100) / 1000, "longitude": -86.0, "h3_cell": f"h3-{i % 100}"} for i in range(10000)])
        service = OmegaLocationQuery(index)
        start = time.perf_counter()
        for _ in range(20):
            service.query(jurisdictions=["IN"], bbox=(-87, 39, -85, 41), h3_cell="h3-42")
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.0, f"20 indexed queries exceeded 2s: {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
