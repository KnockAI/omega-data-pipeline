"""Fail-safe proposal evaluation, canary, rollback, and bounded repeat."""
from __future__ import annotations
import hashlib, json, time
from dataclasses import dataclass
from typing import Any

CONSTITUTION = ("TENANT_ISOLATION", "RIGHTS_BEFORE_CAPABILITY", "HUMAN_AUTHORITY_SUPREMACY", "NO_SELF_APPROVAL", "NO_UNBOUNDED_AUTONOMY", "EVIDENCE_BEFORE_VERIFICATION", "IMMUTABLE_AUDIT_HISTORY", "FAIL_CLOSED_ON_AUTHORITY_AMBIGUITY")
AUTO_CANARY_ALLOWED="AUTO_CANARY_ALLOWED"; HUMAN_CANARY_APPROVAL_REQUIRED="HUMAN_CANARY_APPROVAL_REQUIRED"; HUMAN_PRODUCTION_APPROVAL_REQUIRED="HUMAN_PRODUCTION_APPROVAL_REQUIRED"; PROHIBITED="PROHIBITED"
DECISIONS={"REJECT","NEEDS_MORE_EVIDENCE","CANARY_ELIGIBLE","HUMAN_REVIEW_REQUIRED"}

def sid(v: Any) -> str: return hashlib.sha256(json.dumps(v, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:24]

@dataclass(frozen=True)
class Budget:
    iterations: int = 3; time_seconds: float = 60; cost: float = 10; actions: int = 10; risk: float = 1

class AdaptationEngine:
    def __init__(self):
        self.candidates={}; self.canaries={}; self.cycles={}; self.audit=[]; self.active={"default":"known-good"}; self.revoked=set()

    def evaluate(self, proposal: dict[str, Any]) -> dict[str, Any]:
        evidence = proposal.get("supporting_evidence", {}); sample = proposal.get("sample_size", evidence.get("sample_size", 0)); confidence = proposal.get("confidence", 0); risk = proposal.get("blast_radius", "policy-pack")
        if proposal.get("tenant_id") in self.revoked: result="REJECT"
        elif proposal.get("rights_violation") or proposal.get("tenant_scope_expansion"): result="REJECT"
        elif sample < 10 or confidence < .5: result="NEEDS_MORE_EVIDENCE"
        elif risk in ("global", "compliance", "financial"): result="HUMAN_REVIEW_REQUIRED"
        else: result="CANARY_ELIGIBLE"
        out={"evaluation_id":sid(proposal),"proposal_id":proposal.get("proposal_id"),"result":result,"sample_size":sample,"confidence":confidence,"risk":risk,"criteria":{"evidence_quality":bool(evidence),"rights_ok":not proposal.get("rights_violation"),"scope_ok":not proposal.get("tenant_scope_expansion")},"evaluated_at":time.time()}; self.audit.append(out); return out

    def candidate(self, proposal: dict[str, Any], evaluation: dict[str, Any], *, policy_version: str) -> dict[str, Any]:
        if evaluation["result"] not in ("CANARY_ELIGIBLE", "HUMAN_REVIEW_REQUIRED"): raise ValueError("proposal is not candidate eligible")
        c={"candidate_id":sid([proposal,evaluation,policy_version]),"proposal_id":proposal.get("proposal_id"),"policy_version":policy_version,"scope":proposal.get("scope",{}),"immutable":True,"status":"CANDIDATE","evaluation_id":evaluation["evaluation_id"]}; self.candidates[c["candidate_id"]]=c; return c

    def canary(self, candidate: dict[str, Any], *, control: dict[str, Any], observed_candidate: dict[str, Any], observed_control: dict[str, Any], authority: str) -> dict[str, Any]:
        scope=candidate.get("scope",{})
        if scope.get("expand") or authority not in (AUTO_CANARY_ALLOWED, HUMAN_CANARY_APPROVAL_REQUIRED): raise PermissionError("canary scope/authority denied")
        result={"canary_id":sid([candidate["candidate_id"],scope]),"candidate_id":candidate["candidate_id"],"control":control,"scope":scope,"candidate_observed":observed_candidate,"control_observed":observed_control,"result":"PASS" if observed_candidate.get("verified_rate",0)>=(observed_control.get("verified_rate",0)) and not observed_candidate.get("rights_violation") else "FAIL","authority":authority}; self.canaries[result["canary_id"]]=result; self.audit.append(result); return result

    def promote(self, canary: dict[str, Any], *, actor: str | None = None, approval: str | None = None) -> dict[str, Any]:
        if canary["result"]!="PASS": raise PermissionError("failed canary cannot promote")
        if not actor or not approval: raise PermissionError("explicit promotion authority required")
        key=canary["scope"].get("policy_pack","default"); prior=self.active.get(key,"known-good"); self.active[key]=canary["candidate_id"]; receipt={"promotion_id":sid([canary,actor,approval]),"candidate_id":canary["candidate_id"],"prior":prior,"actor":actor,"approval":approval,"status":"PROMOTED"}; self.audit.append(receipt); return receipt

    def rollback(self, promotion: dict[str, Any], *, actor: str, approval: str) -> dict[str, Any]:
        key="default"; self.active[key]=promotion["prior"]; receipt={"rollback_id":sid([promotion,actor,approval]),"restored":promotion["prior"],"actor":actor,"approval":approval,"status":"ROLLED_BACK"}; self.audit.append(receipt); return receipt

    def drift(self, baseline: dict[str, float], current: dict[str, float], *, threshold: float=.2) -> dict[str, Any]:
        deltas={k: current.get(k,0)-v for k,v in baseline.items()}; detected=any(abs(v)>threshold for v in deltas.values()); return {"drift":detected,"deltas":deltas,"proposal_only":True}

    def repeat(self, *, budget: Budget, step) -> dict[str, Any]:
        cycle={"cycle_id":sid([time.time(),budget.__dict__]),"iterations":0,"status":"RUNNING","lineage":[]}; start=time.time(); spent=0
        while cycle["iterations"]<budget.iterations and time.time()-start<budget.time_seconds and spent<budget.actions:
            outcome=step(cycle["iterations"]); cycle["lineage"].append(outcome); cycle["iterations"]+=1; spent+=1
            if outcome.get("stop") or outcome.get("rights_violation"): cycle["status"]="STOPPED"; break
        else: cycle["status"]="BOUNDED_STOP"
        self.cycles[cycle["cycle_id"]]=cycle; return cycle
