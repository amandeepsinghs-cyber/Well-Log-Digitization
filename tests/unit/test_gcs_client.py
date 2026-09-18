"""Tests for the authenticated Cloud Storage client.

Regression coverage for a production 403 where the agent service account, scoped
to roles/storage.objectUser on the data bucket, was rejected because the Storage
client sent an 'x-goog-user-project' header demanding 'serviceusage.services.use'.
"""

from __future__ import annotations

from unittest import mock

from app.gcs.client import build_storage_client


class _FakeCreds:
    """Credentials double that mimics google-auth quota-project behaviour."""

    def __init__(self, quota_project_id: str | None) -> None:
        self.quota_project_id = quota_project_id
        self.with_quota_project_called_with: object = "<not-called>"

    def with_quota_project(self, value: str | None) -> _FakeCreds:
        self.with_quota_project_called_with = value
        return _FakeCreds(value)


def test_strips_quota_project() -> None:
    """The quota project must be cleared so no x-goog-user-project is sent.

    With that header present, Cloud Storage additionally requires
    'serviceusage.services.use', which the least-privileged agent service
    account deliberately does not hold.
    """
    creds = _FakeCreds("og-agentic-ecosystem")
    captured: dict[str, object] = {}

    def _client(project: str, credentials: _FakeCreds) -> str:
        captured["project"] = project
        captured["credentials"] = credentials
        return "client"

    with (
        mock.patch("google.cloud.storage.Client", _client),
        mock.patch("google.auth.default", return_value=(creds, "og-agentic-ecosystem")),
    ):
        result = build_storage_client()

    assert result == "client"
    assert creds.with_quota_project_called_with is None, (
        "with_quota_project(None) must be called to drop x-goog-user-project"
    )
    assert captured["credentials"].quota_project_id is None


def test_leaves_unset_quota_project_alone() -> None:
    """Credentials without a quota project need no modification."""
    creds = _FakeCreds(None)

    with (
        mock.patch(
            "google.cloud.storage.Client", lambda project, credentials: "client"
        ),
        mock.patch("google.auth.default", return_value=(creds, "p")),
    ):
        assert build_storage_client() == "client"

    assert creds.with_quota_project_called_with == "<not-called>"


def test_falls_back_when_credential_setup_fails() -> None:
    """A credential resolution failure must degrade to default construction."""
    with (
        mock.patch("google.cloud.storage.Client", return_value="default-client"),
        mock.patch("google.auth.default", side_effect=RuntimeError("no ADC")),
    ):
        assert build_storage_client() == "default-client"
