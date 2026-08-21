import time
import unittest
from engine.acquisition.omega_coverage import CoverageRegistry
from engine.acquisition.omega_graph import OmegaGraph
from engine.acquisition.omega_decision import DecisionEngine, Policy


class DecisionTests(unittest.TestCase):
    def setUp(self):
        reg = CoverageRegistry({"rows": [{"jurisdiction": "IN", "coverage_status": "COVERED_AUTHORITATIVE", "rights": "CLEAR_FOR_NON_MAILING_USE", "primary_source": "nad"}, {"jurisdiction": "MI", "coverage_status": "DEGRADED", "rights": "UNRESOLVED"}]})
        g = OmegaGraph(reg); g.add_node("a", "ADDRESS", jurisdiction="IN", source_confidence=1); g.add_node("m", "ADDRESS", jurisdiction="MI")
        g.attach_observation({"observation_id": "o", "jurisdiction": "IN", "tenant_id": "t1", "observed_at": time.time(), "verified": True, "evidence_strength": 1, "outcome": "ok"}, address_id="a")
        self.e = DecisionEngine(g)

    def test_decision_replay_and_required_trace(self):
        p = Policy("p1", "political", threshold=.1)
        a = self.e.decide("a", tenant_id="t1", policy=p, now=1); b = self.e.decide("a", tenant_id="t1", policy=p, now=1)
        self.assertEqual(a, b); self.assertEqual(a["decision_type"], "TARGET"); self.assertIn("rights_gate", a["rule_trace"])

    def test_tenant_contamination_and_rights_block(self):
        with self.assertRaises(PermissionError): self.e.decide("a", tenant_id="t2", policy=Policy("p", "utility"))
        self.assertEqual(self.e.decide("m", tenant_id="t1", policy=Policy("p", "utility"), now=1)["decision_type"], "BLOCK_RIGHTS")

    def test_policy_upgrade_is_versioned(self):
        one = self.e.decide("a", tenant_id="t1", policy=Policy("p1", "inspection", threshold=.1), now=1)
        two = self.e.decide("a", tenant_id="t1", policy=Policy("p2", "inspection", threshold=.99), now=1)
        self.assertNotEqual(one["policy_version"], two["policy_version"]); self.assertEqual(one["timestamp"], two["timestamp"])


if __name__ == "__main__": unittest.main()
