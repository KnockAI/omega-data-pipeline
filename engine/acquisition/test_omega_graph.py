import time
import unittest
from engine.acquisition.omega_coverage import CoverageRegistry
from engine.acquisition.omega_graph import GraphRightsDenied, OmegaGraph


def reg():
    return CoverageRegistry({"rows": [
        {"jurisdiction": "IN", "coverage_status": "COVERED_AUTHORITATIVE", "rights": "CLEAR_FOR_NON_MAILING_USE", "primary_source": "nad", "release": "R23"},
        {"jurisdiction": "VI", "coverage_status": "COVERED_AUTHORITATIVE", "rights": "CLEAR_FOR_NON_MAILING_USE", "primary_source": "nad", "release": "R23"},
        {"jurisdiction": "MI", "coverage_status": "DEGRADED", "rights": "UNRESOLVED"}], "additional_territories": [{"jurisdiction": "VI", "source": "nad"}]})


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.g = OmegaGraph(reg()); self.g.add_node("a1", "ADDRESS", jurisdiction="IN", provenance={"source_id": "nad-1"}, freshness=1, zone_id="z1"); self.g.add_node("p1", "PROPERTY"); self.g.add_node("s1", "STRUCTURE"); self.g.add_node("z1", "ZONE")
        self.g.add_edge("ADDRESS_PROPERTY_RELATIONSHIP", "a1", "p1", method="authoritative_parcel_id", evidence=["parcel-1"], confidence=1, provenance={"source_id": "nad"})
        self.g.add_edge("ADDRESS_STRUCTURE_RELATIONSHIP", "a1", "s1", method="authoritative_structure_id", evidence=["structure-1"], confidence=.9, provenance={"source_id": "nad"})
        self.g.add_edge("ENTITY_ZONE_MEMBERSHIP", "p1", "z1", method="h3_membership", evidence=["h3-1"], confidence=1, provenance={"source_id": "nad"})

    def test_relationship_queries_and_signals(self):
        self.g.attach_observation({"observation_id": "o1", "jurisdiction": "IN", "source": "knock", "release": "run-1", "observed_at": time.time(), "verified": True, "completed": True, "outcome": "yes", "evidence_strength": .8, "provenance": {"event": "gps"}}, address_id="a1", property_id="p1", structure_id="s1")
        self.assertEqual(len(self.g.query("observations_at_property", "p1")), 1)
        self.assertEqual(len(self.g.query("addresses_for_property", "p1")), 1)
        self.assertEqual(len(self.g.query("structures_for_address", "a1")), 1)
        self.assertEqual(self.g.signals("a1")["verification_count"], 1)
        self.assertEqual(self.g.intelligence("a1")["rights"], "CLEAR_FOR_NON_MAILING_USE")

    def test_nearest_structure_and_pending_rights(self):
        with self.assertRaises(ValueError): self.g.add_edge("ADDRESS_STRUCTURE_RELATIONSHIP", "a1", "s2", method="nearest_structure", evidence=["distance"], confidence=.5, provenance={})
        self.g.add_node("m1", "ADDRESS", jurisdiction="MI")
        with self.assertRaises(GraphRightsDenied): self.g.attach_observation({"observation_id": "o2", "jurisdiction": "MI"}, address_id="m1")

    def test_conflict_and_versioned_update(self):
        self.g.attach_observation({"observation_id": "o1", "jurisdiction": "IN", "outcome": "yes", "observed_at": 1}, address_id="a1")
        self.g.attach_observation({"observation_id": "o2", "jurisdiction": "IN", "outcome": "no", "observed_at": 2}, address_id="a1")
        self.assertEqual(len(self.g.query("contradictory_observations")), 1)
        self.g.add_edge("ADDRESS_PROPERTY_RELATIONSHIP", "a1", "p2", method="updated_parcel", evidence=["new"], confidence=.9, provenance={})
        self.assertEqual(len([e for e in self.g.edges if e.edge_type == "ADDRESS_PROPERTY_RELATIONSHIP" and e.active]), 1)

    def test_cross_industry_observation_attachment(self):
        for i, kind in enumerate(("political", "inspection", "utility")):
            self.g.attach_observation({"observation_id": f"{kind}-{i}", "jurisdiction": "IN", "outcome": "verified", "raw_evidence": {"kind": kind}}, address_id="a1", property_id="p1")
        self.assertEqual(len(self.g.query("observations_at_property", "p1")), 3)

    def test_graph_signal_benchmark(self):
        for i in range(10000):
            aid = f"bench-{i}"; self.g.add_node(aid, "ADDRESS", jurisdiction="IN", provenance={}, freshness=1)
            self.g.attach_observation({"observation_id": f"bo-{i}", "jurisdiction": "IN", "outcome": "ok", "observed_at": 1}, address_id=aid)
        start = time.perf_counter()
        for i in range(100): self.g.signals(f"bench-{i}")
        self.assertLess(time.perf_counter() - start, 2.0)


if __name__ == "__main__": unittest.main()
