"""Authenticated Cloud Storage client for the Log Digitisation Agent.

In : Nothing (resolves Application Default Credentials).
Out: A google.cloud.storage.Client suitable for the agent's service account.
Rule: Returns a client and nothing else. Performs no listing, reading or writing.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_storage_client() -> Any:
    """Create a Storage client that does NOT send the x-goog-user-project header.

    google-auth attaches 'x-goog-user-project: <quota project>' whenever the
    resolved credentials carry a quota_project_id (google/auth/credentials.py).
    Cloud Storage then requires the caller to hold 'serviceusage.services.use'
    on that project, on top of the object permissions.

    The agent service account is deliberately scoped to roles/storage.objectUser
    on the data bucket and holds no project-wide Service Usage role, so the
    header turns an otherwise-valid read into:

        403 ... does not have serviceusage.services.use access to the Google
        Cloud project. Permission 'serviceusage.services.use' denied on resource

    Clearing the quota project bills the request to the bucket's own project and
    reduces the requirement back to plain storage permissions, keeping the
    service account least-privileged.

    Raises:
        ImportError: if google-cloud-storage is not installed. Callers that must
            not fail (such as ADK callbacks) are responsible for catching it.
    """
    from google.cloud import storage

    try:
        import google.auth

        credentials, project = google.auth.default()
        if getattr(credentials, "quota_project_id", None) and hasattr(
            credentials, "with_quota_project"
        ):
            logger.info(
                "build_storage_client: clearing quota project %r to avoid "
                "x-goog-user-project (needs serviceusage.services.use)",
                credentials.quota_project_id,
            )
            credentials = credentials.with_quota_project(None)
        return storage.Client(project=project, credentials=credentials)
    except Exception as exc:  # noqa: BLE001 - degrade to default construction
        logger.warning(
            "build_storage_client: FALLBACK - explicit credential setup failed "
            "(%s: %s); falling back to storage.Client()",
            type(exc).__name__,
            exc,
        )
        return storage.Client()
