"""List the objects in the agent's two prefixes: scanned rasters and digitised LAS.

In : Nothing (bucket and prefixes come from app.gcs.paths).
Out: Sorted object keys, each including its prefix.
Rule: Listing only. Never reads an object's contents, and never touches bucket
      metadata — the agent's service account deliberately lacks
      `storage.buckets.get`.

      Replaces the earlier gcs/list_las.py. Listing scans and listing LAS files
      differ only by which prefix and which extensions, so the two public
      functions are thin wrappers over one private lister rather than two
      near-identical files that can drift apart.
"""

from __future__ import annotations

import logging

try:
    from app.gcs.client import build_storage_client
    from app.gcs.paths import (
        DIGITISABLE_EXTENSIONS,
        GCS_BUCKET,
        LAS_EXTENSION,
        LAS_OUTPUT_PREFIX,
        SCANNED_LOGS_PREFIX,
    )
except ImportError:
    from gcs.client import build_storage_client
    from gcs.paths import (
        DIGITISABLE_EXTENSIONS,
        GCS_BUCKET,
        LAS_EXTENSION,
        LAS_OUTPUT_PREFIX,
        SCANNED_LOGS_PREFIX,
    )

logger = logging.getLogger(__name__)


def list_las() -> list[str]:
    """Return the keys of every LAS file under the digitised-output prefix.

    Scoped to LAS_OUTPUT_PREFIX so it sees only this agent's digitised output —
    never the scanned rasters, and never the other petrophysics agents' prefixes
    in this shared bucket.

    Returns:
        Sorted object keys, each including the prefix. Empty if none exist.

    Raises:
        google.api_core.exceptions.Forbidden: if the caller lacks list
            permission. Propagated so a caller cannot mistake a permissions
            failure for an empty output folder.
    """
    return _list(LAS_OUTPUT_PREFIX, (LAS_EXTENSION,))


def list_images() -> list[str]:
    """Return the keys of every scan awaiting digitisation.

    Scoped to SCANNED_LOGS_PREFIX. Filtering on DIGITISABLE_EXTENSIONS matters
    because this prefix is where a human drops files: a stray .docx or .xlsx
    would otherwise be handed to the decoder as if it were a scan. Scanned PDFs
    ARE included — ingest/load_image.py renders them to pixels.

    Returns:
        Sorted object keys, each including the prefix. Empty if none exist.

    Raises:
        google.api_core.exceptions.Forbidden: as for list_las().
    """
    return _list(SCANNED_LOGS_PREFIX, DIGITISABLE_EXTENSIONS)


def _list(prefix: str, extensions: tuple[str, ...]) -> list[str]:
    """List keys under one prefix, keeping only the given file extensions."""
    client = build_storage_client()
    blobs = client.list_blobs(GCS_BUCKET, prefix=prefix)

    keys = [
        blob.name
        for blob in blobs
        # Exclude the zero-byte folder placeholder that the console creates, and
        # anything whose extension says it is not the kind of file we asked for.
        if not blob.name.endswith("/") and blob.name.lower().endswith(extensions)
    ]
    keys.sort()

    logger.info(
        "list_objects: OK - gs://%s/%s -> %d file(s) matching %s",
        GCS_BUCKET,
        prefix,
        len(keys),
        extensions,
    )
    return keys
