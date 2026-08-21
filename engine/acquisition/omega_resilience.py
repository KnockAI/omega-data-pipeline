"""Reference-runtime resilience controls and deterministic failure injection."""
from __future__ import annotations
import json, sqlite3, time
from dataclasses import dataclass

HEALTHY="HEALTHY"; DEGRADED="DEGRADED"; OPEN="OPEN"; PROBING="PROBING"; RECOVERED="RECOVERED"

class InjectedFailure(RuntimeError): pass

class FailureInjector:
    def __init__(self): self.rules={}
    def inject(self, point, error="injected"): self.rules[point]=error
    def clear(self, point=None): self.rules.clear() if point is None else self.rules.pop(point,None)
    def hit(self, point):
        if point in self.rules: raise InjectedFailure(self.rules[point])

class CircuitBreaker:
    def __init__(self, threshold=3): self.threshold=threshold; self.failures=0; self.state=HEALTHY
    def failure(self):
        self.failures+=1
        if self.failures>=self.threshold: self.state=OPEN
        elif self.state==HEALTHY: self.state=DEGRADED
    def probe(self):
        if self.state==OPEN: self.state=PROBING
        return self.state in (HEALTHY, DEGRADED, PROBING)
    def success(self): self.failures=0; self.state=RECOVERED if self.state==PROBING else HEALTHY

@dataclass(frozen=True)
class Reconciliation:
    key: str; state: str; external_id: str|None; action: str

class Resilience:
    def __init__(self, runtime): self.runtime=runtime; self.injector=FailureInjector(); self.breakers={}; self.events=[]
    def dependency(self, name): return self.breakers.setdefault(name,CircuitBreaker())
    def call_dependency(self, name, fn):
        breaker=self.dependency(name)
        if not breaker.probe(): raise RuntimeError("circuit open")
        try: result=fn(); breaker.success(); return result
        except Exception:
            breaker.failure(); raise
    def reconcile_side_effect(self, key, lookup, *, risk="reversible"):
        found=lookup(key)
        if found is not None: return Reconciliation(key,"KNOWN_SUCCEEDED",str(found),"do_not_repeat")
        if risk=="irreversible": return Reconciliation(key,"AMBIGUOUS_REQUIRES_HUMAN",None,"block")
        return Reconciliation(key,"NOT_FOUND_SAFE_TO_RETRY",None,"retry_idempotently")
    def backup(self, path):
        dest=sqlite3.connect(path); self.runtime.con.backup(dest); dest.close(); return path
    def restore(self, path):
        src=sqlite3.connect(path); src.backup(self.runtime.con); src.close(); self.runtime.con.commit(); return True
    def health(self):
        row=self.runtime.con.execute("select count(*) n from work where status in ('ready','retry','running')").fetchone()
        return {"liveness":True,"readiness":all(b.state not in (OPEN,) for b in self.breakers.values()),"runtime_health":"FORWARD_PROGRESS" if row[0] else "IDLE","queue_depth":row[0],"dependencies":{k:b.state for k,b in self.breakers.items()}}
    def event(self, work_id, event_type, **fields):
        item={"trace_id":fields.pop("trace_id",None),"work_id":work_id,"event_type":event_type,"timestamp":time.time(),**fields}; self.events.append(item); return item
