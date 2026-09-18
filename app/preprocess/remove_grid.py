"""Erase the printed reading grid so it is never traced as curve ink.

In : The RGB sheet and the GridLines found by detect/gridlines.py.
Out: The same sheet with grid pixels replaced by paper white.
Rule: Conservative by design. Only pixels that are BOTH on a detected rule line
      AND pale enough to be grid are erased. A curve crossing a grid line is
      darker or more saturated than the grid, so it survives — erasing the whole
      line would punch a hole through every curve at every crossing, and the
      tracer would read those holes as gaps.

      Handles only the rules that detect/gridlines.py found: the horizontal
      depth grid and the heavy separators. The pale vertical value-grid inside
      each track is deliberately left alone. It is never mistaken for a curve
      because extract/separate.py selects pixels by curve colour, and the pale
      grid matches none of them.
"""

from __future__ import annotations

import logging

import numpy as np

try:
    from app.contracts import GridLines, RasterImage
    from app.ingest.load_image import FORMAT_RGB, from_array, to_array
except ImportError:
    from contracts import GridLines, RasterImage
    from ingest.load_image import FORMAT_RGB, from_array, to_array

logger = logging.getLogger(__name__)

# A pixel on a rule is grid, and not a curve crossing it, if it is at least this
# bright. Measured on the reference sheet: the reading grid prints around
# 200-235, the heavy rules around 30-90, and curve ink below 120. The threshold
# sits above curve ink so that a curve is never punched through.
_GRID_MIN_BRIGHTNESS = 150

# Rules are 1-3 px thick after anti-aliasing, so clearing only the centre row
# would leave two grey fringes that still read as ink. One pixel either side
# covers the fringe without reaching a curve running alongside the rule.
_LINE_HALF_WIDTH = 1

# Paper white. Curves are found by darkness or colour, so erased pixels must
# look like blank paper, not like a pale curve.
_PAPER = 255


def remove_grid(image: RasterImage, grid: GridLines) -> RasterImage:
    """Return the sheet with its printed grid erased.

    Args:
        image: the full RGB sheet.
        grid: the rules found by detect_gridlines on this same sheet.

    Returns:
        A new RasterImage; the input is not modified.
    """
    array = to_array(image).copy()
    height, width = array.shape[:2]
    brightness = array.mean(axis=2)

    # Only pale pixels are candidates. Building the mask once and intersecting
    # it with each line is what keeps curve crossings intact.
    is_pale = brightness >= _GRID_MIN_BRIGHTNESS

    erased = 0
    for row in grid.depth_grid_rows:
        for offset in range(-_LINE_HALF_WIDTH, _LINE_HALF_WIDTH + 1):
            y = row + offset
            if 0 <= y < height:
                target = is_pale[y, :]
                array[y, target] = _PAPER
                erased += int(target.sum())

    for column in grid.track_separators:
        for offset in range(-_LINE_HALF_WIDTH, _LINE_HALF_WIDTH + 1):
            x = column + offset
            if 0 <= x < width:
                target = is_pale[:, x]
                array[target, x] = _PAPER
                erased += int(target.sum())

    logger.info(
        "remove_grid: OK - erased %d pale pixel(s) on %d row(s) and %d column(s)",
        erased,
        len(grid.depth_grid_rows),
        len(grid.track_separators),
    )
    return from_array(array, FORMAT_RGB)


def grid_pixel_count(image: RasterImage, grid: GridLines) -> int:
    """How many pixels remove_grid would erase, without erasing them.

    Used by the QC report to state how much of the sheet was grid, which is a
    cheap sanity check: a wildly low count means the rules were mis-located and
    the grid is still in the image the tracer sees.
    """
    brightness = to_array(image).mean(axis=2)
    height, width = brightness.shape
    mask = np.zeros((height, width), dtype=bool)

    for row in grid.depth_grid_rows:
        for offset in range(-_LINE_HALF_WIDTH, _LINE_HALF_WIDTH + 1):
            if 0 <= row + offset < height:
                mask[row + offset, :] = True
    for column in grid.track_separators:
        for offset in range(-_LINE_HALF_WIDTH, _LINE_HALF_WIDTH + 1):
            if 0 <= column + offset < width:
                mask[:, column + offset] = True

    return int(np.count_nonzero(mask & (brightness >= _GRID_MIN_BRIGHTNESS)))
