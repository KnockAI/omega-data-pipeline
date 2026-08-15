import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.geospatial import coordinate_h3, normalize_zip5, safe_backoff, validate_h3_resolution
from pipeline.observability import STATUSES

def test_zip_and_h3_precision_contract():
    assert normalize_zip5("48104-1234") == "48104"
    assert normalize_zip5("bad") is None
    cell = coordinate_h3(42.28, -83.74)
    assert validate_h3_resolution(cell, 9)

def test_retry_is_bounded():
    assert safe_backoff(1) == 1
    assert safe_backoff(99) == 60

def test_statuses_fail_closed():
    assert {"complete", "partial", "failed", "rate_limited", "blocked"} <= STATUSES
