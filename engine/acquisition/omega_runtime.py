"""Durable, fail-closed runtime orchestration primitives."""
from __future__ import annotations
import json, sqlite3, time, uuid
from dataclasses import dataclass, asdict

STATUSES = {"ready","scheduled","running","waiting-for-human","waiting-for-external-system","retry","blocked","dead-letter","completed","cancelled"}
FAILURES = {"TRANSIENT","PERMANENT","AUTHORITY_BLOCKED","RIGHTS_BLOCKED","DEPENDENCY_BLOCKED","BUDGET_EXHAUSTED","DEADLINE_EXCEEDED","INVALID_STATE","EXTERNAL_FAILURE","CONSTITUTION_VIOLATION"}

@dataclass(frozen=True)
class WorkEnvelope:
    work_id: str; tenant_id: str; cycle_id: str; parent_work_id: str|None; work_type: str; priority: int; actor: str; policy_version: str; evidence_refs: tuple[str,...]; rights: str; authority: str; deadline: float; retry_limit: int; budget: int; idempotency_key: str; trace_id: str

class RuntimeDenied(PermissionError): pass

class Runtime:
    def __init__(self, db: str = ":memory:"):
        self.db=db; self.con=sqlite3.connect(db); self.con.row_factory=sqlite3.Row; self.con.execute("create table if not exists work (id text primary key, payload text, status text, lease text, attempts integer, result text)"); self.con.execute("create unique index if not exists work_idem on work(json_extract(payload,'$.idempotency_key'))"); self.con.commit(); self.revoked=set()

    def enqueue(self, work: WorkEnvelope, *, status="ready") -> dict:
        if status not in STATUSES or work.rights != "CLEAR_FOR_NON_MAILING_USE" or work.authority in ("PROHIBITED", "AMBIGUOUS"):
            raise RuntimeDenied("runtime gate denied work")
        payload=asdict(work); payload["evidence_refs"]=list(work.evidence_refs)
        try: self.con.execute("insert into work values (?,?,?,?,?,?)", (work.work_id,json.dumps(payload),status,None,0,None)); self.con.commit()
        except sqlite3.IntegrityError: pass
        return self.get(work.work_id)

    def get(self, work_id):
        row=self.con.execute("select * from work where id=?",(work_id,)).fetchone(); return dict(row) if row else None

    def claim(self, work_id: str, worker: str, *, lease_seconds: float=30) -> dict:
        row=self.get(work_id)
        if not row or row["status"] not in {"ready","retry","scheduled"}: raise RuntimeDenied("not runnable")
        payload=json.loads(row["payload"])
        if payload["tenant_id"] in self.revoked or payload["deadline"] < time.time(): raise RuntimeDenied("revoked/expired")
        lease=f"{worker}:{uuid.uuid4()}"; cur=self.con.execute("update work set status='running',lease=?,attempts=attempts+1 where id=? and status in ('ready','retry','scheduled')",(lease,work_id)); self.con.commit()
        if cur.rowcount != 1: raise RuntimeDenied("lost race")
        return {**self.get(work_id), "lease": lease}

    def complete(self, work_id: str, lease: str, result: dict) -> dict:
        cur=self.con.execute("update work set status='completed',result=?,lease=null where id=? and lease=? and status='running'",(json.dumps(result),work_id,lease)); self.con.commit()
        if cur.rowcount != 1: raise RuntimeDenied("stale lease")
        return self.get(work_id)

    def fail(self, work_id: str, lease: str, failure: str) -> dict:
        if failure not in FAILURES: raise ValueError(failure)
        row=self.get(work_id); payload=json.loads(row["payload"]); terminal = failure in {"PERMANENT","AUTHORITY_BLOCKED","RIGHTS_BLOCKED","BUDGET_EXHAUSTED","DEADLINE_EXCEEDED","CONSTITUTION_VIOLATION"} or row["attempts"] >= payload["retry_limit"]
        status="dead-letter" if terminal else "retry"; cur=self.con.execute("update work set status=?,result=?,lease=null where id=? and lease=? and status='running'",(status,json.dumps({"failure":failure}),work_id,lease)); self.con.commit()
        if cur.rowcount != 1: raise RuntimeDenied("stale lease")
        return self.get(work_id)

    def cancel(self, work_id: str, reason: str) -> dict:
        self.con.execute("update work set status='cancelled',result=? where id=? and status != 'completed'",(json.dumps({"reason":reason}),work_id)); self.con.commit(); return self.get(work_id)

    def revoke_tenant(self, tenant: str): self.revoked.add(tenant); self.con.execute("update work set status='cancelled' where json_extract(payload,'$.tenant_id')=? and status in ('ready','scheduled','retry')",(tenant,)); self.con.commit()

    def route(self, *, capability: str, risk: str, budget: int, available: list[str]) -> dict:
        if risk in {"high","compliance"} and "human" not in available: raise RuntimeDenied("authority unavailable")
        choice = "human" if risk in {"high","compliance"} else (sorted(available)[0] if available else None)
        if not choice: raise RuntimeDenied("no capability")
        return {"capability":capability,"risk":risk,"selected":choice,"budget":budget}
