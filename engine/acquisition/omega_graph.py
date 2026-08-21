"""Evidence-backed OMEGA physical-world graph and deterministic signals."""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .omega_coverage import ACTIVE, PENDING, CoverageRegistry


RELATIONSHIP_TYPES = {
    "ADDRESS_PROPERTY_RELATIONSHIP",
    "ADDRESS_STRUCTURE_RELATIONSHIP",
    "PROPERTY_STRUCTURE_RELATIONSHIP",
    "ENTITY_ZONE_MEMBERSHIP",
    "OBSERVATION_LOCATION_RELATIONSHIP",
}


@dataclass(frozen=True)
class Edge:
    edge_id: str
    edge_type: str
    src: str
    dst: str
    method: str
    evidence: tuple[str, ...]
    rule_version: str
    confidence: float
    provenance: dict[str, Any]
    source: str | None
    release: str | None
    version: int
    active: bool = True


class GraphRightsDenied(PermissionError):
    pass


class OmegaGraph:
    def __init__(self, registry: CoverageRegistry, *, rule_version: str = "graph-v1"):
        self.registry = registry
        self.rule_version = rule_version
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[Edge] = []
        self.observations: dict[str, dict[str, Any]] = {}

    def add_node(self, node_id: str, node_type: str, **attrs: Any) -> None:
        self.nodes[node_id] = {"id": node_id, "type": node_type, **attrs}

    def add_edge(self, edge_type: str, src: str, dst: str, *, method: str,
                 evidence: list[str], confidence: float,
                 provenance: dict[str, Any], source: str | None = None,
                 release: str | None = None, version: int | None = None) -> Edge:
        if edge_type not in RELATIONSHIP_TYPES:
            raise ValueError(f"unsupported relationship type: {edge_type}")
        if not (0 <= confidence <= 1):
            raise ValueError("confidence must be in [0,1]")
        if edge_type == "ADDRESS_STRUCTURE_RELATIONSHIP" and method == "nearest_structure":
            raise ValueError("nearest structure is diagnostic only")
        prior = [e for e in self.edges if e.src == src and e.edge_type == edge_type]
        edge_version = version or (max((e.version for e in prior), default=0) + 1)
        for e in prior:
            if e.active:
                self.edges[self.edges.index(e)] = Edge(**{**e.__dict__, "active": False})
        edge_id = hashlib.sha256(f"{edge_type}:{src}:{dst}:{edge_version}".encode()).hexdigest()[:20]
        edge = Edge(edge_id, edge_type, src, dst, method, tuple(evidence), self.rule_version,
                    confidence, dict(provenance), source, release, edge_version)
        self.edges.append(edge)
        return edge

    def attach_observation(self, observation: dict[str, Any], *, address_id: str,
                           zone_id: str | None = None, property_id: str | None = None,
                           structure_id: str | None = None) -> str:
        jurisdiction = str(observation.get("jurisdiction", "")).upper()
        if self.registry.decide(jurisdiction).status != ACTIVE:
            raise GraphRightsDenied(f"{jurisdiction}: {PENDING}")
        oid = str(observation["observation_id"])
        self.observations[oid] = {**observation, "address_id": address_id,
                                  "zone_id": zone_id, "property_id": property_id,
                                  "structure_id": structure_id,
                                  "raw_evidence": observation.get("raw_evidence", {})}
        self.add_edge("OBSERVATION_LOCATION_RELATIONSHIP", oid, address_id,
                      method="observation_address_id", evidence=[oid], confidence=1.0,
                      provenance=observation.get("provenance", {}), source=observation.get("source"),
                      release=observation.get("release"))
        return oid

    def edges_for(self, node_id: str, edge_type: str | None = None) -> list[Edge]:
        return [e for e in self.edges if e.active and (e.src == node_id or e.dst == node_id)
                and (edge_type is None or e.edge_type == edge_type)]

    def query(self, kind: str, node_id: str | None = None, *, zone_id: str | None = None,
              jurisdiction: str | None = None) -> list[Any]:
        if jurisdiction and self.registry.decide(jurisdiction).status != ACTIVE:
            raise GraphRightsDenied(f"{jurisdiction}: {PENDING}")
        if kind == "observations_at_property":
            return [o for o in self.observations.values() if o.get("property_id") == node_id]
        if kind == "addresses_for_property":
            ids = {e.src for e in self.edges_for(node_id, "ADDRESS_PROPERTY_RELATIONSHIP")}
            return [self.nodes[i] for i in ids if i in self.nodes]
        if kind == "structures_for_address":
            ids = {e.dst for e in self.edges_for(node_id, "ADDRESS_STRUCTURE_RELATIONSHIP") if e.src == node_id}
            return [self.nodes[i] for i in ids if i in self.nodes]
        if kind == "properties_in_zone":
            ids = {e.src for e in self.edges if e.active and e.edge_type == "ENTITY_ZONE_MEMBERSHIP" and e.dst == zone_id}
            return [self.nodes[i] for i in ids if i in self.nodes]
        if kind == "contradictory_observations":
            grouped: dict[str, list[dict[str, Any]]] = {}
            for o in self.observations.values(): grouped.setdefault(o["address_id"], []).append(o)
            return [v for v in grouped.values() if len({x.get("outcome") for x in v}) > 1]
        if kind == "unobserved_addresses":
            observed = {o["address_id"] for o in self.observations.values()}
            return [n for n in self.nodes.values() if n["type"] == "ADDRESS" and n["id"] not in observed]
        if kind == "target_locations":
            return [n for n in self.nodes.values() if n["type"] == "ADDRESS" and (not zone_id or n.get("zone_id") == zone_id)]
        raise ValueError(f"unknown graph query: {kind}")

    def signals(self, address_id: str, *, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        obs = [o for o in self.observations.values() if o.get("address_id") == address_id]
        outcomes = [o.get("outcome") for o in obs]
        latest = max((o.get("observed_at", 0) for o in obs), default=None)
        contradictory = len(set(outcomes)) > 1 if outcomes else False
        edges = self.edges_for(address_id)
        return {"observation_count": len(obs), "latest_observation_at": latest,
                "verification_count": sum(bool(o.get("verified")) for o in obs),
                "evidence_strength": sum(float(o.get("evidence_strength", 0)) for o in obs),
                "contradictory_observation_count": int(contradictory),
                "recency": (max(0.0, 1 - (now - latest) / 31557600) if latest else 0.0),
                "coverage_gap": not bool(obs), "repeated_visit_count": max(0, len(obs) - 1),
                "relationship_confidence": min((e.confidence for e in edges), default=0.0),
                "zone_observation_density": len(obs), "execution_completion": sum(bool(o.get("completed")) for o in obs),
                "evidence_refs": [x.get("observation_id") for x in obs]}

    def intelligence(self, address_id: str) -> dict[str, Any]:
        node = self.nodes[address_id]
        jurisdiction = node["jurisdiction"]
        decision = self.registry.decide(jurisdiction)
        return {"location_id": address_id, "location_type": "ADDRESS", "jurisdiction": jurisdiction,
                "graph_relationships": [e.__dict__ for e in self.edges_for(address_id)],
                "signals": self.signals(address_id), "evidence_refs": self.signals(address_id)["evidence_refs"],
                "source_provenance": node.get("provenance", {}), "freshness": node.get("freshness"),
                "confidence": self.signals(address_id)["relationship_confidence"], "rights": decision.rights}
