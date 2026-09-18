"""Tests for decomposing the sheet into columns, data area and header block.

The pixel positions asserted here were measured from `Well_log_schlum.jpg`
independently of the code under test. The synthetic cases cover the structures
the reference sheet does not contain — a leftmost depth column, a sheet with no
narrow column at all — because those are where a wrong assumption would show.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.contracts import GridLines
from app.detect.gridlines import detect_gridlines
from app.detect.regions import detect_regions
from app.ingest.load_image import FORMAT_RGB, from_array

# Measured from the source scan.
EXPECTED_DATA_TOP = 194
EXPECTED_DATA_BOTTOM = 772
EXPECTED_DEPTH_COLUMN = (285, 357)
EXPECTED_TRACKS = ((8, 285), (357, 635), (635, 912))
# Row 13 is the top rule of the "Resistivity, Shallow" header box, which is the
# tallest header on the sheet and therefore the top of the header block.
EXPECTED_HEADER_TOP = 13


@pytest.fixture(scope="module")
def layout(scan_rgb: np.ndarray):
    image = from_array(scan_rgb, FORMAT_RGB)
    return detect_regions(image, detect_gridlines(image))


def _blank_sheet(height: int = 400, width: int = 800) -> np.ndarray:
    """White paper to draw synthetic layouts on."""
    return np.full((height, width, 3), 255, dtype=np.uint8)


def _grid(separators, heavy, depth_rows, spacing=20.0) -> GridLines:
    return GridLines(
        track_separators=tuple(separators),
        heavy_horizontals=tuple(heavy),
        depth_grid_rows=tuple(depth_rows),
        grid_spacing_px=spacing,
    )


# -- The data area ------------------------------------------------------------

def test_data_area_matches_the_scan(layout) -> None:
    assert (layout.data_top, layout.data_bottom) == (
        EXPECTED_DATA_TOP,
        EXPECTED_DATA_BOTTOM,
    )


def test_header_rules_are_not_mistaken_for_the_data_top(layout) -> None:
    """The sheet has heavy rules at 72 and 133, inside the header.

    They are indistinguishable from the data frame by span alone, so the only
    thing keeping them out is that they do not bracket the depth grid.
    """
    assert layout.data_top > 133


def test_data_area_is_bracketed_by_the_innermost_rules() -> None:
    """Given rules outside the data frame too, the innermost pair wins."""
    image = from_array(_blank_sheet(), FORMAT_RGB)
    # Draw some header ink so the header block can be found.
    sheet = _blank_sheet()
    sheet[10:20, 100:200] = 0
    image = from_array(sheet, FORMAT_RGB)

    grid = _grid(
        separators=[10, 300, 340, 600],
        heavy=[5, 50, 100, 300, 380],  # 5 and 380 are the outer margin rules
        depth_rows=[100, 120, 140, 160, 180, 200, 220, 240, 260, 280, 300],
    )
    layout = detect_regions(image, grid)
    assert (layout.data_top, layout.data_bottom) == (100, 300)


def test_no_rule_above_the_grid_is_refused() -> None:
    sheet = _blank_sheet()
    sheet[10:20, 100:200] = 0
    grid = _grid(
        separators=[10, 300, 340, 600],
        heavy=[200, 380],  # both below the first grid row
        depth_rows=[100, 120, 140],
    )
    with pytest.raises(ValueError, match="no heavy rule at or above"):
        detect_regions(from_array(sheet, FORMAT_RGB), grid)


def test_no_depth_grid_is_refused() -> None:
    grid = _grid(separators=[10, 300, 340, 600], heavy=[100, 300], depth_rows=[])
    with pytest.raises(ValueError, match="No depth grid rows"):
        detect_regions(from_array(_blank_sheet(), FORMAT_RGB), grid)


# -- The columns --------------------------------------------------------------

def test_finds_three_curve_tracks(layout) -> None:
    """Gate 2 requires the agent to report three tracks from the image alone."""
    assert len(layout.tracks) == 3


def test_track_bounds_match_the_scan(layout) -> None:
    assert tuple((t.x_left, t.x_right) for t in layout.tracks) == EXPECTED_TRACKS


def test_tracks_are_numbered_left_to_right_skipping_depth(layout) -> None:
    """Track 2 must be the resistivity column at 357-635, not the depth column.

    A log is described by track number, so an off-by-one here would attach the
    logarithmic resistivity scale to the wrong pixels.
    """
    assert [t.name for t in layout.tracks] == ["Track 1", "Track 2", "Track 3"]
    track_two = next(t for t in layout.tracks if t.name == "Track 2")
    assert (track_two.x_left, track_two.x_right) == (357, 635)


def test_depth_column_is_the_middle_one_on_this_sheet(layout) -> None:
    """The depth column is 2nd of 4, not leftmost. This is the phase's main trap."""
    assert (layout.depth_column.x_left, layout.depth_column.x_right) == (
        EXPECTED_DEPTH_COLUMN
    )
    assert layout.depth_column.x_left > layout.tracks[0].x_left


