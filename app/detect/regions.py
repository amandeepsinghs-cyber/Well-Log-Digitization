"""Decompose a log sheet into its columns and its data area.

In : the RGB sheet and the GridLines already detected on it.
Out: a PageLayout — where the data starts and ends vertically, which pixel
     columns hold which track, which column holds the depth labels, and the
     rows spanned by the header block.
Rule: geometry only. This module says *where* things are. What they mean —
      which curve, which units, linear or logarithmic — is printed in the
      header and is read later, in header/ocr_header.py and parse_header.py.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

try:
    from app.contracts import ColumnSpan, GridLines, PageLayout, RasterImage
    from app.ingest.load_image import to_array
except ImportError:
    from contracts import ColumnSpan, GridLines, PageLayout, RasterImage
    from ingest.load_image import to_array

logger = logging.getLogger(__name__)

# The depth column is identified by being conspicuously narrower than the curve
# tracks: it carries four short numbers, while a track carries a full plot. On
# the reference sheet it is 72 px against tracks of 277 px, a ratio of 0.26.
# Requiring it to be under half the median column width leaves a wide margin
# either side of that, while still refusing a sheet where no column stands out —
# which is the case we must not guess at, because picking the wrong column makes
# every depth in the output wrong.
_DEPTH_COLUMN_MAX_WIDTH_RATIO = 0.5

# Anything below this on a 0-255 grey scale counts as printed content when
# looking for the top of the header. Set well above black because header box
# rules and small text are anti-aliased grey, but below the paper's own tone so
# JPEG speckle on the white margin is not mistaken for the header.
_CONTENT_INK_MAX = 200


def detect_regions(image: RasterImage, grid: GridLines) -> PageLayout:
    """Work out which part of the sheet is what.

    Args:
        image: the full RGB sheet.
        grid: the rules found by detect/gridlines.py on that same sheet.

    Returns:
        PageLayout locating the data area, the tracks, the depth column and the
        header block.

    Raises:
        ValueError: if the sheet does not decompose into a depth column plus at
            least one track, or if no header block sits above the data area.
            Every one of these is a case where continuing would mean guessing
            at the sheet's structure.
    """
    if not grid.depth_grid_rows:
        raise ValueError(
            "No depth grid rows were detected, so the data area cannot be "
            "bounded. detect_gridlines found only the heavy rules at "
            f"{grid.heavy_horizontals}."
        )

    data_top, data_bottom = _data_area(grid)
    tracks, depth_column = _columns(grid)
    header_rows = _header_block(image, data_top)

    logger.info(
        "detect_regions: OK - data rows %d-%d, %d track(s) %s, depth column "
        "%s, header rows %s",
        data_top,
        data_bottom,
        len(tracks),
        [(t.name, t.x_left, t.x_right) for t in tracks],
        (depth_column.x_left, depth_column.x_right),
        header_rows,
    )
    return PageLayout(
        data_top=data_top,
        data_bottom=data_bottom,
        tracks=tracks,
        depth_column=depth_column,
        header_rows=header_rows,
    )


def _data_area(grid: GridLines) -> tuple[int, int]:
    """Find the heavy rules that bracket the depth grid.

    The data area is the part of the sheet where curves are plotted. Its top and
    bottom are heavy rules, not light grid lines — but the reliable way to tell
    *which* heavy rules is that they are the ones enclosing the evenly spaced
    reading grid. A log sheet has heavy rules through its header too, and those
    look identical to a detector that only measures span.
    """
    first_grid_row = grid.depth_grid_rows[0]
    last_grid_row = grid.depth_grid_rows[-1]

    # On the reference sheet the top rule doubles as the first grid line, so the
    # comparison has to be inclusive at both ends.
    above = [row for row in grid.heavy_horizontals if row <= first_grid_row]
    below = [row for row in grid.heavy_horizontals if row >= last_grid_row]

    if not above:
        raise ValueError(
            f"The topmost depth grid row is {first_grid_row} but there is no "
            f"heavy rule at or above it (heavy rules: {grid.heavy_horizontals}). "
            "The data area has no top edge."
        )
    if not below:
        raise ValueError(
            f"The lowest depth grid row is {last_grid_row} but there is no "
            f"heavy rule at or below it (heavy rules: {grid.heavy_horizontals}). "
            "The data area has no bottom edge."
        )

    # The innermost bracketing rules: the lowest one above the grid and the
    # highest one below it. Anything further out is the header or the margin.
    return max(above), min(below)


def _columns(grid: GridLines) -> tuple[tuple[ColumnSpan, ...], ColumnSpan]:
    """Split the sheet into columns at the heavy vertical rules.

    Consecutive separators bound one column each. One of those columns carries
    the depth labels rather than curves, and it is found by width, not by
    position: on this sheet it sits between Track 1 and Track 2, and assuming it
    is leftmost is the single most likely way to get this phase wrong.
    """
    separators = grid.track_separators
    if len(separators) < 3:
        raise ValueError(
            f"Only {len(separators)} vertical rule(s) at {separators}, which "
            "bound at most one column. A log sheet needs a depth column and at "
            "least one curve track, so at least 3 rules."
        )

    spans = [
        (left, right) for left, right in zip(separators, separators[1:])
    ]
    widths = [right - left for left, right in spans]

    narrowest = int(np.argmin(widths))
    median_width = float(np.median(widths))
    if widths[narrowest] > median_width * _DEPTH_COLUMN_MAX_WIDTH_RATIO:
        raise ValueError(
            f"No column is narrow enough to be the depth column: widths "
            f"{widths} px against a median of {median_width:.0f} px. The depth "
            "column cannot be identified by width on this sheet."
        )

    tracks: list[ColumnSpan] = []
    for index, (left, right) in enumerate(spans):
        if index == narrowest:
            continue
        # Tracks are numbered left to right over the curve tracks only, skipping
        # the depth column, which is how a log is described: "Track 2 is
        # resistivity" counts tracks, not columns.
        tracks.append(
            ColumnSpan(name=f"Track {len(tracks) + 1}", x_left=left, x_right=right)
        )

    depth_left, depth_right = spans[narrowest]
    return tuple(tracks), ColumnSpan(name="Depth", x_left=depth_left, x_right=depth_right)


def _header_block(image: RasterImage, data_top: int) -> tuple[int, int]:
    """Find the rows spanned by the header, from its first printed row to the data.

    The header block is not one tidy band. On the reference sheet Track 2 holds
    three stacked curve headers and starts at row 13, while Tracks 1 and 3 hold
    two and start at row 72. Returning the union — the topmost printed row above
    the data area, down to the data area — gives every track's header in full;
    cropping it to a track's x-bounds yields that track's headers with some
    blank paper above, which costs the OCR step nothing.
    """
    array = to_array(image)
    grey = array if array.ndim == 2 else cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)

    above_data = grey[:data_top, :]
    printed_rows = np.where((above_data < _CONTENT_INK_MAX).any(axis=1))[0]
    if printed_rows.size == 0:
        raise ValueError(
            f"Nothing is printed in rows 0-{data_top} above the data area, so "
            "the sheet has no header. Without it there is no scale to calibrate "
            "against and no curve can be given a value."
        )

    return int(printed_rows[0]), data_top
