"""Tests for the GCS inventory scan.

The scan runs inside an ADK after_agent_callback, so its defining property is
that it never raises: a storage failure must surface as a visible error card,
not abort the agent's entire response.
"""

from __future__ import annotations

from unittest import mock

from app.gcs.inventory import _classify, scan_bucket_inventory
from app.gcs.paths import GCS_BUCKET, GCS_REGION


def test_classify_recognises_scans_las_and_ignores_folders() -> None:
    assert _classify("Scanned Well Logs/a.JPG") == "scan"
    assert _classify("x.tiff") == "scan"
    assert _classify("Digitised Well Logs/a.las") == "las"
    assert _classify("Scanned Well Logs/") is None
    assert _classify("notes.txt") is None


def test_scan_returns_error_inventory_on_failure() -> None:
    """A Storage failure must degrade gracefully, never raise into the callback."""
    with mock.patch(
        "app.gcs.client.build_storage_client",
        side_effect=RuntimeError("403 Forbidden"),
    ):
        inv = scan_bucket_inventory()

    assert inv.ok is False
    assert "403 Forbidden" in (inv.error or "")
    assert inv.bucket == GCS_BUCKET
    assert inv.region == GCS_REGION
    assert inv.total_objects == 0
