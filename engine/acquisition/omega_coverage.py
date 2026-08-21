"""Fail-closed, registry-aware OMEGA address coverage path.

This module deliberately treats the national registry as the source of truth
for jurisdiction availability.  It is usable with the proven NAD manifest and
with any future state override without changing the query contract.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PENDING = "COVERAGE_PENDING_RIGHTS"
ACTIVE = "ACTIVE"


@dataclass(frozen=True)
class CoverageDecision:
    jurisdiction: str
    status: str
    source: str | None
    rights: str | None
    release: Any = None
    freshness: Any = None
    proof_sha: str | None = None


class CoverageRegistry:
    def __init__(self, registry: dict[str, Any]):
        self.registry = registry
        self.rows = {row["jurisdiction"]: row for row in registry.get("rows", [])}

    @classmethod
    def from_file(cls, path: Path) -> "CoverageRegistry":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def decide(self, jurisdiction: str) -> CoverageDecision:
        code = jurisdiction.upper()
        row = self.rows.get(code)
        if not row or not row.get("coverage_status", "").startswith("COVERED"):
            return CoverageDecision(code, PENDING, row.get("primary_source") if row else None,
                                    row.get("rights") if row else None,
                                    row.get("release") if row else None,
                                    row.get("freshness") if row else None,
                                    row.get("proof_sha") if row else None)
        if row.get("rights") != "CLEAR_FOR_NON_MAILING_USE":
            return CoverageDecision(code, PENDING, row.get("primary_source"), row.get("rights"),
                                    row.get("release"), row.get("freshness"), row.get("proof_sha"))
        return CoverageDecision(code, ACTIVE, row.get("primary_source"), row.get("rights"),
                                row.get("release"), row.get("freshness"), row.get("proof_sha"))


class CoverageDenied(PermissionError):
    pass


class CanonicalAddressIndex:
    """Small non-production index; production promotion is intentionally absent."""

    def __init__(self, registry: CoverageRegistry):
        self.registry = registry
        self._rows: dict[str, list[dict[str, Any]]] = {}

    def load(self, records: Iterable[dict[str, Any]]) -> int:
        count = 0
        for record in records:
            jurisdiction = str(record.get("jurisdiction") or record.get("state") or "").upper()
            decision = self.registry.decide(jurisdiction)
            if decision.status != ACTIVE:
                raise CoverageDenied(f"{jurisdiction}: {decision.status}")
            record = dict(record)
            record["jurisdiction"] = jurisdiction
            record["coverage_status"] = decision.status
            record["active_source"] = decision.source
            record["rights_status"] = decision.rights
            record["source_release"] = decision.release
            record["freshness"] = decision.freshness
            record["proof_sha"] = decision.proof_sha
            self._rows.setdefault(jurisdiction, []).append(record)
            count += 1
        return count

    def query(self, jurisdiction: str, *, address: str | None = None, source_id: str | None = None) -> list[dict[str, Any]]:
        decision = self.registry.decide(jurisdiction)
        if decision.status != ACTIVE:
            raise CoverageDenied(f"{decision.jurisdiction}: {decision.status}")
        rows = self._rows.get(decision.jurisdiction, [])
        return [r for r in rows if (address is None or r.get("normalized_address") == address)
                and (source_id is None or r.get("source_record_id") == source_id)]


REQUIRED_RIGHTS_FIELDS = {
    "source", "granting_authority", "exact_dataset", "commercial_use",
    "storage_permission", "transformation_permission", "effective_date",
    "evidence_sha256",
}


def validate_rights_artifact(artifact: dict[str, Any]) -> tuple[bool, list[str]]:
    missing = sorted(REQUIRED_RIGHTS_FIELDS - set(artifact))
    if artifact.get("commercial_use") is not True:
        missing.append("commercial_use=true")
    if artifact.get("storage_permission") is not True:
        missing.append("storage_permission=true")
    if artifact.get("transformation_permission") is not True:
        missing.append("transformation_permission=true")
    return not missing, missing


def activate_rights_artifact(registry_path: Path, artifact_path: Path) -> dict[str, Any]:
    """Validate a synthetic/real grant and update only its jurisdiction row."""
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    ok, missing = validate_rights_artifact(artifact)
    if not ok:
        return {"activated": False, "missing": missing}
    jurisdiction = artifact["jurisdiction"].upper()
    rows = {r["jurisdiction"]: r for r in registry["rows"]}
    if jurisdiction not in rows:
        return {"activated": False, "missing": [f"unknown jurisdiction {jurisdiction}"]}
    row = rows[jurisdiction]
    row.update({"coverage_status": "COVERED_FALLBACK", "rights": "CLEAR_FOR_NON_MAILING_USE",
                "primary_source": artifact["source"], "proof_sha": artifact["evidence_sha256"]})
    registry["rows"] = [rows[r["jurisdiction"]] for r in registry["rows"]]
    registry["coverage_gaps"] = [r["jurisdiction"] for r in registry["rows"] if not r["coverage_status"].startswith("COVERED")]
    registry["us_51_coverage_complete"] = not registry["coverage_gaps"]
    Path(registry_path).write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"activated": True, "jurisdiction": jurisdiction, "proof_sha": artifact["evidence_sha256"]}


def rights_artifact_sha(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()
