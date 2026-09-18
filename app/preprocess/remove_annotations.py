"""Erase what is printed over the tracks but is not log data.

In : the sheet, after the reading grid has been removed.
Out: the same sheet with the coloured area fills hollowed out and the
     annotation words erased, everything else untouched.
Rule: this removes the INTERIOR of a fill and never its edge. On a log sheet a
      fill is bounded by the curves either side of it — the salmon "Gas" fill on
      the reference scan is the area between the neutron and density curves — so
      its boundary IS curve ink. Erasing the boundary would delete the very
      curve the fill was drawn to highlight.

      Kept separate from preprocess/remove_grid.py on purpose: different input,
      different algorithm, and if a curve goes missing you need to know which of
      the two removed it.

      KNOWN LIMIT: a label whose leader line actually touches the curve it points
      at becomes one connected shape with that curve, and is left alone. Erasing
      it would mean erasing the curve. What survives is a short horizontal stroke
      a few pixels tall, which the continuity prior in extract/trace.py ignores:
      following it would cost a large sideways jump for a handful of rows and
      then a jump back.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

try:
    from app.contracts import RasterImage
    from app.ingest.load_image import FORMAT_RGB, from_array, to_array
except ImportError:
    from contracts import RasterImage
    from ingest.load_image import FORMAT_RGB, from_array, to_array

logger = logging.getLogger(__name__)

# White paper. Erased pixels are set to this so later stages see blank sheet
# rather than a special value they would each have to know about.
_PAPER = 255

# Anything darker than this is printed matter rather than paper. Set just below
# white because a JPEG leaves the margin speckled in the high 240s.
_PRINTED_MAX = 245

# A fill is eroded by this much. Erosion, not opening: opening would restore the
# region to its original outline and take the boundary curve with it, whereas
# erosion strictly shrinks, leaving a rim. A kernel of 7 shrinks a region by 3
# pixels on every side, and a printed curve is 1-3 pixels wide, so no curve
# survives erosion and every fill boundary does.
_FILL_EROSION_PX = 7

# Curve ink is darker than any area fill, and that gap is what lets a fill be
# hollowed out around a curve that crosses it. Measured on the reference sheet:
# the palest curve is bulk density's brown at about 116 on a grey scale, and the
# darkest fill is the grey hydrocarbon shading at about 150.
_CURVE_INK_MAX = 135

# How dark a pixel must be to count as part of a character. Measured on the
# reference sheet: the labels are printed near black but anti-aliased, and their
# strokes tail out to about 170. A tighter threshold breaks a letter into two or
# three fragments, none of them character-shaped, and the word survives. The
# pale reading grid prints at 200 and above, so it stays out.
_TEXT_INK_MAX = 170

# What a printed character looks like, measured from the "Shale", "Sand",
# "Hydrocarbon", "Gas", "Oil" and "Brine" labels on the reference sheet: 6-7 px
# wide and 9-13 px tall. The lower bounds are what keep a dotted curve's dots
# (2x2) and a dashed curve's dashes (2x8) out.
_CHAR_MIN_WIDTH_PX = 3
_CHAR_MAX_WIDTH_PX = 20
_CHAR_MIN_HEIGHT_PX = 6
_CHAR_MAX_HEIGHT_PX = 22
_CHAR_MIN_AREA_PX = 10

# Characters in a word sit side by side on a shared baseline. This is the real
# discriminator: a curve's dashes are stacked vertically along the curve, so
# they never form a horizontal run of shapes at the same height. Where a dashed
# curve briefly runs horizontally its dashes are only 2-3 px tall and fail the
# character height test first.
_WORD_MAX_CHAR_GAP_PX = 8
_WORD_MIN_CHARACTERS = 2

# A leader line joins a label to the thing it points at, and at this ink
# threshold a label often merges with its own leader into one shape: measured at
# 40-130 px long and up to 20 px tall on the reference sheet. Both are wide,
# short and sparse. A curve can never look like this, because a curve is one
# connected component running the full height of the track — several hundred
# pixels — so it fails the height test by two orders of magnitude.
_BLOCK_MIN_WIDTH_PX = 25
_BLOCK_MAX_HEIGHT_PX = 22
# Average ink per column. A hairline gives 1-2 and a word with its leader about
# 2-3, whereas a solid patch of fill the same size gives its full height. Note
# this cannot be expressed as a density: a perfectly horizontal hairline fills
# its own bounding box completely.
_BLOCK_MAX_MEAN_THICKNESS_PX = 6


def remove_annotations(image: RasterImage) -> RasterImage:
    """Hollow out the colour fills and erase the labels and their leader lines.

    Args:
        image: the RGB sheet.

    Returns:
        A new RasterImage. The input is not modified.
    """
    array = to_array(image)
    if array.ndim != 3:
        raise ValueError(
            f"Annotation removal needs the colour sheet, but was given an array "
            f"of shape {array.shape}. The fills are only distinguishable by "
            "colour."
        )

    grey = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    fills = _fill_interiors(grey)
    labels = _labels_and_leaders(grey)

    cleaned = array.copy()
    cleaned[fills | labels] = _PAPER

    logger.info(
        "remove_annotations: OK - %d fill pixel(s) and %d label pixel(s) erased",
        int(fills.sum()),
        int(labels.sum()),
    )
    return from_array(cleaned, FORMAT_RGB)


def _fill_interiors(grey: np.ndarray) -> np.ndarray:
    """The inside of every printed region thicker than a curve, minus the curves.

    Erosion answers "is this pixel at least three pixels deep inside something
    printed?". A curve is not, however long it is. A colour fill is, everywhere
    except its rim.

    The curve ink is then put back. A curve that runs THROUGH a fill — the
    spontaneous potential curve crosses the yellow sand on the reference scan —
    sits in the middle of a thick printed region, so erosion alone would eat it.
    Area fills are printed pale and curves dark, which is what separates them:
    measured on the reference sheet the palest curve is around 116 on a grey
    scale and the darkest fill around 150.
    """
    printed = (grey < _PRINTED_MAX).astype(np.uint8)
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (_FILL_EROSION_PX, _FILL_EROSION_PX)
    )
    interiors = cv2.erode(printed, kernel).astype(bool)
    return interiors & (grey >= _CURVE_INK_MAX)


def _labels_and_leaders(grey: np.ndarray) -> np.ndarray:
    """Pixels belonging to annotation words and the lines pointing from them."""
    ink = (grey < _TEXT_INK_MAX).astype(np.uint8)
    count, labelled, stats, _ = cv2.connectedComponentsWithStats(ink, 8)

    characters: list[tuple[int, int, int, int, int]] = []
    mask = np.zeros(grey.shape, dtype=bool)

    for index in range(1, count):
        left, top, width, height, area = (
            int(stats[index, cv2.CC_STAT_LEFT]),
            int(stats[index, cv2.CC_STAT_TOP]),
            int(stats[index, cv2.CC_STAT_WIDTH]),
            int(stats[index, cv2.CC_STAT_HEIGHT]),
            int(stats[index, cv2.CC_STAT_AREA]),
        )

        if (
            width >= _BLOCK_MIN_WIDTH_PX
            and height <= _BLOCK_MAX_HEIGHT_PX
            and area <= width * _BLOCK_MAX_MEAN_THICKNESS_PX
        ):
            # A wide, short, sparse shape: a leader line, or a label that has
            # run into its own leader. A curve is never short.
            mask |= labelled == index
            continue

        if (
            _CHAR_MIN_WIDTH_PX <= width <= _CHAR_MAX_WIDTH_PX
            and _CHAR_MIN_HEIGHT_PX <= height <= _CHAR_MAX_HEIGHT_PX
            and area >= _CHAR_MIN_AREA_PX
        ):
            characters.append((index, left, top, width, height))

    for index in _characters_in_words(characters):
        mask |= labelled == index

    return mask


def _characters_in_words(
    characters: list[tuple[int, int, int, int, int]]
) -> list[int]:
    """Keep only character shapes that sit beside another on the same baseline.

    A single character-sized blob on its own is far more likely to be a piece of
    curve than a word, so it is left alone. Requiring company is what makes this
    safe to run over a track full of dashed and dotted curves.

    Grouping is transitive over neighbouring pairs rather than a single sweep
    left to right. A sweep fails on a real sheet because the labels sit in
    different tracks at different depths: sorted by x, the letters of one word
    are interleaved with those of another, every run breaks after a letter or
    two, and most of the text survives.
    """
    # Each character starts in its own group; neighbours are then merged. The
    # sheet has a few hundred character-sized shapes, so comparing every pair is
    # immediate and avoids any assumption about reading order.
    group_of = list(range(len(characters)))

    def root(item: int) -> int:
        while group_of[item] != item:
            group_of[item] = group_of[group_of[item]]
            item = group_of[item]
        return item

    for i in range(len(characters)):
        for j in range(i + 1, len(characters)):
            if _adjacent(characters[i], characters[j]) or _adjacent(
                characters[j], characters[i]
            ):
                group_of[root(i)] = root(j)

    members: dict[int, list[int]] = {}
    for position, character in enumerate(characters):
        members.setdefault(root(position), []).append(character[0])

    return [
        index
        for group in members.values()
        if len(group) >= _WORD_MIN_CHARACTERS
        for index in group
    ]


def _adjacent(
    left: tuple[int, int, int, int, int], right: tuple[int, int, int, int, int]
) -> bool:
    """True if two character shapes are neighbours in the same word."""
    _, left_x, left_y, left_w, left_h = left
    _, right_x, right_y, _, right_h = right

    gap = right_x - (left_x + left_w)
    if not 0 <= gap <= _WORD_MAX_CHAR_GAP_PX:
        return False

    # Letters in a word share a baseline, so their vertical extents overlap for
    # most of their height. Two shapes at the same x but different depths — a
    # curve's dashes — do not overlap at all.
    overlap = min(left_y + left_h, right_y + right_h) - max(left_y, right_y)
    return overlap >= min(left_h, right_h) / 2
