"""Live inventory scan of the agent's GCS bucket in asia-south1.

In : Nothing (reads bucket constants from app.gcs.paths).
Out: A BucketInventory describing raster scans awaiting digitisation and LAS outputs.
Rule: Never raise. Failures are captured in BucketInventory.error and logged at
      ERROR level so production Cloud Logging reveals every fallback explicitly.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from app.contracts import BucketInventory, GcsObjectInfo
    from app.gcs.paths import (
        DIGITISABLE_EXTENSIONS,
        GCS_BUCKET,
        GCS_REGION,
        LAS_EXTENSION,
        LAS_OUTPUT_PREFIX,
        SCANNED_LOGS_PREFIX,
    )
except ImportError:
    from contracts import BucketInventory, GcsObjectInfo
    from gcs.paths import (
        DIGITISABLE_EXTENSIONS,
        GCS_BUCKET,
        GCS_REGION,
        LAS_EXTENSION,
        LAS_OUTPUT_PREFIX,
        SCANNED_LOGS_PREFIX,
    )

logger = logging.getLogger(__name__)

# Upper bound on objects enumerated per scan. The Gemini Enterprise chat surface
# shows a summary card, not a file browser, so an unbounded listing would cost
# latency without improving the rendered output.
MAX_OBJECTS: int = 500


def _classify(name: str) -> str | None:
    """Return 'scan' for digitisable inputs, 'las' for LAS files, None to ignore.

    'scan' covers rasters and scanned PDFs alike, because both are digitisable
    and the petrophysicist thinks of them as the same thing: a picture of a log
    sheet. ingest/load_image.py is what tells them apart.

    Directory placeholder objects (trailing slash, zero bytes) are ignored so
    they do not inflate the object count shown to the petrophysicist.
    """
    if name.endswith("/"):
        return None
    lowered = name.lower()
    if lowered.endswith(LAS_EXTENSION):
        return "las"
    if lowered.endswith(DIGITISABLE_EXTENSIONS):
        return "scan"
    return None


def scan_bucket_inventory() -> BucketInventory:
    """Enumerate this agent's digitisable scans and LAS outputs.

    Scoped deliberately to SCANNED_LOGS_PREFIX and LAS_OUTPUT_PREFIX. The bucket
    is shared with other petrophysics agents that own unrelated prefixes (LAS/,
    raw/, composites/, plots/, reports/). A whole-bucket listing would report
    their files as this agent's, which is both wrong and misleading in the chat
    card, so each prefix is listed explicitly.

    Uses objects.list only. The agent service account holds
    roles/storage.objectUser, which grants object-level permissions but NOT
    storage.buckets.get, so any call reading bucket metadata would 403.

    Returns a BucketInventory with ok=False and a populated 'error' field if the
    scan cannot complete, rather than raising into the ADK callback chain where
    an exception would abort the agent's entire response.
    """
    try:
        from app.gcs.client import build_storage_client
    except ImportError:
        try:
            from gcs.client import build_storage_client
        except ImportError as exc:
            logger.error(
                "scan_bucket_inventory: FALLBACK - storage client unavailable: %s",
                exc,
            )
            return BucketInventory(
                bucket=GCS_BUCKET,
                region=GCS_REGION,
                ok=False,
                error=f"storage client unavailable: {exc}",
            )

    scans: list[GcsObjectInfo] = []
    las_files: list[GcsObjectInfo] = []

    try:
        client = build_storage_client()

        # One listing per owned prefix. list_blobs targets the bucket by name
        # and never fetches bucket metadata.
        for prefix, expected in (
            (SCANNED_LOGS_PREFIX, "scan"),
            (LAS_OUTPUT_PREFIX, "las"),
        ):
            for blob in client.list_blobs(
                GCS_BUCKET, prefix=prefix, max_results=MAX_OBJECTS
            ):
                # Extension still decides: it rejects stray files that do not
                # belong in the prefix, such as a PNG dropped among the scans.
                if _classify(blob.name) != expected:
                    continue
                info = GcsObjectInfo(
                    name=blob.name,
                    size_bytes=int(blob.size or 0),
                    content_type=blob.content_type or "application/octet-stream",
                    updated=blob.updated.isoformat() if blob.updated else None,
                )
                if expected == "las":
                    las_files.append(info)
                else:
                    scans.append(info)

    except Exception as exc:  # noqa: BLE001 - callback must never propagate
        logger.error(
            "scan_bucket_inventory: FALLBACK - listing gs://%s failed: %s: %s",
            GCS_BUCKET,
            type(exc).__name__,
            exc,
        )
        return BucketInventory(
            bucket=GCS_BUCKET,
            region=GCS_REGION,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    # Deterministic ordering keeps the rendered card stable across turns.
    scans.sort(key=lambda o: o.name)
    las_files.sort(key=lambda o: o.name)

    logger.info(
        "scan_bucket_inventory: OK - gs://%s [%s] -> %d scan(s), %d LAS file(s)",
        GCS_BUCKET,
        GCS_REGION,
        len(scans),
        len(las_files),
    )
    return BucketInventory(
        bucket=GCS_BUCKET,
        region=GCS_REGION,
        scans=scans,
        las_files=las_files,
        ok=True,
    )
