"""Tests for locating the printed depth labels and pairing them with grid lines.

Measured from `Well_log_schlum.jpg` independently of the code under test: four
labels, at rows 202-215, 382-396, 576-590 and 754-767, marking the grid lines at
194, 387, 579 and 771.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.contracts import ColumnSpan, GridLines, PageLayout
from app.detect.depth_ticks import detect_depth_ticks
from app.detect.gridlines import detect_gridlines
from app.detect.regions import detect_regions
from app.ingest.load_image import FORMAT_RGB, from_array

# The scan labels 7,000 / 7,100 / 7,200 / 7,300 only.
EXPECTED_TICK_COUNT = 4
EXPECTED_GRID_ROWS = (194, 387, 579, 771)
EXPECTED_LABEL_SPANS = ((202, 215), (382, 396), (576, 590), (754, 767))


@pytest.fixture(scope="module")
def scan_parts(scan_rgb: np.ndarray):
    image = from_array(scan_rgb, FORMAT_RGB)
    grid = detect_gridlines(image)
    return image, detect_regions(image, grid), grid


@pytest.fixture(scope="module")
def ticks(scan_parts):
    return detect_depth_ticks(*scan_parts)


def _synthetic(label_rows: list[tuple[int, int]], grid_rows=(100, 150, 200, 250, 300)):
    """A white sheet with black bars standing in for depth labels.

    A bar 20 px wide in a 60 px column is text-like: it is neither the full
    column width nor a single row, so it survives the same filters as a numeral.
    """
    sheet = np.full((400, 700, 3), 255, dtype=np.uint8)
    for top, bottom in label_rows:
        sheet[top : bottom + 1, 30:50] = 0

    layout = PageLayout(
        data_top=90,
        data_bottom=320,
        tracks=(ColumnSpan("Track 1", 80, 400),),
        depth_column=ColumnSpan("Depth", 20, 80),
        header_rows=(10, 90),
    )
    grid = GridLines(
        track_separators=(20, 80, 400),
        heavy_horizontals=(90, 320),
        depth_grid_rows=tuple(grid_rows),
        grid_spacing_px=50.0,
    )
    return from_array(sheet, FORMAT_RGB), layout, grid


# -- Against the real scan ----------------------------------------------------

def test_finds_every_labelled_tick(ticks) -> None:
    assert len(ticks) == EXPECTED_TICK_COUNT


def test_ticks_sit_on_the_expected_grid_lines(ticks) -> None:
    assert tuple(tick.grid_row for tick in ticks) == EXPECTED_GRID_ROWS


def test_label_spans_match_the_printed_text(ticks) -> None:
    assert (
        tuple((tick.label_top, tick.label_bottom) for tick in ticks)
        == EXPECTED_LABEL_SPANS
    )


def test_the_data_frame_is_not_read_as_a_label(ticks) -> None:
    """Solid rules cross the depth column at rows 194 and 772.

    They are ink the full width of the column and would be collected as two
    extra labels, inventing depths at both ends of the log.
    """
    assert all(tick.label_top > 194 for tick in ticks)
    assert all(tick.label_bottom < 772 for tick in ticks)


def test_end_labels_are_offset_from_their_own_grid_lines(ticks) -> None:
    """The reason DepthTick keeps grid_row and label rows apart.

    7,000 is printed 14 px below its line and 7,300 11 px above its own, both to
    stay inside the frame. Calibrating on label centres would bend the scale.
    """
    first, last = ticks[0], ticks[-1]
    assert (first.label_top + first.label_bottom) // 2 - first.grid_row == 14
    assert last.grid_row - (last.label_top + last.label_bottom) // 2 == 11


def test_middle_labels_sit_on_their_grid_lines(ticks) -> None:
    """Interior labels have room to be centred, so the offset is a few pixels."""
    for tick in ticks[1:-1]:
        centre = (tick.label_top + tick.label_bottom) // 2
        assert abs(centre - tick.grid_row) <= 5


def test_ticks_are_returned_shallowest_first(ticks) -> None:
    rows = [tick.grid_row for tick in ticks]
    assert rows == sorted(rows)


def test_labelled_lines_are_evenly_spaced_in_grid_steps(ticks, scan_parts) -> None:
    """Labels mark every 5th line: 100 ft labels on a 20 ft grid.

    Not enforced by the module, but if it drifted the depth fit in step 41 would
    be built on mismatched pairs, so it is worth pinning.
    """
    _, _, grid = scan_parts
    indices = [grid.depth_grid_rows.index(tick.grid_row) for tick in ticks]
    assert np.diff(indices).tolist() == [5, 5, 5]


# -- Synthetic edge cases -----------------------------------------------------

def test_text_below_the_end_of_the_grid_is_refused() -> None:
    """A footnote printed past the last grid line belongs to no depth.

    Attributing it to the nearest line would add a bogus pair to the depth fit
    and tilt every depth in the output.
    """
    image, layout, grid = _synthetic([(290, 302)], grid_rows=(100, 150, 200))
    with pytest.raises(ValueError, match="more than half"):
        detect_depth_ticks(image, layout, grid)


def test_two_labels_on_one_grid_line_are_refused() -> None:
    image, layout, grid = _synthetic([(94, 104), (108, 118)])
    with pytest.raises(ValueError, match="both fall on the grid line"):
        detect_depth_ticks(image, layout, grid)


def test_an_empty_depth_column_is_refused() -> None:
    image, layout, grid = _synthetic([])
    with pytest.raises(ValueError, match="No depth labels found"):
        detect_depth_ticks(image, layout, grid)


def test_speckle_is_not_a_label() -> None:
    """A run under 4 px tall is dirt, not a numeral, and is dropped silently."""
    image, layout, grid = _synthetic([(145, 155), (199, 200)])
    ticks = detect_depth_ticks(image, layout, grid)
    assert [tick.grid_row for tick in ticks] == [150]


def test_an_unmeasured_grid_spacing_is_refused() -> None:
    image, layout, grid = _synthetic([(145, 155)])
    broken = GridLines(
        track_separators=grid.track_separators,
        heavy_horizontals=grid.heavy_horizontals,
        depth_grid_rows=grid.depth_grid_rows,
        grid_spacing_px=0.0,
    )
    with pytest.raises(ValueError, match="no measured spacing"):
        detect_depth_ticks(image, layout, broken)
