"""Tests for the conversational workflow: list, confirm, digitise, display.

These cover the wiring that turns four tools into one conversation — what the
agent can state as fact, and what it queues for the callback to draw. The
pipeline itself is replaced throughout; nothing here digitises anything.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from app.contracts import BucketInventory, GcsObjectInfo
from app.ingest.load_pdf import rasterise_first_page
from app.integration import tools


class FakeContext:
    """Stand-in for ADK's ToolContext, which is only ever used for .state here."""

    def __init__(self) -> None:
        self.state: dict[str, str] = {}


def _scan(name: str, size: int = 200_000) -> GcsObjectInfo:
    return GcsObjectInfo(name=name, size_bytes=size, content_type="image/jpeg")


def _las(name: str, size: int = 55_000) -> GcsObjectInfo:
    return GcsObjectInfo(name=name, size_bytes=size, content_type="text/plain")


# -- list_well_logs -----------------------------------------------------------

def test_the_agent_can_count_what_is_still_only_a_scan() -> None:
    """The question 'how many are not digitised yet' must have a real answer.

    The inventory card shows the same files, but it is drawn after the model
    has written its text, so without this tool the model can only defer to it.
    """
    inventory = BucketInventory(
        bucket="og-agentic-petrophysics-data",
        region="us-central1",
        scans=[
            _scan("Scanned Well Logs/Well_log_schlum.jpg"),
            _scan("Scanned Well Logs/Deep_well_2.pdf"),
            _scan("Scanned Well Logs/Old_sheet.tif"),
        ],
        las_files=[_las("Digitised Well Logs/WELL_LOG_SCHLUM.las")],
    )

    with patch.object(tools, "scan_bucket_inventory", return_value=inventory):
        result = tools.list_well_logs()

    assert result["ok"]
    assert result["scan_count"] == 3
    assert result["digitised_count"] == 1
    assert result["awaiting_digitisation"] == 2


def test_a_scan_is_paired_to_its_las_by_the_writer_s_own_naming_rule() -> None:
    """Pairing must not be a guess: it uses the rule the digitiser writes by."""
    inventory = BucketInventory(
        bucket="b",
        region="r",
        scans=[_scan("Scanned Well Logs/Well_log_schlum.jpg")],
        las_files=[_las("Digitised Well Logs/WELL_LOG_SCHLUM.las")],
    )

    with patch.object(tools, "scan_bucket_inventory", return_value=inventory):
        scan = tools.list_well_logs()["scans"][0]

    assert scan["digitised"] is True
    assert scan["las_object_name"] == "Digitised Well Logs/WELL_LOG_SCHLUM.las"


def test_the_destination_key_is_reported_before_the_file_exists() -> None:
    """The agent has to name the destination when it asks permission to write."""
    inventory = BucketInventory(
        bucket="b", region="r", scans=[_scan("Scanned Well Logs/Deep_well_2.pdf")]
    )

    with patch.object(tools, "scan_bucket_inventory", return_value=inventory):
        scan = tools.list_well_logs()["scans"][0]

    assert scan["digitised"] is False
    assert scan["las_object_name"] == "Digitised Well Logs/DEEP_WELL_2.las"


def test_pdf_and_image_are_distinguished_for_the_reader() -> None:
    """'Only PDFs or images' is the distinction the petrophysicist asks about."""
    inventory = BucketInventory(
        bucket="b",
        region="r",
        scans=[
            _scan("Scanned Well Logs/a.pdf"),
            _scan("Scanned Well Logs/b.JPG"),
            _scan("Scanned Well Logs/c.tiff"),
        ],
    )

    with patch.object(tools, "scan_bucket_inventory", return_value=inventory):
        formats = [s["format"] for s in tools.list_well_logs()["scans"]]

    assert formats == ["PDF", "JPG", "TIFF"]


def test_a_failed_scan_is_reported_as_a_failure_not_an_empty_bucket() -> None:
    """An empty list and a 403 must never look the same to the agent."""
    inventory = BucketInventory(
        bucket="b", region="r", ok=False, error="Forbidden: storage.objects.list"
    )

    with patch.object(tools, "scan_bucket_inventory", return_value=inventory):
        result = tools.list_well_logs()

    assert result["ok"] is False
    assert "Forbidden" in result["error"]


