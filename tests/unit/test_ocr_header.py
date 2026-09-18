"""Tests for the sheet transcription step.

The language-model call itself is replaced here. What is tested is everything
around it: that the right crops are sent, and that a reply which is wrong in a
way no later stage would notice is rejected rather than passed on. The live call
is exercised separately against the real scan, because it costs a request and
its exact wording is not deterministic.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.contracts import ColumnSpan, DepthTick, PageLayout
from app.header import ocr_header
from app.ingest.load_image import FORMAT_RGB, from_array

LAYOUT = PageLayout(
    data_top=100,
    data_bottom=300,
    tracks=(
        ColumnSpan("Track 1", 10, 200),
        ColumnSpan("Track 2", 260, 450),
    ),
    depth_column=ColumnSpan("Depth", 200, 260),
    header_rows=(20, 100),
)
TICKS = (
    DepthTick(grid_row=100, label_top=104, label_bottom=118),
    DepthTick(grid_row=200, label_top=194, label_bottom=208),
)

GOOD_REPLY = {
    "curves": [
        {"track_name": "Track 1", "title": "Gamma Ray", "scale": "0 gAPI 150"},
        {"track_name": "Track 2", "title": "Resistivity, Deep", "scale": "0.2 ohm.m 20"},
    ],
    "depth_header": "Depth, ft",
    "depth_labels": ["7,000", "7,100"],
}


@pytest.fixture
def image():
    sheet = np.full((400, 500, 3), 255, dtype=np.uint8)
    # Some ink so the crops are not uniformly blank.
    sheet[30:80, 20:180] = 0
    return from_array(sheet, FORMAT_RGB)


@pytest.fixture
def captured(monkeypatch):
    """Replace the model call, recording the prompt it was given."""
    record: dict = {}

    def fake_call(parts):
        record["parts"] = parts
        return record.get("reply", GOOD_REPLY)

    monkeypatch.setattr(ocr_header, "_call_model", fake_call)
    return record


# -- What gets sent -----------------------------------------------------------

def test_one_image_per_region_is_sent(image, captured) -> None:
    """Two tracks, one depth header and two labels: five images, one call."""
    ocr_header.read_sheet_text(image, LAYOUT, TICKS)
    images = [p for p in captured["parts"] if p.inline_data is not None]
    assert len(images) == 5


def test_every_image_is_introduced_by_name(image, captured) -> None:
    """The model cannot see where a crop came from, so the text part says.

    Without this the model has no basis for the track_name it returns.
    """
    ocr_header.read_sheet_text(image, LAYOUT, TICKS)
    texts = [p.text for p in captured["parts"] if p.text]
    assert "Header block above Track 1:" in texts
    assert "Header block above Track 2:" in texts
    assert "Header of the depth column:" in texts
    assert "Depth label 1 of 2:" in texts


def test_crops_are_sent_as_png(image, captured) -> None:
    """Re-encoding text as JPEG blurs the strokes the model has to read."""
    ocr_header.read_sheet_text(image, LAYOUT, TICKS)
    images = [p for p in captured["parts"] if p.inline_data is not None]
    assert {p.inline_data.mime_type for p in images} == {"image/png"}


def test_a_label_crop_at_the_top_edge_does_not_fail(captured) -> None:
    """The margin around a label can run off the sheet; clamping is required."""
    sheet = np.full((400, 500, 3), 255, dtype=np.uint8)
    layout = PageLayout(
        data_top=0,
        data_bottom=300,
        tracks=(ColumnSpan("Track 1", 10, 200),),
        depth_column=ColumnSpan("Depth", 200, 260),
        header_rows=(0, 1),
    )
    ticks = (DepthTick(grid_row=0, label_top=1, label_bottom=10),)
    captured["reply"] = {
        "curves": [{"track_name": "Track 1", "title": "GR", "scale": "0 gAPI 150"}],
        "depth_header": "Depth, ft",
        "depth_labels": ["7,000"],
    }
    text = ocr_header.read_sheet_text(from_array(sheet, FORMAT_RGB), layout, ticks)
    assert text.depth_labels == ("7,000",)


# -- What comes back ----------------------------------------------------------

def test_a_good_reply_is_returned_verbatim(image, captured) -> None:
    """Nothing is normalised here — "0.2 ohm.m 20" stays a string."""
    text = ocr_header.read_sheet_text(image, LAYOUT, TICKS)
    assert text.depth_header == "Depth, ft"
    assert text.depth_labels == ("7,000", "7,100")
    assert text.curves[1].scale == "0.2 ohm.m 20"
    assert text.curves[1].track_name == "Track 2"


def test_surrounding_whitespace_is_stripped(image, captured) -> None:
    captured["reply"] = {
        "curves": [
            {"track_name": "Track 1", "title": "  Gamma Ray ", "scale": " 0 gAPI 150 "}
        ],
        "depth_header": " Depth, ft ",
        "depth_labels": [" 7,000 ", "7,100"],
    }
    text = ocr_header.read_sheet_text(image, LAYOUT, TICKS)
    assert text.curves[0].title == "Gamma Ray"
    assert text.depth_labels[0] == "7,000"


def test_a_label_count_mismatch_is_refused(image, captured) -> None:
    """The dangerous case: labels pair with rows by position.

    One missing label shifts every depth onto the wrong grid line, and every
    value in the LAS file would then be filed at the wrong depth while looking
    perfectly reasonable.
    """
    captured["reply"] = dict(GOOD_REPLY, depth_labels=["7,000"])
    with pytest.raises(ValueError, match="depth label image"):
        ocr_header.read_sheet_text(image, LAYOUT, TICKS)


def test_an_unknown_track_name_is_refused(image, captured) -> None:
    captured["reply"] = dict(
        GOOD_REPLY,
        curves=[{"track_name": "Track 9", "title": "GR", "scale": "0 gAPI 150"}],
    )
    with pytest.raises(ValueError, match="not a track on this sheet"):
        ocr_header.read_sheet_text(image, LAYOUT, TICKS)


def test_no_curves_read_is_refused(image, captured) -> None:
    captured["reply"] = dict(GOOD_REPLY, curves=[])
    with pytest.raises(ValueError, match="no curve headers"):
        ocr_header.read_sheet_text(image, LAYOUT, TICKS)
