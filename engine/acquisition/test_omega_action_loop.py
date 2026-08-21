import unittest
from engine.acquisition.omega_action_loop import *

def decision(tenant="t1", rights="CLEAR_FOR_NON_MAILING_USE"):
    return {"decision_id":"d1", "decision_type":"TARGET", "tenant_id":tenant, "rights":rights}

class ActionLoopTests(unittest.TestCase):
    def setUp(self): self.loop=ActionLoop(); self.auth=Authority(AUTO_ALLOWED)
    def test_idempotency_failure_receipt_and_independent_verify(self):
        calls=[]
        r=self.loop.act(decision("t1"), action_type="FIELD_JOB", authority=self.auth, executor="worker", effect=lambda k: calls.append(k) or "done", idempotency_key="x")
        self.assertEqual(self.loop.act(decision("t1"), action_type="FIELD_JOB", authority=self.auth, executor="worker", effect=lambda k: calls.append(k) or "bad", idempotency_key="x"), r); self.assertEqual(len(calls),1)
        self.assertEqual(self.loop.verify(r, observed={"result":"done","evidence":{"gps":True}})["state"], "VERIFIED")
        failed=self.loop.act(decision("t1"), action_type="FIELD_JOB", authority=self.auth, executor="worker", effect=lambda k: (_ for _ in ()).throw(RuntimeError("outage")), idempotency_key="y")
        self.assertEqual(failed["status"], "FAILED")
        with self.assertRaises(ActionDenied): self.loop.verify(r, observed={"result":"done"}, independent=False)
    def test_rights_tenant_and_human_gate(self):
        with self.assertRaises(ActionDenied): self.loop.act(decision("t1","UNRESOLVED"), action_type="FIELD_JOB", authority=self.auth, executor="x", effect=lambda k:1, idempotency_key="a")
        with self.assertRaises(ActionDenied): self.loop.act(decision("t1"), action_type="FIELD_JOB", authority=Authority(HUMAN_APPROVAL_REQUIRED), executor="x", effect=lambda k:1, idempotency_key="b")
        with self.assertRaises(ActionDenied): self.loop.act(decision("t2"), action_type="FIELD_JOB", authority=Authority(HUMAN_APPROVAL_REQUIRED,"a","ceo",1,"t1"), executor="x", effect=lambda k:1, idempotency_key="c")
    def test_learning_is_proposal_and_lineage(self):
        r=self.loop.act(decision(), action_type="NOTIFICATION", authority=self.auth, executor="x", effect=lambda k:"sent", idempotency_key="z")
        v=self.loop.verify(r, observed={"result":"sent","evidence":{"delivery":"provider"}}); p=self.loop.learn(decision(),r,v,proposed_change={"expected_effect":"more verified visits"})
        self.assertEqual(p["status"],"PROPOSAL_ONLY"); self.assertEqual(self.loop.lineage(r["receipt_id"])["learning"],p)
    def test_cross_industry_action_types_and_approval(self):
        for kind in ("HUMAN_TASK","FIELD_JOB","SYSTEM_ACTION"):
            r=self.loop.act(decision(), action_type=kind, authority=self.auth, executor="x", effect=lambda k:kind, idempotency_key=kind); self.assertEqual(r["status"],"SUCCEEDED")

if __name__ == "__main__": unittest.main()