# -- digitise chains into the chart -------------------------------------------

def _digitise_result() -> SimpleNamespace:
    """A minimal stand-in for what pipeline_digitise.digitise returns."""
    curve = SimpleNamespace(
        mnemonic="GR",
        unit="GAPI",
        description="Gamma Ray",
        coverage_fraction=0.893,
        mean_confidence=0.82,
    )
    document = SimpleNamespace(depth_units="FT", depth_step=0.5, curves=[curve])
    return SimpleNamespace(
        las_document=document,
        source_gcs_uri="gs://b/Scanned Well Logs/Well_log_schlum.jpg",
        output_las_uri="gs://b/Digitised Well Logs/WELL_LOG_SCHLUM.las",
        well_name="WELL_LOG_SCHLUM",
        depth_range=(7000.5, 7300.2),
        overall_confidence=0.81,
        qc_findings=[],
    )


def test_digitising_queues_the_chart_for_the_same_turn() -> None:
    """Nobody asks for a log to be digitised and then asks again to see it."""
    context = FakeContext()

    with patch.object(tools, "digitise", return_value=_digitise_result()):
        result = tools.digitise_scanned_log(
            "Scanned Well Logs/Well_log_schlum.jpg", context
        )

    assert result["ok"]
    assert result["chart_attached"] is True
    assert (
        context.state[tools.PENDING_CHART_KEY]
        == "Digitised Well Logs/WELL_LOG_SCHLUM.las"
    )


def test_a_failed_digitisation_queues_nothing() -> None:
    """A chart of a file that was never written would be a fabrication."""
    context = FakeContext()

    with patch.object(tools, "digitise", side_effect=RuntimeError("no gridlines")):
        result = tools.digitise_scanned_log("Scanned Well Logs/broken.jpg", context)

    assert result["ok"] is False
    assert tools.PENDING_CHART_KEY not in context.state


# -- PDF ingestion ------------------------------------------------------------

def _pdf_bytes(pages: int = 1, size: tuple[int, int] = (400, 300)) -> bytes:
    """Build a PDF in memory, one uniform page per requested page."""
    images = [Image.new("RGB", size, (255, 255, 255)) for _ in range(pages)]
    buffer = io.BytesIO()
    images[0].save(
        buffer, "PDF", resolution=72.0, save_all=True, append_images=images[1:]
    )
    return buffer.getvalue()


def test_a_scan_is_rendered_at_its_own_resolution() -> None:
    """A scanned PDF is a raster in a wrapper; resampling it is pure loss.

    This is not a preference. Upscaling the 919 px reference sheet to 1000 px
    moved the depth grid enough that a header text block landed within half a
    grid pitch of a rule, was taken for a depth label, and failed the sheet.
    """
    pixels = rasterise_first_page(_pdf_bytes(size=(919, 778)))

    assert pixels.shape[1] == 919, "the embedded raster's own width, not a target"
    assert pixels.shape[0] == 778


def test_a_scan_smaller_than_the_target_is_not_inflated() -> None:
    """Interpolated pixels would give the tracer detail the scanner never saw."""
    pixels = rasterise_first_page(_pdf_bytes(size=(500, 400)))

    assert pixels.shape[1] == 500


def test_an_oversized_scan_is_downscaled_into_the_tuned_regime() -> None:
    """A 300 dpi letter page is ~2550 px; the thresholds were tuned near 919."""
    pixels = rasterise_first_page(_pdf_bytes(size=(2400, 1800)))

    assert pixels.shape[1] == 1000
    assert pixels.shape[0] == pytest.approx(750, abs=2)


def test_a_multi_page_pdf_is_refused_rather_than_half_digitised() -> None:
    """Publishing page 1 of five as 'the well log' would silently drop the rest."""
    with pytest.raises(ValueError, match="has 3 pages"):
        rasterise_first_page(_pdf_bytes(pages=3))


def test_empty_bytes_are_refused() -> None:
    with pytest.raises(ValueError, match="0 bytes"):
        rasterise_first_page(b"")
