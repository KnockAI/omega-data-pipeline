import unittest
from engine.acquisition.omega_adapt import *

class AdaptTests(unittest.TestCase):
    def setUp(self): self.e=AdaptationEngine(); self.p={"proposal_id":"p1","sample_size":20,"confidence":.9,"supporting_evidence":{"verified":True},"scope":{"tenant":"t1","policy_pack":"political"}}
    def test_evaluate_candidate_canary_promote_rollback(self):
        ev=self.e.evaluate(self.p); self.assertEqual(ev["result"],"CANARY_ELIGIBLE"); c=self.e.candidate(self.p,ev,policy_version="v2"); ca=self.e.canary(c,control={"v":"v1"},observed_candidate={"verified_rate":.9},observed_control={"verified_rate":.8},authority=AUTO_CANARY_ALLOWED); pr=self.e.promote(ca,actor="human",approval="a1"); self.assertEqual(self.e.rollback(pr,actor="human",approval="a2")["status"],"ROLLED_BACK")
    def test_adversarial_rejections(self):
        self.assertEqual(self.e.evaluate({**self.p,"rights_violation":True})["result"],"REJECT")
        c=self.e.candidate(self.p,self.e.evaluate(self.p),policy_version="v2")
        with self.assertRaises(PermissionError): self.e.canary({**c,"scope":{"expand":True}},control={},observed_candidate={},observed_control={},authority=AUTO_CANARY_ALLOWED)
        with self.assertRaises(PermissionError): self.e.promote({"result":"PASS","candidate_id":"x","scope":{}},actor=None,approval=None)
    def test_drift_and_bounded_repeat(self):
        self.assertTrue(self.e.drift({"verify":.9},{"verify":.4})["drift"])
        c=self.e.repeat(budget=Budget(iterations=2,actions=2),step=lambda i:{"i":i}); self.assertEqual(c["status"],"BOUNDED_STOP"); self.assertEqual(c["iterations"],2)
    def test_low_evidence_and_global_scope(self):
        self.assertEqual(self.e.evaluate({"proposal_id":"x","sample_size":1,"confidence":.9})["result"],"NEEDS_MORE_EVIDENCE")
        self.assertEqual(self.e.evaluate({**self.p,"blast_radius":"global"})["result"],"HUMAN_REVIEW_REQUIRED")

if __name__ == "__main__": unittest.main()
