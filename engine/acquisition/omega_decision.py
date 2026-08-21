"""Deterministic, replayable OMEGA Understand -> Identify -> Decide engine."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from .omega_coverage import ACTIVE, PENDING
from .omega_graph import GraphRightsDenied, OmegaGraph

DECISIONS = {"DO_NOTHING", "TARGET", "REVISIT", "VERIFY", "INSPECT", "ESCALATE", "BLOCK_RIGHTS"}


@dataclass(frozen=True)
class Policy:
    version: str
    industry: str
    threshold: float = 0.6
    require_verified: bool = False


def _stable_id(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:24]


class DecisionEngine:
    def __init__(self, graph: OmegaGraph):
        self.graph = graph

    def understand(self, location_id: str, *, tenant_id: str, policy: Policy, now: float | None = None) -> dict[str, Any]:
        node = self.graph.nodes[location_id]
        if node.get("tenant_id") not in (None, tenant_id):
            raise PermissionError("tenant isolation violation")
        jurisdiction = node["jurisdiction"]
        gate = self.graph.registry.decide(jurisdiction)
        if gate.status != ACTIVE:
            return {"location_id": location_id, "tenant_id": tenant_id, "jurisdiction": jurisdiction,
                    "rights": gate.rights, "coverage_status": PENDING, "facts": [], "signals": {},
                    "contradictions": [], "missing_evidence": [PENDING], "confidence_components": {}}
        all_obs = [o for o in self.graph.observations.values() if o.get("address_id") == location_id]
        if any(o.get("tenant_id", tenant_id) != tenant_id for o in all_obs):
            raise PermissionError("tenant isolation violation")
        obs = all_obs
        signals = self.graph.signals(location_id, now=now)
        confidence = {"source_confidence": float(node.get("source_confidence", 1.0)),
                      "relationship_confidence": signals["relationship_confidence"],
                      "observation_confidence": min(1.0, signals["evidence_strength"] / max(1, len(obs))),
                      "recency": signals["recency"], "completeness": min(1.0, len(obs) / 2),
                      "contradiction_penalty": min(1.0, signals["contradictory_observation_count"] * .25)}
        facts = [{"type": "observation", "id": o["observation_id"], "outcome": o.get("outcome")} for o in obs]
        return {"location_id": location_id, "tenant_id": tenant_id, "jurisdiction": jurisdiction,
                "rights": gate.rights, "coverage_status": gate.status, "facts": facts,
                "signals": signals, "contradictions": self.graph.query("contradictory_observations"),
                "missing_evidence": [] if obs else ["observation"], "confidence_components": confidence}

    def identify(self, context: dict[str, Any], *, policy: Policy) -> dict[str, Any]:
        if context["coverage_status"] != ACTIVE:
            return {"candidates": [], "blockers": [PENDING], "policy_version": policy.version}
        c = context["confidence_components"]
        score = (c["source_confidence"] + c["relationship_confidence"] + c["observation_confidence"] + c["recency"] + c["completeness"] - c["contradiction_penalty"]) / 5
        qualifying = score >= policy.threshold and (not policy.require_verified or context["signals"]["verification_count"] > 0)
        return {"candidates": [{"location_id": context["location_id"], "qualifying_facts": context["facts"], "disqualifying_facts": [] if qualifying else ["confidence_threshold"], "confidence": round(score, 8), "evidence": context["signals"]["evidence_refs"]}] if qualifying else [], "score": round(score, 8), "policy_version": policy.version, "blockers": [] if qualifying else ["confidence_threshold"]}

    def decide(self, location_id: str, *, tenant_id: str, policy: Policy, requested_action: str = "TARGET", now: float | None = None) -> dict[str, Any]:
        context = self.understand(location_id, tenant_id=tenant_id, policy=policy, now=now)
        if context["coverage_status"] != ACTIVE:
            decision_type = "BLOCK_RIGHTS"
        else:
            identified = self.identify(context, policy=policy)
            decision_type = requested_action if identified["candidates"] else ("VERIFY" if context["missing_evidence"] else "DO_NOTHING")
        payload = {"location_id": location_id, "tenant_id": tenant_id, "jurisdiction": context["jurisdiction"], "decision_type": decision_type, "policy_version": policy.version, "inputs": context, "rule_trace": ["rights_gate", "confidence_components", "policy_threshold"], "alternatives_considered": sorted(DECISIONS - {decision_type}), "blockers": context["missing_evidence"], "rights": context["rights"], "timestamp": now if now is not None else time.time()}
        payload["decision_id"] = _stable_id({k: payload[k] for k in payload if k != "timestamp"})
        return payload
