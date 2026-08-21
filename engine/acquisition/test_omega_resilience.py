import tempfile, unittest
from engine.acquisition.omega_runtime import Runtime, WorkEnvelope
from engine.acquisition.omega_resilience import *
import time

def w(i='w'): return WorkEnvelope(i,'t','c',None,'FIELD_JOB',1,'s','p',('e',),'CLEAR_FOR_NON_MAILING_USE','AUTO_ALLOWED',time.time()+60,1,2,i,'trace')

class ResilienceTests(unittest.TestCase):
 def test_backup_restore_and_reconcile(self):
  with tempfile.NamedTemporaryFile() as a, tempfile.NamedTemporaryFile() as b:
   r=Runtime(a.name); r.enqueue(w()); x=Resilience(r); x.backup(b.name); r.cancel('w','human'); x.restore(b.name); self.assertEqual(r.get('w')['status'],'ready'); self.assertEqual(x.reconcile_side_effect('k',lambda k:'ext-1').state,'KNOWN_SUCCEEDED'); self.assertEqual(x.reconcile_side_effect('z',lambda k:None,risk='irreversible').state,'AMBIGUOUS_REQUIRES_HUMAN')
 def test_circuit_breaker_and_degraded_health(self):
  x=Resilience(Runtime());
  for _ in range(4):
   with self.assertRaises(RuntimeError): x.call_dependency('api',lambda: (_ for _ in ()).throw(RuntimeError('503')))
  self.assertEqual(x.dependency('api').state,OPEN); self.assertFalse(x.health()['readiness'])
  x.dependency('api').probe(); x.dependency('api').success(); self.assertEqual(x.dependency('api').state,RECOVERED)
 def test_failure_injection_and_structured_events(self):
  x=Resilience(Runtime()); x.injector.inject('before_receipt');
  with self.assertRaises(InjectedFailure): x.injector.hit('before_receipt')
  e=x.event('w','LEASE_EXPIRED',trace_id='t',failure_class='TRANSIENT'); self.assertEqual(e['work_id'],'w'); self.assertEqual(e['failure_class'],'TRANSIENT')
 def test_restore_preserves_cancellation_and_queue_state(self):
  with tempfile.NamedTemporaryFile() as a, tempfile.NamedTemporaryFile() as b:
   r=Runtime(a.name); r.enqueue(w('x')); r.cancel('x','revoked'); Resilience(r).backup(b.name); r2=Runtime(); Resilience(r2).restore(b.name); self.assertEqual(r2.get('x')['status'],'cancelled')
 def test_dependency_recovery_and_missing_verification(self):
  x=Resilience(Runtime()); self.assertEqual(x.health()['liveness'],True); self.assertEqual(x.reconcile_side_effect('a',lambda k:None).action,'retry_idempotently')

if __name__=='__main__': unittest.main()
