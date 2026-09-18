"""Tests for the scan-inspection tool.

The image bytes and the language-model call are both replaced, so these test the
wiring: that the chain runs in the right order, that the report says what the
sheet actually contains, and that a failure anywhere comes back as a message
rather than an exception that would cost the agent its turn.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.header import ocr_header
from app.integration import tools

SCAN_REPLY = {
    "curves": [
        {"track_name": "Track 1", "title": "Gamma Ray", "scale": "0 gAPI 150"},
        {
            "track_name": "Track 1",
            "title": "Spontaneous Potential",
            "scale": "-80 mV 20",
        },
        {
            "track_name": "Track 2",
            "title": "Resistivity, Shallow",
            "scale": "0.2 ohm.m 20",
        },
        {
            "track_name": "Track 2",
            "title": "Resistivity, Medium",
            "scale": "0.2 ohm.m 20",
        },
        {"track_name": "Track 2", "title": "Resistivity, Deep", "scale": "0.2 ohm.m 20"},
        {"track_name": "Track 3", "title": "Neutron Porosity", "scale": "45 % -15"},
        {"track_name": "Track 3", "title": "Bulk Density", "scale": "1.90 g/cm³ 2.90"},
    ],
    "depth_header": "Depth, ft",
    "depth_labels": ["7,000", "7,100", "7,200", "7,300"],
}


@pytest.fixture
def report(monkeypatch, scan_bytes: bytes):
    """Run the tool over the real scan with GCS and the model replaced."""
    monkeypatch.setattr(tools, "read_bytes", lambda name: scan_bytes)
    monkeypatch.setattr(ocr_header, "_call_model", lambda parts: SCAN_REPLY)
    return tools.inspect_scanned_log("Scanned Well Logs/Well_log_schlum.jpg")


# -- The Gate 2 assertions ----------------------------------------------------

def test_the_sheet_has_three_tracks(report) -> None:
    assert report["track_count"] == 3


def test_the_sheet_has_seven_curves(report) -> None:
    assert report["curve_count"] == 7


def test_the_depth_range_is_read_from_the_page(report) -> None:
    low, high = report["depth_range"]
    assert low == pytest.approx(7000.0, abs=1.0)
    assert high == pytest.approx(7300.0, abs=2.0)
    assert report["depth_units"] == "FT"


def test_track_two_is_identified_as_logarithmic(report) -> None:
    """Derived from the printed scale "0.2 ohm.m 20", not from the track number."""
    tracks = {t["name"]: t for t in report["tracks"]}
    assert tracks["Track 2"]["scale_type"] == "LOGARITHMIC"
    assert tracks["Track 1"]["scale_type"] == "LINEAR"
    assert tracks["Track 3"]["scale_type"] == "LINEAR"


def test_the_scan_is_digitisable(report) -> None:
    assert report["ok"] is True
    assert report["digitisable"] is True


# -- What else the report carries ---------------------------------------------

def test_every_curve_is_reported_with_its_scale(report) -> None:
    curves = {
        curve["mnemonic"]: curve
        for track in report["tracks"]
        for curve in track["curves"]
    }
    assert set(curves) == {"GR", "SP", "RXO", "ILM", "ILD", "NPHI", "RHOB"}
    assert curves["ILD"]["scale"] == [0.2, 20.0]
    # Printed as a percentage, recorded as the fraction LAS expects.
    assert curves["NPHI"] == {"mnemonic": "NPHI", "unit": "V/V", "scale": [0.45, -0.15]}


def test_the_depth_resolution_is_reported(report) -> None:
    """A LAS step finer than one pixel would be invented detail."""
    assert report["depth_resolution"] == pytest.approx(0.52, abs=0.01)


def test_tracks_carry_their_pixel_columns(report) -> None:
    tracks = {t["name"]: t for t in report["tracks"]}
    assert tracks["Track 2"]["pixel_columns"] == [357, 635]


def test_the_source_uri_is_reported(report) -> None:
    assert report["source"].startswith("gs://")
    assert report["source"].endswith("Well_log_schlum.jpg")


# -- Failure --------------------------------------------------------------

def test_a_missing_object_is_reported_not_raised(monkeypatch) -> None:
    """An ADK tool that raises costs the agent its whole turn."""
    def missing(name):
        raise FileNotFoundError(f"No such object: {name}")

    monkeypatch.setattr(tools, "read_bytes", missing)
    report = tools.inspect_scanned_log("Scanned Well Logs/nope.jpg")
    assert report["ok"] is False
    assert "FileNotFoundError" in report["error"]


def test_an_unreadable_sheet_is_reported_not_raised(monkeypatch) -> None:
    """Blank paper has no rules, so gridline detection refuses it."""
    import cv2

    blank = np.full((400, 400, 3), 255, dtype=np.uint8)
    monkeypatch.setattr(
        tools, "read_bytes", lambda name: cv2.imencode(".png", blank)[1].tobytes()
    )
    report = tools.inspect_scanned_log("Scanned Well Logs/blank.png")
    assert report["ok"] is False
    assert "ValueError" in report["error"]
