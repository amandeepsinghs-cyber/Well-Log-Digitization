"""Find which reading-grid lines carry a printed depth label, and where it is.

In : the RGB sheet, its PageLayout, and its GridLines.
Out: one DepthTick per printed label — the grid row it marks, and the rows the
     label text occupies.
Rule: geometry only. This locates the labels; header/ocr_header.py reads what
      they say. Keeping the two apart is what stops a model's reading of "7,100"
      from also deciding where 7,100 sits on the page.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

try:
    from app.contracts import DepthTick, GridLines, PageLayout, RasterImage
    from app.ingest.load_image import to_array
except ImportError:
    from contracts import DepthTick, GridLines, PageLayout, RasterImage
    from ingest.load_image import to_array

logger = logging.getLogger(__name__)

# Depth labels are printed as solid black numerals. Set above pure black to
# survive JPEG anti-aliasing, but below the pale reading grid (measured at
# 200-235 on the reference sheet) so a grid line crossing the depth column is
# not collected as a character.
_LABEL_INK_MAX = 150

# Rows this close together belong to the same numeral. A digit is a connected
# shape, but anti-aliasing can leave a one-row hole in a thin stroke.
_LABEL_ROW_GAP_PX = 2

# A run shorter than this is not text. Measured: the labels on the reference
# sheet are 14 rows tall; a printed rule is 1-3 rows and speckle is 1.
_MIN_LABEL_HEIGHT_PX = 4

# A label may be nudged off its own grid line to stay inside the frame — on the
# reference sheet by up to 37% of the pitch — but it must still belong to a line.
# Within the grid's span nothing can be further than half a pitch from the
# nearest line, so what this actually rejects is text lying OUTSIDE the grid's
# extent: a footnote or a stray mark printed below where the grid stops, which
# would otherwise be attributed to the last line and shift the depth scale.
_MAX_LABEL_OFFSET_FRACTION = 0.5


def detect_depth_ticks(
    image: RasterImage, layout: PageLayout, grid: GridLines
) -> tuple[DepthTick, ...]:
    """Pair each printed depth label with the grid line it marks.

    Args:
        image: the full RGB sheet.
        layout: the page decomposition, used for the depth column's x-bounds.
        grid: the reading-grid rows the labels are matched against.

    Returns:
        DepthTicks in depth order, shallowest first.

    Raises:
        ValueError: if no labels are found, if a label cannot be attributed to a
            grid line, or if two labels claim the same line. Each of these means
            the depth scale cannot be established, and a wrong depth scale makes
            every value in the LAS file wrong while looking entirely plausible.
    """
    if grid.grid_spacing_px <= 0:
        raise ValueError(
            "The reading grid has no measured spacing, so a label cannot be "
            f"attributed to a line. Grid rows were {grid.depth_grid_rows}."
        )

    blocks = _label_blocks(image, layout)
    if not blocks:
        raise ValueError(
            f"No depth labels found in the depth column, pixel columns "
            f"{layout.depth_column.x_left}-{layout.depth_column.x_right}, rows "
            f"{layout.data_top}-{layout.data_bottom}. Without labels the depth "
            "scale is unknown."
        )

    ticks = _attribute_to_grid(blocks, grid)

    logger.info(
        "detect_depth_ticks: OK - %d label(s) on grid rows %s",
        len(ticks),
        [tick.grid_row for tick in ticks],
    )
    return ticks


def _label_blocks(image: RasterImage, layout: PageLayout) -> list[tuple[int, int]]:
    """Return (top, bottom) rows of each run of printed text in the depth column.

    The crop stops one pixel inside the data frame on every side. The frame
    rules are solid ink across the full column and would otherwise be collected
    as two more "labels" at the top and bottom of the log.
    """
    array = to_array(image)
    grey = array if array.ndim == 2 else cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)

    column = grey[
        layout.data_top + 1 : layout.data_bottom,
        layout.depth_column.x_left + 1 : layout.depth_column.x_right,
    ]
    inked_rows = np.where((column < _LABEL_INK_MAX).any(axis=1))[0]
    if inked_rows.size == 0:
        return []

    # Walk the inked rows and cut a new block wherever the gap is too wide to be
    # a hole inside one numeral. Offsets are relative to the crop, so the crop's
    # own top row is added back.
    offset = layout.data_top + 1
    blocks: list[tuple[int, int]] = []
    start = previous = int(inked_rows[0])
    for row in inked_rows[1:]:
        row = int(row)
        if row - previous > _LABEL_ROW_GAP_PX:
            blocks.append((start + offset, previous + offset))
            start = row
        previous = row
    blocks.append((start + offset, previous + offset))

    return [
        (top, bottom)
        for top, bottom in blocks
        if bottom - top + 1 >= _MIN_LABEL_HEIGHT_PX
    ]


def _attribute_to_grid(
    blocks: list[tuple[int, int]], grid: GridLines
) -> tuple[DepthTick, ...]:
    """Match each label block to the nearest reading-grid line."""
    grid_rows = np.asarray(grid.depth_grid_rows)
    limit = grid.grid_spacing_px * _MAX_LABEL_OFFSET_FRACTION

    claimed: dict[int, tuple[int, int]] = {}
    ticks: list[DepthTick] = []
    for top, bottom in blocks:
        centre = (top + bottom) // 2
        nearest = int(grid_rows[int(np.argmin(np.abs(grid_rows - centre)))])
        offset = abs(nearest - centre)
        if offset > limit:
            raise ValueError(
                f"A label spanning rows {top}-{bottom} is {offset} px from the "
                f"nearest grid line at row {nearest}, more than half the "
                f"{grid.grid_spacing_px:.0f} px grid pitch. It cannot be "
                "attributed to a depth."
            )
        if nearest in claimed:
            raise ValueError(
                f"Two labels, rows {claimed[nearest]} and rows {top}-{bottom}, "
                f"both fall on the grid line at row {nearest}. One of them is "
                "not a depth label, or the text was split wrongly."
            )
        claimed[nearest] = (top, bottom)
        ticks.append(DepthTick(grid_row=nearest, label_top=top, label_bottom=bottom))

    # Depth increases downward, so sorting by row puts the ticks in depth order.
    return tuple(sorted(ticks, key=lambda tick: tick.grid_row))
