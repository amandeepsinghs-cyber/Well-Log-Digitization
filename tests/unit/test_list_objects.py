"""Tests for the GCS object listers.

The bucket is shared with other petrophysics agents, so the property that
matters most is SCOPE: each function must see only its own prefix and only its
own file types. A lister that over-reaches would report another agent's data as
this agent's, which has already happened once on this project.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.gcs.list_objects import list_images, list_las
from app.gcs.paths import GCS_BUCKET, LAS_OUTPUT_PREFIX, SCANNED_LOGS_PREFIX


def _blob(name: str) -> MagicMock:
    """A stand-in for a google.cloud.storage.Blob carrying only its key."""
    blob = MagicMock()
    blob.name = name
    return blob


def _client_returning(*names: str) -> MagicMock:
    client = MagicMock()
    client.list_blobs.return_value = [_blob(n) for n in names]
    return client


# -- Scope: each lister stays inside its own prefix ---------------------------

def test_list_las_requests_only_the_output_prefix() -> None:
    client = _client_returning()
    with patch("app.gcs.list_objects.build_storage_client", return_value=client):
        list_las()
    client.list_blobs.assert_called_once_with(GCS_BUCKET, prefix=LAS_OUTPUT_PREFIX)


def test_list_images_requests_only_the_scanned_prefix() -> None:
    client = _client_returning()
    with patch("app.gcs.list_objects.build_storage_client", return_value=client):
        list_images()
    client.list_blobs.assert_called_once_with(GCS_BUCKET, prefix=SCANNED_LOGS_PREFIX)


# -- Filtering ----------------------------------------------------------------

def test_list_las_keeps_only_las_files() -> None:
    """A raster sitting in the output folder is not a LAS file."""
    client = _client_returning(
        f"{LAS_OUTPUT_PREFIX}WELL-A.las",
        f"{LAS_OUTPUT_PREFIX}notes.txt",
        f"{LAS_OUTPUT_PREFIX}preview.png",
    )
    with patch("app.gcs.list_objects.build_storage_client", return_value=client):
        assert list_las() == [f"{LAS_OUTPUT_PREFIX}WELL-A.las"]


def test_list_images_keeps_every_digitisable_extension() -> None:
    """A human drops files here by hand, so the extension filter is load-bearing.

    A scanned PDF belongs in the list: ingest/load_image.py renders it. A .docx
    does not, and must not reach the decoder.
    """
    client = _client_returning(
        f"{SCANNED_LOGS_PREFIX}a.jpg",
        f"{SCANNED_LOGS_PREFIX}b.JPEG",
        f"{SCANNED_LOGS_PREFIX}c.png",
        f"{SCANNED_LOGS_PREFIX}d.tif",
        f"{SCANNED_LOGS_PREFIX}e.tiff",
        f"{SCANNED_LOGS_PREFIX}f.pdf",
        f"{SCANNED_LOGS_PREFIX}g.PDF",
        f"{SCANNED_LOGS_PREFIX}spec.docx",
    )
    with patch("app.gcs.list_objects.build_storage_client", return_value=client):
        keys = list_images()

    assert [k.rsplit("/", 1)[-1] for k in keys] == [
        "a.jpg",
        "b.JPEG",
        "c.png",
        "d.tif",
        "e.tiff",
        "f.pdf",
        "g.PDF",
    ]


def test_extension_matching_is_case_insensitive() -> None:
    """Vendor exports arrive as .LAS as often as .las."""
    client = _client_returning(f"{LAS_OUTPUT_PREFIX}WELL-A.LAS")
    with patch("app.gcs.list_objects.build_storage_client", return_value=client):
        assert list_las() == [f"{LAS_OUTPUT_PREFIX}WELL-A.LAS"]


def test_folder_placeholders_are_skipped() -> None:
    """The console creates a zero-byte object named after the folder itself."""
    client = _client_returning(
        LAS_OUTPUT_PREFIX,
        f"{LAS_OUTPUT_PREFIX}WELL-A.las",
    )
    with patch("app.gcs.list_objects.build_storage_client", return_value=client):
        assert list_las() == [f"{LAS_OUTPUT_PREFIX}WELL-A.las"]


# -- Ordering and the empty case ----------------------------------------------

def test_results_are_sorted() -> None:
    """Deterministic order: the agent names files to the user in this order."""
    client = _client_returning(
        f"{LAS_OUTPUT_PREFIX}C.las",
        f"{LAS_OUTPUT_PREFIX}A.las",
        f"{LAS_OUTPUT_PREFIX}B.las",
    )
    with patch("app.gcs.list_objects.build_storage_client", return_value=client):
        assert list_las() == [
            f"{LAS_OUTPUT_PREFIX}A.las",
            f"{LAS_OUTPUT_PREFIX}B.las",
            f"{LAS_OUTPUT_PREFIX}C.las",
        ]


def test_an_empty_prefix_returns_an_empty_list() -> None:
    client = _client_returning()
    with patch("app.gcs.list_objects.build_storage_client", return_value=client):
        assert list_images() == []


def test_a_listing_error_propagates() -> None:
    """A permissions failure must not read as 'no files here'."""
    client = MagicMock()
    client.list_blobs.side_effect = PermissionError("403 does not have storage.objects.list")
    with patch("app.gcs.list_objects.build_storage_client", return_value=client):
        try:
            list_images()
        except PermissionError as exc:
            assert "403" in str(exc)
        else:
            raise AssertionError("the error was swallowed")
