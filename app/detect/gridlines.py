"""Find the printed rule lines on a log sheet: track separators and depth grid.

In : A RasterImage of the whole sheet.
Out: A GridLines giving the heavy vertical separators, the heavy horizontal
     rules, and the light horizontal reading grid with its spacing.
Rule: This module ALSO owns binarisation (originally checklist step 31).
      Thresholding is an input stage of line detection, not a pipeline stage:
      the binary image exists only so morphology can run on it and is never
      handed onward. Binarising the whole sheet as a pipeline step would be
      actively harmful — it destroys the colour that extract/separate.py uses
      to tell a green GR curve from a black SP curve.

      Lines are found by morphological opening with a long 1-pixel-thick
      kernel, the standard OpenCV approach for table rules. A curve, a text
      label or a colour fill cannot survive an opening with a kernel most of a
      track wide, so only genuine full-width rules remain.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

try:
    from app.contracts import GridLines, RasterImage
    from app.ingest.load_image import to_array
except ImportError:
    from contracts import GridLines, RasterImage
    from ingest.load_image import to_array

logger = logging.getLogger(__name__)

# A rule must run this fraction of the sheet to count as HEAVY structure. The
# outer frame and the track separators run essentially edge to edge; a curve
# never does.
_HEAVY_SPAN_FRACTION = 0.55

# The light reading grid is printed inside a track, so it only has to cross most
# of one track. Measured on the reference sheet: the narrowest track is ~230 px
# of a 919 px sheet, so a quarter-width kernel spans it without being satisfied
# by a colour fill, which covers at most part of a track.
_LIGHT_SPAN_FRACTION = 0.25

# Ink is anything below this on a 0-255 grey scale. The reading grid is printed
# pale — measured around 200-230 on the reference sheet — so a plain Otsu
# threshold tuned for black ink would erase it entirely. Two separate passes are
# therefore run, one per population.
_HEAVY_INK_MAX = 128
_LIGHT_INK_MAX = 245

# Adjacent detected rows/columns within this distance are one printed line seen
# twice: rules are 1-3 px thick after anti-aliasing.
_MERGE_TOLERANCE_PX = 4

# A light row is only part of the depth grid if the grid is regular. Rows whose
# spacing deviates by more than this fraction of the median are dropped as
# stray ink rather than grid.
_SPACING_TOLERANCE = 0.15


def detect_gridlines(image: RasterImage) -> GridLines:
    """Locate the sheet's rule lines.

    Args:
        image: the full RGB sheet.

    Returns:
        GridLines with separators, heavy horizontals, and the depth grid.

    Raises:
        ValueError: if fewer than two vertical separators are found. One track
            needs two edges; with fewer, nothing downstream can define a track,
            and guessing the sheet's structure would fabricate every value.
    """
    grey = _to_grey(image)
    height, width = grey.shape

    # --- Heavy structure: the frame and the track separators -----------------
    heavy = (grey < _HEAVY_INK_MAX).astype(np.uint8)
    separators = _find_lines(heavy, axis="vertical", span=int(height * _HEAVY_SPAN_FRACTION))
    heavy_rows = _find_lines(heavy, axis="horizontal", span=int(width * _HEAVY_SPAN_FRACTION))

    if len(separators) < 2:
        raise ValueError(
            f"Found {len(separators)} vertical rule(s) in a {width}x{height} "
            "sheet; at least 2 are needed to bound a single track. The image "
            "may not be a gridded well log."
        )

    # --- Light structure: the reading grid inside the tracks -----------------
    # Anything not white counts here, so this pass also picks up the heavy rules
    # and any dark fill edge. The regularity filter below removes those.
    light = (grey < _LIGHT_INK_MAX).astype(np.uint8)
    light_rows = _find_lines(
        light, axis="horizontal", span=int(width * _LIGHT_SPAN_FRACTION)
    )
    depth_rows, spacing = _keep_regular(light_rows)

    logger.info(
        "detect_gridlines: OK - %d separator(s) at %s, %d heavy row(s), "
        "%d depth grid row(s) spaced %.1f px",
        len(separators),
        separators,
        len(heavy_rows),
        len(depth_rows),
        spacing,
    )
    return GridLines(
        track_separators=separators,
        heavy_horizontals=heavy_rows,
        depth_grid_rows=depth_rows,
        grid_spacing_px=spacing,
    )


def _to_grey(image: RasterImage) -> np.ndarray:
    """Collapse a RasterImage to a 2-D grey array for thresholding.

    Local to this module on purpose. The greyscale copy exists only to find
    lines; the colour original is what every later stage works on.
    """
    array = to_array(image)
    if array.ndim == 2:
        return array
    return cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)


def _find_lines(binary: np.ndarray, axis: str, span: int) -> tuple[int, ...]:
    """Return the positions of rules running at least `span` pixels along `axis`.

    Morphological opening with a long, one-pixel-thick kernel keeps only runs of
    ink that are continuous for the kernel's whole length. Everything shorter —
    curves, characters, fill edges — is erased, which is why no separate
    "is this a curve?" test is needed.
    """
    # A kernel of at least 3 px, otherwise opening is a no-op on a thin rule.
    length = max(3, span)
    if axis == "vertical":
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, length))
        profile_axis = 0  # collapse rows, leaving one value per column
    else:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1))
        profile_axis = 1  # collapse columns, leaving one value per row

    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    # A surviving column/row holds a genuine rule. Requiring most of the kernel
    # length to survive rejects the ragged ends of a near-miss.
    profile = opened.sum(axis=profile_axis)
    hits = np.where(profile >= length * 0.8)[0]
    return _merge_adjacent(hits)


def _merge_adjacent(positions: np.ndarray) -> tuple[int, ...]:
    """Collapse runs of neighbouring pixels into one position per printed line.

    An anti-aliased rule is 2-3 px wide, so it is detected as several adjacent
    positions. The centre of each run is the line.
    """
    if positions.size == 0:
        return ()

    merged: list[int] = []
    run_start = previous = int(positions[0])
    for position in positions[1:]:
        position = int(position)
        if position - previous > _MERGE_TOLERANCE_PX:
            merged.append((run_start + previous) // 2)
            run_start = position
        previous = position
    merged.append((run_start + previous) // 2)
    return tuple(merged)


def _keep_regular(rows: tuple[int, ...]) -> tuple[tuple[int, ...], float]:
    """Keep the longest evenly spaced run of rows, and report its pitch.

    The reading grid is printed at a constant depth interval, so its rows are
    evenly spaced. Everything else that survived the light pass — the header box
    rules, the frame, a fill edge — is not, and filtering on regularity
    separates the two without knowing the pitch in advance.

    The run is found by trying every row as a starting point and keeping the
    longest chain, rather than anchoring on the first row detected. That matters
    because the topmost surviving rows belong to the header, not the grid:
    anchoring on them either drags them in or throws the real grid away.
    """
    if len(rows) < 3:
        # Too few rows to establish a pitch. Report them unfiltered and let
        # calibrate/validate_calibration decide whether that is fatal.
        return rows, 0.0

    gaps = np.diff(np.asarray(rows, dtype=float))
    pitch = float(np.median(gaps))
    if pitch <= 0:
        return rows, 0.0

    best: list[int] = []
    for start in range(len(rows)):
        chain = _chain_from(rows, start, pitch)
        if len(chain) > len(best):
            best = chain

    if len(best) < 3:
        return rows, pitch

    # Re-derive the pitch from the accepted run only, so the reported spacing
    # is not skewed by the irregular rows that were just discarded.
    return tuple(best), float(np.median(np.diff(np.asarray(best, dtype=float))))


def _chain_from(rows: tuple[int, ...], start: int, pitch: float) -> list[int]:
    """Greedily extend an evenly spaced chain of rows from one starting row."""
    chain = [rows[start]]
    for row in rows[start + 1:]:
        multiple = (row - chain[-1]) / pitch
        nearest = round(multiple)
        # A multiple of 2 is allowed because a grid line can be hidden beneath a
        # colour fill; anything larger is a different population of lines, not a
        # gap in this one.
        if nearest in (1, 2) and abs(multiple - nearest) <= _SPACING_TOLERANCE:
            chain.append(row)
    return chain