def test_depth_column_is_found_when_it_is_leftmost() -> None:
    """The reference sheet cannot test this, but most log sheets look like it."""
    sheet = _blank_sheet()
    sheet[10:20, 100:200] = 0
    grid = _grid(
        separators=[10, 70, 350, 630],  # the narrow column is now first
        heavy=[100, 300],
        depth_rows=[100, 150, 200, 250, 300],
    )
    layout = detect_regions(from_array(sheet, FORMAT_RGB), grid)
    assert (layout.depth_column.x_left, layout.depth_column.x_right) == (10, 70)
    assert [(t.x_left, t.x_right) for t in layout.tracks] == [(70, 350), (350, 630)]


def test_columns_of_equal_width_are_refused() -> None:
    """With no narrow column, which one holds the depths cannot be known.

    Guessing would put every depth in the output on the wrong scale, so the
    honest answer is to stop.
    """
    sheet = _blank_sheet()
    sheet[10:20, 100:200] = 0
    grid = _grid(
        separators=[10, 210, 410, 610],
        heavy=[100, 300],
        depth_rows=[100, 150, 200, 250, 300],
    )
    with pytest.raises(ValueError, match="No column is narrow enough"):
        detect_regions(from_array(sheet, FORMAT_RGB), grid)


def test_too_few_separators_is_refused() -> None:
    sheet = _blank_sheet()
    sheet[10:20, 100:200] = 0
    grid = _grid(separators=[10, 610], heavy=[100, 300], depth_rows=[100, 200, 300])
    with pytest.raises(ValueError, match="at most one column"):
        detect_regions(from_array(sheet, FORMAT_RGB), grid)


def test_column_width_is_the_distance_between_its_rules(layout) -> None:
    assert layout.depth_column.width == 72
    assert layout.tracks[0].width == 277


# -- The header block ---------------------------------------------------------

def test_header_block_reaches_the_tallest_header(layout) -> None:
    """Track 2 stacks three curve headers and starts higher than Tracks 1 and 3.

    Taking the union means a crop of Track 2's x-bounds contains all three; a
    band sized to Track 1 would silently cut "Resistivity, Shallow" off.
    """
    assert layout.header_rows == (EXPECTED_HEADER_TOP, EXPECTED_DATA_TOP)


def test_header_block_ends_where_the_data_begins(layout) -> None:
    assert layout.header_rows[1] == layout.data_top


def test_a_sheet_with_no_header_is_refused() -> None:
    """A blank area above the data means no printed scale, so no calibration."""
    grid = _grid(
        separators=[10, 70, 350, 630],
        heavy=[100, 300],
        depth_rows=[100, 150, 200, 250, 300],
    )
    with pytest.raises(ValueError, match="no header"):
        detect_regions(from_array(_blank_sheet(), FORMAT_RGB), grid)
