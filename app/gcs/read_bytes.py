"""Read a single object out of the agent's GCS bucket as raw bytes.

In : An object key (for example 'Scanned Well Logs/Well_log_schlum.jpg').
Out: The object's bytes.
Rule: Key to bytes, nothing else. No parsing, no decoding, no classification.
"""

from __future__ import annotations

import logging

try:
    from app.gcs.client import build_storage_client
    from app.gcs.paths import GCS_BUCKET
except ImportError:
    from gcs.client import build_storage_client
    from gcs.paths import GCS_BUCKET

logger = logging.getLogger(__name__)


def read_bytes(object_name: str) -> bytes:
    """Download one object from the agent's bucket.

    Uses blob.download_as_bytes(), which issues a plain objects.get. The agent
    service account holds roles/storage.objectUser on this bucket, which covers
    object reads but NOT storage.buckets.get, so this deliberately avoids any
    call that would touch bucket metadata.

    Args:
        object_name: Full object key within the agent's bucket, without the
            'gs://bucket/' prefix.

    Returns:
        The object's raw bytes.

    Raises:
        ValueError: if object_name is empty.
        google.cloud.exceptions.NotFound: if the object does not exist.
        google.api_core.exceptions.Forbidden: if the caller lacks object read
            permission. Both are allowed to propagate: a caller asking for a
            specific file needs to know it was not delivered, unlike the
            inventory scan which degrades to a visible error card.
    """
    if not object_name:
        raise ValueError("object_name cannot be empty.")

    client = build_storage_client()
    # bucket() constructs a reference lazily and does not fetch bucket metadata.
    blob = client.bucket(GCS_BUCKET).blob(object_name)
    data = blob.download_as_bytes()

    logger.info(
        "read_bytes: OK - gs://%s/%s -> %d byte(s)",
        GCS_BUCKET,
        object_name,
        len(data),
    )
    return data
