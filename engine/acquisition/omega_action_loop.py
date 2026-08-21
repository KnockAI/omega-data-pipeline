"""Deterministic decision -> action -> verify -> learn execution loop."""
from __future__ import annotations
import hashlib, json, time
from dataclasses import dataclass
from typing import Any, Callable

AUTO_ALLOWED = "AUTO_ALLOWED"; HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"; PROHIBITED = "PROHIBITED"
ACTION_TYPES = {"HUMAN_TASK", "FIELD_JOB", "NOTIFICATION", "SYSTEM_ACTION", "EXTERNAL_INTEGRATION"}
VERIFY_STATES = {"UNVERIFIED", "VERIFIED", "PARTIAL", "FAILED", "DISPUTED"}

def _id(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:24]

class ActionDenied(PermissionError): pass

@dataclass(frozen=True)
class Authority:
    mode: str
    approval_id: str | None = None
    approver: str | None = None
    approved_at: float | None = None
    scope: str | None = None

class ActionLoop:
    def __init__(self):
        self.actions: dict[str, dict[str, Any]] = {}; self.receipts: dict[str, dict[str, Any]] = {}
        self.verifications: dict[str, dict[str, Any]] = {}; self.proposals: dict[str, dict[str, Any]] = {}
        self._effects: dict[str, Any] = {}; self._lineage: dict[str, dict[str, Any]] = {}

    def authorize(self, decision: dict[str, Any], *, action_type: str, authority: Authority) -> None:
        if action_type not in ACTION_TYPES: raise ValueError("unsupported action type")
        if decision.get("decision_type") == "BLOCK_RIGHTS" or decision.get("rights") != "CLEAR_FOR_NON_MAILING_USE":
            raise ActionDenied("rights gate blocks action")
        if authority.mode == PROHIBITED: raise ActionDenied("prohibited action")
        if authority.mode == HUMAN_APPROVAL_REQUIRED:
            if not authority.approval_id or not authority.approver or not authority.approved_at or not authority.scope:
                raise ActionDenied("complete scoped approval required")
            if authority.scope != decision.get("tenant_id"): raise ActionDenied("approval scope mismatch")

    def act(self, decision: dict[str, Any], *, action_type: str, authority: Authority,
            executor: str, effect: Callable[[str], Any], idempotency_key: str,
            reversibility: str = "reversible") -> dict[str, Any]:
        self.authorize(decision, action_type=action_type, authority=authority)
        if idempotency_key in self.receipts: return self.receipts[idempotency_key]
        start = time.time(); receipt = {"receipt_id": _id([decision["decision_id"], idempotency_key]), "decision_id": decision["decision_id"], "tenant_id": decision["tenant_id"], "action_type": action_type, "executor": executor, "requested_action": decision["decision_type"], "authorization": authority.__dict__, "started_at": start, "reversibility": reversibility, "status": "FAILED", "retries": 0, "errors": []}
        try:
            if idempotency_key not in self._effects: self._effects[idempotency_key] = effect(idempotency_key)
            receipt.update({"status": "SUCCEEDED", "result": self._effects[idempotency_key]})
        except Exception as exc:
            receipt["errors"] = [str(exc)]
        receipt["ended_at"] = time.time(); receipt["provenance_hash"] = _id(receipt); self.receipts[idempotency_key] = receipt
        self.actions[receipt["receipt_id"]] = receipt; self._lineage[receipt["receipt_id"]] = {"decision": decision, "receipt": receipt}
        return receipt

    def verify(self, receipt: dict[str, Any], *, observed: dict[str, Any], independent: bool = True) -> dict[str, Any]:
        if not independent: raise ActionDenied("executor self-report cannot verify")
        expected = receipt.get("result"); status = "VERIFIED" if receipt.get("status") == "SUCCEEDED" and observed.get("result") == expected else ("FAILED" if receipt.get("status") == "FAILED" else "DISPUTED")
        item = {"verification_id": _id([receipt["receipt_id"], observed]), "receipt_id": receipt["receipt_id"], "tenant_id": receipt["tenant_id"], "state": status, "observed": observed, "evidence": observed.get("evidence", {}), "independent": independent, "verified_at": time.time()}
        self.verifications[item["verification_id"]] = item; self._lineage[receipt["receipt_id"]]["verification"] = item; return item

    def learn(self, decision: dict[str, Any], receipt: dict[str, Any], verification: dict[str, Any], *, proposed_change: dict[str, Any]) -> dict[str, Any]:
        proposal = {"proposal_id": _id([decision["decision_id"], receipt["receipt_id"], verification["verification_id"], proposed_change]), "observed_outcome": verification["state"], "supporting_evidence": verification["evidence"], "confidence": 1.0 if verification["state"] == "VERIFIED" else .25, "proposed_change": proposed_change, "expected_effect": proposed_change.get("expected_effect"), "blast_radius": proposed_change.get("blast_radius", "policy-pack"), "decision_id": decision["decision_id"], "action_id": receipt["receipt_id"], "verification_id": verification["verification_id"], "status": "PROPOSAL_ONLY"}
        self.proposals[proposal["proposal_id"]] = proposal; self._lineage[receipt["receipt_id"]]["learning"] = proposal; return proposal

    def lineage(self, receipt_id: str) -> dict[str, Any]: return self._lineage[receipt_id]
