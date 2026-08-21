"""Dataset-scoped rights vectors; statutory classification is not permission."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, asdict
from typing import Any

USES = ("access", "commercial_internal_use", "derivative_use", "redistribution", "resale", "attribution", "fees", "privacy_sensitive_use", "mailing_use", "political_use")

@dataclass(frozen=True)
class RightsVector:
    jurisdiction: str
    agency: str
    dataset: str
    acquisition_method: str
    statutory_basis: str
    terms_version: str
    intended_use: str
    rights: dict[str, str]
    evidence_hash: str

    def allows(self, use: str) -> bool:
        return self.rights.get(use, "DENIED") == "ALLOWED"

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


CLASSIFICATIONS = {
    "CA": "CLEAR_NONEXEMPT_GIS_PUBLIC_RECORD",
    "FL": "CLEAR_PUBLIC_GIS_UNLESS_SPECIFIC_EXCEPTION",
    "TX": "SOURCE_SPECIFIC",
    "NC": "COMMERCIAL_REUSE_CONDITIONAL",
    "IN": "ELECTRONIC_MAP_SPECIAL_REGIME",
    "WI": "ACCESS_CLEAR_REUSE_SOURCE_SPECIFIC",
    "NV": "GIS_COST_RECOVERY_SOURCE_TERMS",
    "HI": "DATASET_SPECIFIC",
    "OR": "INTERGOVERNMENTAL_GIS_RESTRICTED_CLASS",
    "NH": "DENIED_FOR_GENERAL_COMMERCIAL_INGEST"
}


def create_vector(*, jurisdiction: str, agency: str, dataset: str, acquisition_method: str,
                  statutory_basis: str, terms_version: str, intended_use: str,
                  rights: dict[str, str], evidence_hash: str) -> RightsVector:
    missing = set(rights) - set(USES)
    if missing: raise ValueError(f"unknown rights fields: {sorted(missing)}")
    return RightsVector(jurisdiction.upper(), agency, dataset, acquisition_method,
                        statutory_basis, terms_version, intended_use, dict(rights), evidence_hash)
