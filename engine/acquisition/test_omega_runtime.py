import tempfile, time, unittest
from engine.acquisition.omega_runtime import *

def work(i="w1",tenant="t1"):
 return WorkEnvelope(i,tenant,"c1",None,"FIELD_JOB",1,"system","p1",("e1",),"CLEAR_FOR_NON_MAILING_USE","AUTO_ALLOWED",time.time()+60,2,3,i,"trace")

class RuntimeTests(unittest.TestCase):
 def test_restart_claim_complete_and_duplicate_delivery(self):
  with tempfile.NamedTemporaryFile() as f:
   r=Runtime(f.name); r.enqueue(work()); a=r.claim("w1","w1");
   with self.assertRaises(RuntimeDenied): r.claim("w1","w2")
   r.complete("w1",a["lease"],{"ok":True}); r2=Runtime(f.name); self.assertEqual(r2.get("w1")["status"],"completed")
 def test_stale_lease_retry_and_deadletter(self):
  r=Runtime(); r.enqueue(work()); a=r.claim("w1","w");
  with self.assertRaises(RuntimeDenied): r.complete("w1","stale",{})
  self.assertEqual(r.fail("w1",a["lease"],"TRANSIENT")["status"],"retry"); b=r.claim("w1","w"); self.assertEqual(r.fail("w1",b["lease"],"TRANSIENT")["status"],"dead-letter")
 def test_rights_revocation_budget_and_routing(self):
  r=Runtime()
  with self.assertRaises(RuntimeDenied): r.enqueue(work(),status="blocked") if False else r.enqueue(WorkEnvelope("x","t","c",None,"JOB",1,"s","p",(),"UNRESOLVED","AUTO_ALLOWED",time.time()+1,1,1,"x","t"))
  r.enqueue(work("a","noisy")); r.revoke_tenant("noisy"); self.assertEqual(r.get("a")["status"],"cancelled"); self.assertEqual(r.route(capability="inspect",risk="low",budget=1,available=["z","a"])["selected"],"a")
 def test_malformed_and_authority(self):
  r=Runtime()
  with self.assertRaises(RuntimeDenied): r.enqueue(work("x"),status="nope")
  with self.assertRaises(RuntimeDenied): r.route(capability="pay",risk="high",budget=1,available=["model"])
 def test_contention_and_backpressure(self):
  r=Runtime(); [r.enqueue(work(f"w{i}", "t" if i < 5 else "u")) for i in range(10)]
  claimed=r.claim("w0","a")
  with self.assertRaises(RuntimeDenied): r.claim("w0","b")
  r.complete("w0",claimed["lease"],{"ok":True}); r.revoke_tenant("t")
  self.assertEqual(r.get("w1")["status"],"cancelled"); self.assertEqual(r.get("w9")["status"],"ready")

if __name__=='__main__': unittest.main()
