"""Tests for the depth fit, using the geometry measured on the reference scan.

The scan labels 7,000 / 7,100 / 7,200 / 7,300 on grid rows 194 / 387 / 579 /
771. That is 300 ft over 577 px, so 0.51993 ft per pixel, worked out
independently of the code under test.
"""

from __future__ import annotations

import pytest

from app.calibrate.depth_axis import depth_at_row, fit_depth_axis
from app.contracts import ColumnSpan, DepthTick, PageLayout

LAYOUT = PageLayout(
    data_top=194,
    data_bottom=772,
    tracks=(ColumnSpan("Track 1", 8, 285),),
    depth_column=ColumnSpan("Depth", 285, 357),
    header_rows=(13, 194),
)

SCAN_TICKS = (
    DepthTick(grid_row=194, label_top=202, label_bottom=215),
    DepthTick(grid_row=387, label_top=382, label_bottom=396),
    DepthTick(grid_row=579, label_top=576, label_bottom=590),
    DepthTick(grid_row=771, label_top=754, label_bottom=767),
)
SCAN_DEPTHS = (7000.0, 7100.0, 7200.0, 7300.0)

EXPECTED_FT_PER_PIXEL = 300.0 / (771 - 194)


@pytest.fixture
def calibration():
    return fit_depth_axis(SCAN_TICKS, SCAN_DEPTHS, "FT", LAYOUT)


def test_scale_matches_the_measured_geometry(calibration) -> None:
    assert calibration.depth_per_pixel == pytest.approx(EXPECTED_FT_PER_PIXEL, abs=1e-3)


def test_the_fit_is_almost_exact_on_this_scan(calibration) -> None:
    """The four ticks are collinear to well under a foot.

    This is the number step 44 judges a scan on, so a regression here would
    quietly weaken that gate rather than fail anything visibly.
    """
    assert calibration.fit_rmse < 0.5


def test_labelled_rows_come_back_as_their_labels(calibration) -> None:
    for tick, depth in zip(SCAN_TICKS, SCAN_DEPTHS):
        assert depth_at_row(calibration, tick.grid_row) == pytest.approx(depth, abs=0.5)


def test_display_range_spans_the_data_area(calibration) -> None:
    """Curves are plotted to the frame, past the last labelled line at row 771."""
    assert calibration.depth_min == pytest.approx(7000.0, abs=0.5)
    assert calibration.depth_max > 7300.0


def test_units_are_carried_through(calibration) -> None:
    assert calibration.depth_units == "FT"


def test_every_tick_contributes_not_just_the_ends() -> None:
    """One misread middle label must move the fit, or it is not a fit.

    If the code silently used only the first and last tick, this would pass
    unchanged — which is exactly the failure this asserts against.
    """
    bad = (7000.0, 7150.0, 7200.0, 7300.0)
    shifted = fit_depth_axis(SCAN_TICKS, bad, "FT", LAYOUT)
    clean = fit_depth_axis(SCAN_TICKS, SCAN_DEPTHS, "FT", LAYOUT)
    assert shifted.fit_rmse > clean.fit_rmse
    assert shifted.depth_per_pixel != clean.depth_per_pixel


def test_a_count_mismatch_is_refused() -> None:
    with pytest.raises(ValueError, match="must correspond one to one"):
        fit_depth_axis(SCAN_TICKS, (7000.0, 7100.0), "FT", LAYOUT)


def test_a_single_tick_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be fitted to one point"):
        fit_depth_axis(SCAN_TICKS[:1], SCAN_DEPTHS[:1], "FT", LAYOUT)


def test_depth_decreasing_downward_is_refused() -> None:
    """Ticks paired with reversed labels would write the log upside down."""
    with pytest.raises(ValueError, match="depth would decrease down the page"):
        fit_depth_axis(SCAN_TICKS, tuple(reversed(SCAN_DEPTHS)), "FT", LAYOUT)


def test_metric_logs_fit_the_same_way() -> None:
    depths = (2133.6, 2164.1, 2194.6, 2225.0)
    calibration = fit_depth_axis(SCAN_TICKS, depths, "M", LAYOUT)
    assert calibration.depth_units == "M"
    assert calibration.depth_per_pixel == pytest.approx(
        (depths[-1] - depths[0]) / (771 - 194), abs=1e-3
    )
