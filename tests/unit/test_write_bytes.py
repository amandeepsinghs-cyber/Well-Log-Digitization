"""Tests for the GCS object writer and the naming that decides where it lands.

The bucket is shared with the other petrophysics agents and the service account
can write anywhere in it, so the property that matters most is that this agent
CANNOT. Most of the tests below pin that boundary.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.gcs.paths import (
    GCS_BUCKET,
    LAS_OUTPUT_PREFIX,
    las_object_name,
    well_name_from,
)
from app.gcs.write_bytes import write_bytes

_KEY = f"{LAS_OUTPUT_PREFIX}WELL_A.las"


def _client() -> MagicMock:
    """A stand-in for a storage Client, exposing the blob it was asked for."""
    client = MagicMock()
    client.bucket.return_value.blob.return_value = MagicMock()
    return client


def _blob_of(client: MagicMock) -> MagicMock:
    return client.bucket.return_value.blob.return_value


# -- The prefix boundary ------------------------------------------------------

def test_writes_under_the_agents_own_prefix() -> None:
    client = _client()
    with patch("app.gcs.write_bytes.build_storage_client", return_value=client):
        write_bytes(_KEY, b"data", "text/plain")

    client.bucket.assert_called_once_with(GCS_BUCKET)
    client.bucket.return_value.blob.assert_called_once_with(_KEY)


def test_refuses_to_write_outside_the_output_prefix() -> None:
    """The service account could overwrite another agent's curves; this cannot."""
    client = _client()
    with patch("app.gcs.write_bytes.build_storage_client", return_value=client):
        with pytest.raises(ValueError, match="writes only under"):
            write_bytes("LAS/someone_elses.las", b"data", "text/plain")


def test_refuses_to_write_into_the_scanned_inputs_prefix() -> None:
    """Writing back over the source scan would destroy the only evidence."""
    client = _client()
    with patch("app.gcs.write_bytes.build_storage_client", return_value=client):
        with pytest.raises(ValueError, match="writes only under"):
            write_bytes("Scanned Well Logs/Well_log_schlum.jpg", b"x", "image/jpeg")


def test_a_refused_write_never_reaches_gcs() -> None:
    """The guard must fire before the client is built, not after uploading."""
    client = _client()
    with patch("app.gcs.write_bytes.build_storage_client", return_value=client):
        with pytest.raises(ValueError):
            write_bytes("elsewhere/file.las", b"data", "text/plain")

    client.bucket.assert_not_called()


def test_refuses_an_empty_key() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        write_bytes("", b"data", "text/plain")


# -- What is uploaded ---------------------------------------------------------

def test_uploads_the_bytes_with_the_content_type_given() -> None:
    client = _client()
    with patch("app.gcs.write_bytes.build_storage_client", return_value=client):
        write_bytes(_KEY, b"~VERSION", "text/plain")

    _blob_of(client).upload_from_string.assert_called_once_with(
        b"~VERSION", content_type="text/plain"
    )


def test_returns_the_uri_of_what_was_written() -> None:
    client = _client()
    with patch("app.gcs.write_bytes.build_storage_client", return_value=client):
        uri = write_bytes(_KEY, b"data", "text/plain")

    assert uri == f"gs://{GCS_BUCKET}/{_KEY}"


def test_never_touches_bucket_metadata() -> None:
    """The agent's service account is not granted storage.buckets.get."""
    client = _client()
    with patch("app.gcs.write_bytes.build_storage_client", return_value=client):
        write_bytes(_KEY, b"data", "text/plain")

    client.get_bucket.assert_not_called()
    client.bucket.return_value.reload.assert_not_called()


# -- Naming -------------------------------------------------------------------

def test_well_name_comes_from_the_scan_filename() -> None:
    assert well_name_from("Scanned Well Logs/Well_log_schlum.jpg") == "WELL_LOG_SCHLUM"


def test_well_name_drops_the_prefix_and_extension() -> None:
    assert well_name_from("a/b/c/WELL-42.tiff") == "WELL_42"


def test_well_name_collapses_spaces_and_punctuation() -> None:
    assert well_name_from("scans/North Sea 15 9-F.png") == "NORTH_SEA_15_9_F"


def test_well_name_handles_a_filename_with_no_extension() -> None:
    assert well_name_from("scans/rawscan") == "RAWSCAN"


def test_a_filename_with_nothing_usable_is_refused() -> None:
    """Silently producing a well called '' would land a LAS at a blank key."""
    with pytest.raises(ValueError, match="no alphanumeric characters"):
        well_name_from("scans/---.jpg")


def test_the_las_key_is_the_well_name_under_the_output_prefix() -> None:
    assert (
        las_object_name("Scanned Well Logs/Well_log_schlum.jpg")
        == f"{LAS_OUTPUT_PREFIX}WELL_LOG_SCHLUM.las"
    )


def test_the_derived_key_is_one_the_writer_accepts() -> None:
    """The two halves of the naming decision must agree, or nothing publishes."""
    client = _client()
    key = las_object_name("Scanned Well Logs/Well_log_schlum.jpg")
    with patch("app.gcs.write_bytes.build_storage_client", return_value=client):
        write_bytes(key, b"data", "text/plain")

    _blob_of(client).upload_from_string.assert_called_once()
