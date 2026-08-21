"""Event-driven rights activation hook.

The caller invokes ``process_grant`` when an authoritative evidence file
arrives. There is no polling and no implicit legal inference.
"""
from pathlib import Path
from typing import Any
import json

from .omega_coverage import activate_rights_artifact


def process_grant(registry_path: Path, grant_path: Path) -> dict[str, Any]:
    result = activate_rights_artifact(registry_path, grant_path)
    if result.get("activated"):
        result["next"] = "run_existing_acquire_reconcile_index_smoke_and_51_state_proof"
    else:
        result["next"] = "hold_and_request_authoritative_clarification"
    return result


def pending_jurisdictions(registry_path: Path) -> list[str]:
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    return sorted(r["jurisdiction"] for r in registry.get("rows", []) if not r.get("coverage_status", "").startswith("COVERED"))
