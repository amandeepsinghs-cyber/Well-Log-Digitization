"""Follow one curve down the page, choosing at every crossing by continuity.

In : the affinity map for a single curve, from extract/separate.py.
Out: a PixelPath — the pixel column the curve occupies on each depth row, and
     how good the evidence was for it.
Rule: tracing and the continuity prior are ONE operation and cannot be split.
      At a crossing there are two candidate columns for a depth row, and
      choosing between them IS the prior: a log curve cannot jump sideways
      without a rock transition, so the continuous path wins. A tracer that
      emitted one column per row first would already have destroyed the
      candidate set the prior needs, which is why continuity can never be
      applied afterwards as a smoothing pass.

      This is the main accuracy lever in the project. It is also the backstop
      for two things earlier stages deliberately leave behind: the leader lines
      that preprocess/remove_annotations.py cannot erase without erasing the
      curve they touch, and the faint ghost of the neighbouring curve that
      extract/separate.py leaves in each map. Both are short sideways
      excursions, and both cost more than they save.
"""

from __future__ import annotations

import logging

import numpy as np

try:
    from app.contracts import PixelPath
except ImportError:
    from contracts import PixelPath

logger = logging.getLogger(__name__)

# What one pixel of sideways movement costs, in the same units as evidence:
# a cost of 1.0 is what a completely blank pixel costs. So 0.10 means moving
# 10 pixels sideways is as expensive as one row of blank paper.
#
# The bounds are set by two real situations on the reference sheet, and the
# usable range between them is wide, which is why a single constant is safe:
#
#  * TOO LOW and the path detours to the ghost of the other curve while its own
#    is occluded. The worst occlusion is 16 rows and the ghost is about 50 px
#    away, so the detour must cost more than 16 rows of weak evidence: that
#    puts the floor at about 0.02.
#  * TOO HIGH and a real bed boundary gets rounded off. The sharpest on this
#    sheet moves a curve about 100 px within a few rows and then stays there
#    for tens of rows, so following it must cost less than refusing to: that
#    puts the ceiling at about 0.3.
_JUMP_PENALTY = 0.10

# A curve is 1-3 px wide, and anti-aliasing adds a pixel either side. The
# sub-pixel centre is therefore searched no further than this from the column
# the path chose. The cap matters: without it, a horizontal leader line or the
# rim of a fill that happens to run through the chosen pixel would drag the
# centroid tens of pixels sideways and report a value that was never printed.
_CENTRE_SEARCH_PX = 4


def trace_curve(affinity: np.ndarray, jump_penalty: float = _JUMP_PENALTY) -> PixelPath:
    """Find the cheapest continuous path down a curve's affinity map.

    Args:
        affinity: (rows, columns) float in [0, 1] from extract/separate.py.
            1.0 is ink this curve plainly owns, 0.0 is blank paper.
        jump_penalty: cost per pixel of sideways movement between adjacent
            rows. Exposed so the QC plot can show what a different prior would
            have produced; the default is what the pipeline uses.

    Returns:
        A PixelPath with one column and one confidence per row.

    Raises:
        ValueError: if the map is not a two-dimensional affinity map.
    """
    if affinity.ndim != 2:
        raise ValueError(
            f"Tracing needs one curve's affinity map, shaped (rows, columns), "
            f"but got an array of shape {affinity.shape}"
        )
    if affinity.size == 0:
        raise ValueError("Cannot trace a curve through an empty track")

    # Evidence becomes cost: ink this curve owns is free to pass through, blank
    # paper costs a full unit. Everything downstream is a comparison of costs,
    # so the absolute scale only matters relative to the jump penalty.
    step_cost = 1.0 - np.clip(affinity.astype(np.float64), 0.0, 1.0)

    totals = _accumulate(step_cost, jump_penalty)
    columns = _backtrack(totals, jump_penalty)

    centres = np.array(
        [_sub_pixel_centre(affinity[row], column) for row, column in enumerate(columns)]
    )
    confidence = affinity[np.arange(len(columns)), columns].astype(np.float64)

    blind_rows = int((confidence == 0.0).sum())
    logger.info(
        "trace_curve: OK - %d row(s), %d carried across gaps, mean confidence %.2f",
        len(columns),
        blind_rows,
        float(confidence.mean()),
    )
    return PixelPath(columns=tuple(centres), confidence=tuple(confidence))


