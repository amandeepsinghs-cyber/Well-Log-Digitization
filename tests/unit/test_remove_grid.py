"""Tests for grid removal.

The property that matters is asymmetric: erasing too little is a nuisance,
erasing too much is data loss. A grid remover that punches through curves at
every crossing would hand the tracer a curve full of holes, and those holes
would be written into the LAS as NULL gaps — reported as missing data that was
never actually missing.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.contracts import GridLines
from app.detect.gridlines import detect_gridlines
from app.ingest.load_image import FORMAT_RGB, from_array, to_array
from app.preprocess.remove_grid import grid_pixel_count, remove_grid


@pytest.fixture(scope="module")
def scan_image(scan_rgb: np.ndarray):
    return from_array(scan_rgb, FORMAT_RGB)


@pytest.fixture(scope="module")
def scan_grid(scan_image):
    return detect_gridlines(scan_image)


# -- On the real scan ---------------------------------------------------------

def test_grid_rows_become_paper(scan_image, scan_grid) -> None:
    """After removal, a grid row inside a track should be mostly blank."""
    cleaned = to_array(remove_grid(scan_image, scan_grid))
    # Row 464 is a depth grid line; columns 660-890 are inside Track 3.
    row = cleaned[464, 660:890].mean(axis=1)
    pale = np.count_nonzero(row > 240)
    assert pale > row.size * 0.6, "the grid line is still visible"


def test_curves_survive_their_crossings(scan_image, scan_grid) -> None:
    """Dark ink must be left untouched wherever it crosses a rule.

    Counted over every grid row at once: if crossings were being erased, the
    total dark-pixel count on those rows would collapse.
    """
    before = to_array(scan_image)
    after = to_array(remove_grid(scan_image, scan_grid))

    rows = list(scan_grid.depth_grid_rows)
    dark_before = np.count_nonzero(before[rows, :, :].mean(axis=2) < 120)
    dark_after = np.count_nonzero(after[rows, :, :].mean(axis=2) < 120)

    assert dark_after >= dark_before * 0.98, (
        f"dark ink on grid rows fell from {dark_before} to {dark_after}"
    )


def test_the_image_elsewhere_is_untouched(scan_image, scan_grid) -> None:
    """Only rows and columns named in GridLines may change."""
    before = to_array(scan_image)
    after = to_array(remove_grid(scan_image, scan_grid))

    changed_rows = set(np.where((before != after).any(axis=(1, 2)))[0].tolist())
    allowed = {
        row + offset for row in scan_grid.depth_grid_rows for offset in (-1, 0, 1)
    }
    # Columns are erased too, so a changed row may be a separator column's doing.
    unexplained = changed_rows - allowed
    if unexplained:
        columns = {
            column + offset
            for column in scan_grid.track_separators
            for offset in (-1, 0, 1)
        }
        for row in unexplained:
            differing = set(np.where((before[row] != after[row]).any(axis=1))[0].tolist())
            assert differing <= columns, f"row {row} changed outside any rule"


def test_removal_does_not_mutate_the_input(scan_image, scan_grid) -> None:
    original = to_array(scan_image).copy()
    remove_grid(scan_image, scan_grid)
    np.testing.assert_array_equal(to_array(scan_image), original)


def test_grid_pixel_count_is_substantial(scan_image, scan_grid) -> None:
    """A tiny count would mean the rules were mis-located."""
    count = grid_pixel_count(scan_image, scan_grid)
    # 16 rows across ~900 px plus 5 columns down ~780 px, three pixels thick.
    assert count > 20_000, f"only {count} grid pixels found"


# -- Controlled cases ---------------------------------------------------------

def test_a_curve_crossing_a_grid_line_keeps_its_pixel() -> None:
    sheet = np.full((50, 50, 3), 255, dtype=np.uint8)
    sheet[25, :] = 200          # a pale grid row
    sheet[25, 30] = 0           # a black curve crossing it
    grid = GridLines(
        track_separators=(),
        heavy_horizontals=(),
        depth_grid_rows=(25,),
        grid_spacing_px=25.0,
    )

    after = to_array(remove_grid(from_array(sheet, FORMAT_RGB), grid))

    assert after[25, 30].tolist() == [0, 0, 0], "the curve pixel was erased"
    assert after[25, 10].tolist() == [255, 255, 255], "the grid was not erased"


def test_a_coloured_curve_crossing_is_kept() -> None:
    """A green GR curve is bright in one channel; brightness alone must not fool it."""
    sheet = np.full((50, 50, 3), 255, dtype=np.uint8)
    sheet[25, :] = 200
    sheet[25, 30] = [76, 124, 47]   # the scan's GR green
    grid = GridLines(
        track_separators=(), heavy_horizontals=(), depth_grid_rows=(25,), grid_spacing_px=25.0
    )

    after = to_array(remove_grid(from_array(sheet, FORMAT_RGB), grid))
    assert after[25, 30].tolist() == [76, 124, 47]


def test_separator_columns_are_erased_where_pale() -> None:
    sheet = np.full((50, 50, 3), 255, dtype=np.uint8)
    sheet[:, 10] = 200
    grid = GridLines(
        track_separators=(10,), heavy_horizontals=(), depth_grid_rows=(), grid_spacing_px=0.0
    )
    after = to_array(remove_grid(from_array(sheet, FORMAT_RGB), grid))
    assert (after[:, 10] == 255).all()


def test_lines_at_the_image_edge_do_not_overrun() -> None:
    """The half-width expansion must not index outside the array."""
    sheet = np.full((20, 20, 3), 200, dtype=np.uint8)
    grid = GridLines(
        track_separators=(0, 19),
        heavy_horizontals=(),
        depth_grid_rows=(0, 19),
        grid_spacing_px=19.0,
    )
    remove_grid(from_array(sheet, FORMAT_RGB), grid)  # must not raise
