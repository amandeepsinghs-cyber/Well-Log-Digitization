"""Tests for the calibration gate.

The reference scan must pass cleanly: it is a flat digital figure with an evenly
printed grid. The interesting cases are the distortions it does not have, which
are built synthetically here — that is the whole reason deskew and rectify were
deferred in favour of a refusal.
"""

from __future__ import annotations

import pytest

from app.calibrate.depth_axis import fit_depth_axis
from app.calibrate.validate_calibration import blocking_findings, validate_calibration
from app.contracts import (
    ColumnSpan,
    DepthCalibration,
    DepthTick,
    FindingSeverity,
    GridLines,
    PageLayout,
)
from app.detect.depth_ticks import detect_depth_ticks
from app.detect.gridlines import detect_gridlines
from app.detect.regions import detect_regions
from app.ingest.load_image import FORMAT_RGB, from_array

SCAN_DEPTHS = (7000.0, 7100.0, 7200.0, 7300.0)


@pytest.fixture(scope="module")
def scan_calibration(scan_rgb):
    """Everything step 44 sees, derived from the real scan end to end."""
    image = from_array(scan_rgb, FORMAT_RGB)
    grid = detect_gridlines(image)
    layout = detect_regions(image, grid)
    ticks = detect_depth_ticks(image, layout, grid)
    depth = fit_depth_axis(ticks, SCAN_DEPTHS, "FT", layout)
    return depth, grid


def _grid(rows, spacing) -> GridLines:
    return GridLines(
        track_separators=(8, 285, 357, 635, 912),
        heavy_horizontals=(194, 772),
        depth_grid_rows=tuple(rows),
        grid_spacing_px=spacing,
    )


def _depth(rmse: float, per_pixel: float = 0.52) -> DepthCalibration:
    return DepthCalibration(
        depth_min=7000.0,
        depth_max=7300.0,
        depth_units="FT",
        depth_per_pixel=per_pixel,
        y_origin_depth=6899.0,
        fit_rmse=rmse,
    )


def _severities(findings) -> set:
    return {f.severity for f in findings}


# -- The reference scan -------------------------------------------------------

def test_the_reference_scan_passes(scan_calibration) -> None:
    """Gate C depends on this scan being digitisable at all."""
    findings = validate_calibration(*scan_calibration)
    assert blocking_findings(findings) == ()


def test_the_reference_scan_raises_no_warnings_either(scan_calibration) -> None:
    findings = validate_calibration(*scan_calibration)
    assert FindingSeverity.WARNING not in _severities(findings)


def test_the_depth_resolution_is_always_reported(scan_calibration) -> None:
    """The finest honest sample step belongs in the audit trail."""
    findings = validate_calibration(*scan_calibration)
    resolution = [f for f in findings if "One pixel is" in f.message]
    assert len(resolution) == 1
    assert resolution[0].severity is FindingSeverity.INFO
    assert "FT" in resolution[0].message


# -- The depth fit ------------------------------------------------------------

def test_a_fit_within_a_pixel_is_not_flagged() -> None:
    grid = _grid(range(194, 772, 38), 38.0)
    findings = validate_calibration(_depth(rmse=0.4), grid)
    assert _severities(findings) == {FindingSeverity.INFO}


def test_a_fit_off_by_two_pixels_warns() -> None:
    grid = _grid(range(194, 772, 38), 38.0)
    findings = validate_calibration(_depth(rmse=1.04), grid)
    assert FindingSeverity.WARNING in _severities(findings)
    assert blocking_findings(findings) == ()


def test_a_fit_off_by_five_pixels_blocks() -> None:
    """At that point a depth label was misread, and every depth is wrong."""
    grid = _grid(range(194, 772, 38), 38.0)
    findings = validate_calibration(_depth(rmse=2.6), grid)
    assert len(blocking_findings(findings)) == 1
    assert "misread" in blocking_findings(findings)[0].message


def test_the_fit_limit_is_in_pixels_not_depth_units() -> None:
    """The same pixel error on a metre log must be judged the same way.

    Expressing the limit in feet would make a metric log look three times
    better than an identical imperial one.
    """
    grid = _grid(range(194, 772, 38), 38.0)
    imperial = validate_calibration(_depth(rmse=2.6, per_pixel=0.52), grid)
    metric = validate_calibration(_depth(rmse=0.79, per_pixel=0.158), grid)
    assert _severities(imperial) == _severities(metric)


# -- Grid regularity ----------------------------------------------------------

def test_a_gap_where_a_fill_hides_a_grid_line_is_not_a_defect() -> None:
    """A double-width gap is expected: the line is under a colour fill."""
    rows = [194, 232, 270, 346, 384, 422]  # 270 -> 346 is two pitches
    findings = validate_calibration(_depth(rmse=0.4), _grid(rows, 38.0))
    assert _severities(findings) == {FindingSeverity.INFO}


def test_an_irregular_grid_warns() -> None:
    rows = [194, 232, 267, 305, 343, 381]  # one gap of 35 against a 38 pitch
    findings = validate_calibration(_depth(rmse=0.4), _grid(rows, 38.0))
    assert FindingSeverity.WARNING in _severities(findings)


def test_a_badly_irregular_grid_blocks() -> None:
    rows = [194, 232, 262, 300, 338, 376]  # one gap of 30 against a 38 pitch
    findings = validate_calibration(_depth(rmse=0.4), _grid(rows, 38.0))
    assert any("not evenly spaced" in f.message for f in blocking_findings(findings))


# -- Perspective --------------------------------------------------------------

def test_a_pitch_that_drifts_down_the_page_blocks() -> None:
    """The signature of a photograph taken at an angle, not a flat scan.

    A straight depth fit would read correctly at the top and drift at the
    bottom, so the sheet is refused rather than silently half-digitised.
    """
    rows = [0]
    for gap in (34, 35, 36, 38, 40, 41, 42, 43):
        rows.append(rows[-1] + gap)
    findings = validate_calibration(_depth(rmse=0.4), _grid(rows, 38.0))
    blocking = blocking_findings(findings)
    assert any("photographed at an angle" in f.message for f in blocking)


def test_even_rounding_jitter_is_not_perspective() -> None:
    """A real sheet alternates 38 and 39 px purely from pixel rounding."""
    rows = [194]
    for gap in (39, 38, 39, 38, 39, 38, 39, 38):
        rows.append(rows[-1] + gap)
    findings = validate_calibration(_depth(rmse=0.4), _grid(rows, 38.0))
    assert blocking_findings(findings) == ()


def test_too_little_grid_to_judge_is_not_an_error() -> None:
    """Two lines cannot show a trend; the fit check still applies."""
    findings = validate_calibration(_depth(rmse=0.4), _grid([194, 232], 38.0))
    assert _severities(findings) == {FindingSeverity.INFO}


# -- Wiring -------------------------------------------------------------------

def test_findings_carry_the_depth_interval() -> None:
    """A petrophysicist reads findings against depths, not pixel rows."""
    findings = validate_calibration(_depth(rmse=2.6), _grid(range(194, 772, 38), 38.0))
    assert all(f.depth_interval == (7000.0, 7300.0) for f in findings)


def test_blocking_findings_selects_only_errors() -> None:
    grid = _grid([194, 232, 262, 300, 338, 376], 38.0)
    findings = validate_calibration(_depth(rmse=2.6), grid)
    assert len(findings) > len(blocking_findings(findings))
    assert all(
        f.severity is FindingSeverity.ERROR for f in blocking_findings(findings)
    )