def _accumulate(step_cost: np.ndarray, jump_penalty: float) -> np.ndarray:
    """Cheapest total cost of reaching each pixel from anywhere on the top row.

    Walking down the rows one at a time is enough because the graph is layered:
    a path only ever moves from a row to the row below it, so there is no
    shorter route back and no need for a general shortest-path search to
    discover the order to visit nodes in. Depth is the topological order.
    """
    totals = np.empty_like(step_cost)
    totals[0] = step_cost[0]
    for row in range(1, len(step_cost)):
        totals[row] = step_cost[row] + _cheapest_arrival(totals[row - 1], jump_penalty)
    return totals


def _cheapest_arrival(previous: np.ndarray, jump_penalty: float) -> np.ndarray:
    """For each column, the cheapest way to arrive there from the row above.

    Computes min over every column y of previous[y] + jump_penalty * |x - y|,
    for all x at once and with no limit on how far the curve may move in one
    row — which matters, because at a sharp bed boundary it moves a hundred
    pixels in two.

    Hand-written because no library exposes a weighted min-plus convolution
    over a cost field, and because the linear penalty makes it a two-line
    identity rather than an algorithm: cost + penalty * distance separates into
    a term in x and a term in y, so a running minimum in each direction gives
    the exact answer in one pass each way. This is the same trick as the L1
    distance transform, generalised from a binary mask to a cost field.
    """
    columns = np.arange(len(previous), dtype=np.float64)
    offset = jump_penalty * columns

    # Arriving from the left: the cheapest previous[y] - penalty*y seen so far,
    # paid back at the current column.
    from_left = np.minimum.accumulate(previous - offset) + offset
    # And symmetrically from the right.
    from_right = np.minimum.accumulate((previous + offset)[::-1])[::-1] - offset

    return np.minimum(from_left, from_right)


def _backtrack(totals: np.ndarray, jump_penalty: float) -> np.ndarray:
    """Walk the cheapest path back up from the bottom row to the top."""
    rows, width = totals.shape
    columns = np.empty(rows, dtype=np.int64)

    # The path ends wherever the bottom row is cheapest to reach.
    columns[-1] = int(np.argmin(totals[-1]))

    candidates = np.arange(width, dtype=np.float64)
    for row in range(rows - 1, 0, -1):
        # Whichever column above made this one cheapest is where we came from.
        # Recomputing it is exact: the accumulated totals already contain every
        # alternative, so no separate record of choices has to be kept.
        arrival = totals[row - 1] + jump_penalty * np.abs(candidates - columns[row])
        columns[row - 1] = int(np.argmin(arrival))

    return columns


def _sub_pixel_centre(row_affinity: np.ndarray, column: int) -> float:
    """Refine a chosen column to the weighted centre of the stroke it sits in.

    A printed curve is two or three pixels wide, so reporting the integer
    column it was found at throws away half a pixel of accuracy for no reason.
    On this sheet a pixel is about 0.54 gAPI on the gamma ray scale, so the
    refinement is worth roughly a quarter of a unit on every sample.
    """
    if row_affinity[column] == 0.0:
        # No ink here: the path is being carried across a gap, and there is no
        # stroke to find the centre of. The column stands as it is and the
        # confidence of 0.0 marks the sample as unobserved.
        return float(column)

    # Widen to the contiguous run of ink around the chosen column, stopping at
    # blank paper or at the search cap, whichever comes first.
    left = column
    while (
        left > 0
        and column - left < _CENTRE_SEARCH_PX
        and row_affinity[left - 1] > 0.0
    ):
        left -= 1

    right = column
    while (
        right < len(row_affinity) - 1
        and right - column < _CENTRE_SEARCH_PX
        and row_affinity[right + 1] > 0.0
    ):
        right += 1

    stroke = row_affinity[left : right + 1].astype(np.float64)
    positions = np.arange(left, right + 1, dtype=np.float64)
    return float((stroke * positions).sum() / stroke.sum())
