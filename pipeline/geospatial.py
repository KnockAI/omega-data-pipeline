"""Canonical geographic normalization helpers."""
import math
import re
import h3

ZIP5_RE = re.compile(r"^\d{5}$")
H3_RESOLUTION = 9

def normalize_zip5(value) -> str | None:
    raw = str(value or "").strip()[:5]
    return raw if ZIP5_RE.fullmatch(raw) else None

def coordinate_h3(lat, lng, resolution=H3_RESOLUTION) -> str | None:
    try:
        lat, lng = float(lat), float(lng)
        if not (-90 <= lat <= 90 and -180 <= lng <= 180): return None
        return h3.latlng_to_cell(lat, lng, resolution)
    except (TypeError, ValueError):
        return None

def validate_h3_resolution(cell: str, resolution=H3_RESOLUTION) -> bool:
    try: return h3.get_resolution(cell) == resolution
    except Exception: return False

def safe_backoff(attempt: int, base=1.0, cap=60.0) -> float:
    return min(cap, base * (2 ** max(0, attempt - 1)))
