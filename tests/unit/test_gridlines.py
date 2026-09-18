"""Tests for rule-line detection on the real scan.

The numbers asserted here were measured from `Well_log_schlum.jpg` before the
detector was written, so these are not the detector's own output fed back as a
test. If the detector regresses, these fail against independently known truth.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.contracts import RasterImage
from app.detect.gridlines import detect_gridlines
from app.ingest.load_image import FORMAT_RGB, from_array

# Measured from the source scan by inspecting its ink profiles.
EXPECTED_SEPARATORS = (8, 285, 357, 635, 912)
EXPECTED_DATA_TOP = 194
EXPECTED_DATA_BOTTOM = 772
EXPECTED_GRID_COUNT = 16


@pytest.fixture(scope="module")
def grid(scan_rgb: np.ndarray):
    return detect_gridlines(from_array(scan_rgb, FORMAT_RGB))


# -- Vertical structure -------------------------------------------------------

def test_finds_every_track_separator(grid) -> None:
    """Five heavy rules bound four columns: Track 1, depth, Track 2, Track 3."""
    assert grid.track_separators == EXPECTED_SEPARATORS


def test_separators_bound_four_columns(grid) -> None:
    assert len(grid.track_separators) - 1 == 4


def test_the_narrow_column_is_not_leftmost(grid) -> None:
    """The depth column is the narrow one, and on this sheet it is 2nd of 4.

    Pinned because assuming the depth axis is leftmost is the single most likely
    wrong assumption in this phase.
    """
    edges = grid.track_separators
    widths = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
    assert widths.index(min(widths)) == 1, f"column widths were {widths}"


# -- Horizontal structure -----------------------------------------------------

def test_finds_the_data_area_frame(grid) -> None:
    """The top and bottom of the data area are heavy, full-width rules."""
    assert EXPECTED_DATA_TOP in grid.heavy_horizontals
    assert EXPECTED_DATA_BOTTOM in grid.heavy_horizontals


def test_depth_grid_has_every_line(grid) -> None:
    assert len(grid.depth_grid_rows) == EXPECTED_GRID_COUNT


def test_depth_grid_is_evenly_spaced(grid) -> None:
    """Even spacing is what makes the grid usable for depth calibration.

    It is also the evidence the sheet is undistorted — step 44's guard rests on
    this, so a regression here would quietly weaken that check too.
    """
    gaps = np.diff(np.asarray(grid.depth_grid_rows, dtype=float))
    assert gaps.std() < 1.0, f"spacings were {gaps.tolist()}"
    assert grid.grid_spacing_px == pytest.approx(38.5, abs=1.0)


def test_depth_grid_spans_the_data_area_not_the_header(grid) -> None:
    """Header box rules must not be mistaken for depth grid.

    They survive the light-ink pass because they are full-width rules, and an
    earlier version of the filter accepted three of them. Their rows sit above
    194, so the grid starting at the data top is the check that they are gone.
    """
    assert grid.depth_grid_rows[0] == EXPECTED_DATA_TOP
    assert grid.depth_grid_rows[-1] == EXPECTED_DATA_BOTTOM - 1
    assert all(row >= EXPECTED_DATA_TOP for row in grid.depth_grid_rows)


def test_grid_intervals_divide_the_logged_depth_range(grid) -> None:
    """15 intervals over 7,000-7,300 ft is one line per 20 ft — a round number.

    A non-integer result would mean lines are missing or spurious ones crept in.
    """
    intervals = len(grid.depth_grid_rows) - 1
    feet_per_interval = 300.0 / intervals
    assert feet_per_interval == pytest.approx(20.0)


# -- Failing loudly -----------------------------------------------------------

def test_a_blank_sheet_raises_rather_than_inventing_tracks() -> None:
    """No rules means this is not a gridded log, and guessing would fabricate."""
    blank = np.full((400, 400, 3), 255, dtype=np.uint8)
    with pytest.raises(ValueError, match="at least 2 are needed"):
        detect_gridlines(from_array(blank, FORMAT_RGB))


def test_a_single_rule_is_not_enough_for_a_track() -> None:
    sheet = np.full((400, 400, 3), 255, dtype=np.uint8)
    sheet[:, 100] = 0  # one vertical rule
    with pytest.raises(ValueError, match="Found 1 vertical rule"):
        detect_gridlines(from_array(sheet, FORMAT_RGB))


def test_a_synthetic_grid_is_measured_correctly() -> None:
    """A controlled sheet where the right answer is known by construction."""
    sheet = np.full((300, 400, 3), 255, dtype=np.uint8)
    # Reading grid first, then the heavy rules on top — the order a real sheet
    # is printed in. Drawn the other way round the grid would chop each
    # separator into short segments and none would be detected.
    for row in range(50, 260, 30):
        sheet[row, 20:380] = 200        # light reading grid, every 30 px
    for column in (20, 200, 380):
        sheet[:, column] = 0            # heavy separators

    grid = detect_gridlines(from_array(sheet, FORMAT_RGB))

    assert grid.track_separators == (20, 200, 380)
    assert grid.grid_spacing_px == pytest.approx(30.0)
    assert len(grid.depth_grid_rows) == 7


def test_a_greyscale_sheet_is_accepted() -> None:
    """Line detection must not require colour; it greyscales internally."""
    sheet = np.full((300, 400), 255, dtype=np.uint8)
    for column in (20, 200, 380):
        sheet[:, column] = 0
    image = RasterImage(
        data=sheet.tobytes(), width=400, height=300, channels=1, format="GRAY"
    )
    assert detect_gridlines(image).track_separators == (20, 200, 380)
