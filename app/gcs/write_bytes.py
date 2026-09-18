"""Write a single object into the agent's GCS bucket.

In : an object key, the bytes to store, and the content type to store them as.
Out: the gs:// URI of what was written.
Rule: bytes to object, nothing else. No serialising, no naming decisions — the
      key is decided by gcs/paths.py and the bytes by las/write_las.py.

      This is the only file in the project that writes to GCS, which is what
      makes the prefix guard below enforceable.
"""

from __future__ import annotations

import logging

try:
    from app.gcs.client import build_storage_client
    from app.gcs.paths import GCS_BUCKET, LAS_OUTPUT_PREFIX, gcs_uri
except ImportError:
    from gcs.client import build_storage_client
    from gcs.paths import GCS_BUCKET, LAS_OUTPUT_PREFIX, gcs_uri

logger = logging.getLogger(__name__)


def write_bytes(object_name: str, data: bytes, content_type: str) -> str:
    """Upload one object to the agent's bucket and return its gs:// URI.

    Overwrites silently if the key already exists. That is deliberate:
    re-digitising a scan should replace its LAS rather than accumulate
    near-identical copies, and GCS object versioning is the right place to keep
    the history if it is ever wanted.

    Args:
        object_name: full object key within the agent's bucket, without the
            'gs://bucket/' prefix. Must sit under the digitised-output prefix.
        data: the bytes to store.
        content_type: MIME type to record on the object. Required rather than
            defaulted, because an object stored as the application/octet-stream
            GCS falls back to downloads instead of previewing, and a LAS file
            nobody can read in the console is a support call.

    Returns:
        The gs:// URI of the object written.

    Raises:
        ValueError: if object_name is empty or falls outside the agent's own
            output prefix.
        google.api_core.exceptions.Forbidden: if the caller lacks object write
            permission. Propagated: a caller that believes it has published a
            LAS must not be told it succeeded when nothing was stored.
    """
    if not object_name:
        raise ValueError("object_name cannot be empty.")

    # This bucket is shared with the other petrophysics agents, and the service
    # account holds roles/storage.objectUser across all of it — enough to
    # overwrite another agent's curves. Audit logging would record that after
    # the fact; this refuses it beforehand. The check is here rather than in the
    # caller because this is the single choke point every write passes through.
    if not object_name.startswith(LAS_OUTPUT_PREFIX):
        raise ValueError(
            f"Refusing to write {object_name!r}: this agent writes only under "
            f"{LAS_OUTPUT_PREFIX!r} in the shared bucket {GCS_BUCKET!r}."
        )

    client = build_storage_client()
    # bucket() builds a reference lazily; it issues no buckets.get, which the
    # agent's service account is not granted.
    blob = client.bucket(GCS_BUCKET).blob(object_name)
    blob.upload_from_string(data, content_type=content_type)

    uri = gcs_uri(object_name)
    logger.info(
        "write_bytes: OK - %d byte(s) of %s -> %s", len(data), content_type, uri
    )
    return uri
