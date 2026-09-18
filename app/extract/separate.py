"""Decide which pixels belong to which curve within a single track.

In : the cleaned pixels of one track, and the MnemonicSpecs of the curves
     printed in it.
Out: one affinity map per curve — same shape as the track, 0.0 where the pixel
     cannot be that curve and 1.0 where it plainly is.
Rule: this file never chooses a curve position. It scores evidence and hands
      the ambiguity on. Where two curves coincide, both score highly on the
      same pixels and extract/trace.py resolves it with the continuity prior,
      which is the only thing that can: a hard mask made here would have to
      guess, and a wrong guess is unrecoverable downstream.

Two routes, dispatched by what actually distinguishes the curves on the page:

  * COLOUR, where the curves are printed in different inks. On the reference
    sheet that is Track 1 (green gamma ray against black spontaneous
    potential) and Track 3 (brown bulk density against black neutron).
  * DASH, where they are not. Track 2's three resistivity curves are all
    printed black and differ only in line style: deep solid, medium dashed,
    shallow dotted.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import cv2
import numpy as np

try:
    from app.las.mnemonics import MnemonicSpec
except ImportError:
    from las.mnemonics import MnemonicSpec

logger = logging.getLogger(__name__)

# How dark a pixel must be to be curve ink rather than a colour fill or the
# reading grid. Measured on the reference sheet and shared with
# preprocess/remove_annotations.py: the palest curve is bulk density's brown at
# about 116, and the palest thing that is NOT a curve is the grey hydrocarbon
# fill at about 150. Sitting between them matters more than the exact value —
# at 170 the eroded fills' surviving rims come too, and because a rim runs
# alongside the curve that bounds it, every curve then appears to change colour
# along its length.
_CURVE_INK_MAX = 135

# A COLOURED curve may be paler than that and still be safe to claim, and on
# this sheet it has to be: the olive gamma ray stroke sits right on the 135
# boundary, so a hard cut chops it into fragments and leaves a third of the
# curve reported as never printed. Between 135 and here, ink counts in
# proportion to how dark it is.
#
# This licence is extended to the coloured curve ONLY. What makes 135 necessary
# in the first place is the grey hydrocarbon fill at about 150, and a grey
# pixel is indistinguishable from faint black ink — but it is nothing like
# coloured ink, because it has no colour, and the chroma term below rejects it
# outright. So the black curve keeps the tight threshold and the coloured curve
# does not need it.
_PALE_COLOURED_INK_MAX = 180

# Distance from grey, as plain 0-255 channel spread (max channel minus min).
# Deliberately NOT HSV saturation: saturation is a ratio, so a near-black pixel
# like (11, 10, 7) computes to a saturation of 93 out of 255 and reads as
# strongly coloured, when it is simply black. Absolute spread does not have
# that failure. Measured on the reference sheet: black curve ink spreads 1-7,
# printed colour spreads 34-61.
_ACHROMATIC_MAX_CHROMA = 12  # At or below this, certainly the black curve.
_CHROMATIC_MIN_CHROMA = 30   # At or above this, certainly the coloured curve.

# Distance from grey says a pixel is coloured. It does not say WHICH colour,
# and that distinction has to be made, because black ink printed over a colour
# fill is also far from grey. On the reference sheet the spontaneous potential
# curve runs along the edge of the yellow sand body, and the blend of black
# curve and yellow fill sits at a chroma of about 55 — as chromatic as the
# gamma ray curve itself, and it duplicated the SP curve into the GR map.
#
# The colour is therefore pinned down by the ORDER of the three channels rather
# than by a hue angle. Printed gamma ray is green-leading (G > R > B) and the
# yellow blend is red-leading (R > G > B), which separates them outright. Order
# is used instead of hue because order is what survives a change of press,
# paper or scanner: the reference sheet's gamma ray is 20-44 degrees away from
# the table's hue for it, but has never stopped being green-leading.
_CHANNEL_ORDER_MARGIN = 8

# Only channel pairs the reference colour separates by at least this much
# impose an ordering. A reference whose red and green are nearly equal is not
# making a claim about which of them should lead, and enforcing one would
# reject the curve on rounding noise.
_REFERENCE_MIN_SEPARATION = 8

# Leaning the wrong way weakens a pixel's claim; it does not disqualify it.
# The two failures are symmetric and both are real on this sheet: black ink
# blended into the yellow sand fill leans red and is NOT the green curve, but
# brown density ink blended into the green oil fill also leans the wrong way
# and IS the curve. A veto fixed the first and broke the second, punching
# 18-row holes in the density curve. A floor keeps both traceable while
# leaving the correctly-leaning pixels roughly six times stronger, which is
# the margin extract/trace.py needs to prefer the real curve over a ghost.
_WRONG_LEAN_AFFINITY = 0.15


def separate_curves(
    track_pixels: np.ndarray, specs: Sequence[MnemonicSpec]
) -> dict[str, np.ndarray]:
    """Score every pixel of one track against each curve printed in it.

    Args:
        track_pixels: RGB pixels of the track's data area, (h, w, 3) uint8,
            already cleaned by remove_annotations then remove_grid.
        specs: the curves this track carries, from the parsed header.

    Returns:
        mnemonic -> affinity map, float32 (h, w) in [0, 1]. Non-ink pixels are
        0.0 for every curve.

    Raises:
        ValueError: if the curves cannot be told apart at all.
    """
    if track_pixels.ndim != 3:
        raise ValueError(
            f"Curve separation needs RGB pixels but got an array of shape "
            f"{track_pixels.shape}. Colour is the evidence in two of three "
            "tracks and cannot be recovered once discarded."
        )
    if not specs:
        raise ValueError("Cannot separate curves in a track with no curves")

    # One curve in the track: everything inked is that curve, and there is
    # nothing to discriminate.
    if len(specs) == 1:
        return {specs[0].mnemonic: _ink_mask(track_pixels).astype(np.float32)}

    # Curves printed in the same ink can only be told apart by line style;
    # curves printed in different inks are separated by colour, which is far
    # more reliable than dash classification and is preferred wherever the
    # sheet offers it.
    if len({spec.colour for spec in specs}) > 1:
        return by_colour(track_pixels, specs)
    return by_dash(track_pixels, specs)


def by_colour(
    track_pixels: np.ndarray, specs: Sequence[MnemonicSpec]
) -> dict[str, np.ndarray]:
    """Separate one black curve from one coloured curve by distance from grey.

    The printed hue itself is NOT compared against the hue in the mnemonic
    table. Those colours were chosen so the rendered log resembles a printed
    one, not sampled from this scan, and they do not agree: gamma ray is
    tabled as #4C7C2F, a true green at hue 97 degrees, while the scan prints it
    olive at 53-76 degrees — closer in hue to the tabled bulk-density brown
    than to its own entry. Matching on absolute hue would therefore swap the
    two curves in Track 3.

    What the table IS trusted for is which curve is the black one, because
    "printed in black" survives any change of press, scanner or vendor.
    """
    achromatic = [spec for spec in specs if _chroma_of(spec.colour) <= _ACHROMATIC_MAX_CHROMA]
    chromatic = [spec for spec in specs if _chroma_of(spec.colour) > _ACHROMATIC_MAX_CHROMA]

    if len(achromatic) != 1 or len(chromatic) != 1:
        # Two coloured curves in one track would need their hues clustered and
        # the clusters matched to the table. No sheet in scope has that, and an
        # untested branch here would be a silent curve swap rather than a
        # visible failure.
        raise ValueError(
            "Colour separation handles exactly one black and one coloured "
            f"curve per track, but this track has "
            f"{[s.mnemonic for s in achromatic]} black and "
            f"{[s.mnemonic for s in chromatic]} coloured."
        )

    ink = _ink_mask(track_pixels)
    channels = track_pixels.astype(np.int16)
    chroma = channels.max(axis=2) - channels.min(axis=2)

    # Ramp rather than a step. Where the two curves physically overlap — the
    # spontaneous potential curve runs along the gamma ray curve through the
    # middle of Track 1 — the ink is a blend and belongs to both. Scoring it
    # half each is the truth; picking one would delete the other's only
    # evidence for those rows.
    span = _CHROMATIC_MIN_CHROMA - _ACHROMATIC_MAX_CHROMA
    black_affinity = np.clip((_CHROMATIC_MIN_CHROMA - chroma) / span, 0.0, 1.0)

    # Being coloured is necessary but not sufficient: the colour also has to
    # lean the right way. Without this, the black curve's edge where it runs
    # along the yellow sand body is scored as gamma ray, and the gamma ray map
    # comes back with a second, entirely fictitious curve drawn on it.
    leaning = _channel_order_match(track_pixels, chromatic[0].colour)
    leaning = _WRONG_LEAN_AFFINITY + (1.0 - _WRONG_LEAN_AFFINITY) * leaning

    return {
        achromatic[0].mnemonic: (black_affinity * ink).astype(np.float32),
        chromatic[0].mnemonic: (
            (1.0 - black_affinity) * leaning * _pale_ink_strength(track_pixels)
        ).astype(np.float32),
    }


def by_dash(
    track_pixels: np.ndarray, specs: Sequence[MnemonicSpec]
) -> dict[str, np.ndarray]:
    """Separate curves printed in the same ink by how long their strokes are.

    A solid curve is one connected shape running the whole height of the track.
    A dashed curve is a column of short shapes, a dotted curve a column of tiny
    ones. So the length of the shape a pixel belongs to says which curve drew
    it, and unlike a vertical run-length it does not collapse where a curve
    turns and runs horizontally across a bed boundary.
    """
    if len({spec.dash for spec in specs}) != len(specs):
        raise ValueError(
            "These curves share both an ink colour and a line style, so "
            "nothing on the page distinguishes them: "
            f"{[(s.mnemonic, s.colour, s.dash) for s in specs]}"
        )

    ink = _ink_mask(track_pixels)
    extent = _stroke_extent(ink)

    # The stroke length each curve should show. A solid curve's shape is as
    # long as the track is tall; a dashed one's is its "on" length, the first
    # number of the dash pattern.
    track_height = track_pixels.shape[0]
    nominal = {
        spec.mnemonic: float(spec.dash[0]) if spec.dash else float(track_height)
        for spec in specs
    }

    # Compare in log space: 2 px against 8 px is the same kind of difference as
    # 8 px against 32 px, and only a ratio scale treats it that way. A linear
    # comparison would make every dash look equally unlike a several-hundred
    # pixel solid stroke.
    measured = np.log2(np.maximum(extent, 1.0))
    weights = {
        mnemonic: 1.0 / (1.0 + np.abs(measured - np.log2(length)))
        for mnemonic, length in nominal.items()
    }

    # Normalise so each inked pixel distributes a total of 1.0 across the
    # curves. Where shapes have merged — the three resistivity curves converge
    # and touch at the top and bottom of the reference track — the merged shape
    # is long, so it scores as solid and the other two are left with weak
    # evidence there. That is the honest reading of the pixels.
    total = sum(weights.values())
    return {
        mnemonic: (weight / total * ink).astype(np.float32)
        for mnemonic, weight in weights.items()
    }


def _ink_mask(track_pixels: np.ndarray) -> np.ndarray:
    """Which pixels are unambiguously curve ink, as a float 0.0/1.0 array."""
    grey = cv2.cvtColor(track_pixels, cv2.COLOR_RGB2GRAY)
    return (grey < _CURVE_INK_MAX).astype(np.float64)


def _pale_ink_strength(track_pixels: np.ndarray) -> np.ndarray:
    """How strongly a pixel reads as ink, allowing for pale coloured strokes.

    Full strength below the ink threshold, then fading to nothing as the pixel
    approaches the paper. The fade is what keeps a stroke that sits on the
    threshold from breaking into fragments from one row to the next.
    """
    grey = cv2.cvtColor(track_pixels, cv2.COLOR_RGB2GRAY).astype(np.float64)
    fade = _PALE_COLOURED_INK_MAX - _CURVE_INK_MAX
    return np.clip((_PALE_COLOURED_INK_MAX - grey) / fade, 0.0, 1.0)


def _stroke_extent(ink: np.ndarray) -> np.ndarray:
    """For each inked pixel, the longer side of the shape it belongs to.

    The longer side rather than the area: a dash and a dot are both about two
    pixels wide, so width carries no information, and area would rank a short
    fat blob above a long thin stroke.
    """
    count, labelled, stats, _ = cv2.connectedComponentsWithStats(
        ink.astype(np.uint8), 8
    )

    # Index 0 is the background. Giving it an extent of 0 costs nothing because
    # the result is multiplied by the ink mask before it is used.
    longer_side = np.zeros(count, dtype=np.float64)
    for index in range(1, count):
        longer_side[index] = max(
            int(stats[index, cv2.CC_STAT_WIDTH]),
            int(stats[index, cv2.CC_STAT_HEIGHT]),
        )

    logger.info(
        "by_dash: %d stroke(s), longest %d px, median %d px",
        count - 1,
        int(longer_side.max()) if count > 1 else 0,
        int(np.median(longer_side[1:])) if count > 1 else 0,
    )
    return longer_side[labelled]


def _channel_order_match(pixels: np.ndarray, hex_colour: str) -> np.ndarray:
    """How well each pixel's channels rank in the same order as a reference.

    Returns 1.0 where every ordering the reference asserts also holds in the
    pixel, 0.0 where any of them is contradicted, and a ramp in between so that
    a pixel sitting a pixel-value or two either side of a tie is not discarded
    on rounding noise.

    Reading gamma ray's #4C7C2F as "green above red above blue" is a far weaker
    claim than reading it as "hue 97 degrees", and weaker is what is wanted: it
    is the part of the claim that is still true of a scan printed on different
    paper by a different press.
    """
    reference = _channels_of(hex_colour)
    channels = pixels.astype(np.int16)

    match = np.ones(pixels.shape[:2], dtype=np.float64)
    for high in range(3):
        for low in range(3):
            if reference[high] - reference[low] < _REFERENCE_MIN_SEPARATION:
                # The reference does not claim this channel leads that one.
                continue
            difference = channels[:, :, high] - channels[:, :, low]
            match = np.minimum(
                match, np.clip(difference / _CHANNEL_ORDER_MARGIN, 0.0, 1.0)
            )
    return match


def _chroma_of(hex_colour: str) -> int:
    """How far a #RRGGBB colour sits from grey, in 0-255 channel spread."""
    channels = _channels_of(hex_colour)
    return max(channels) - min(channels)


def _channels_of(hex_colour: str) -> tuple[int, int, int]:
    """Split a #RRGGBB colour into red, green and blue."""
    value = hex_colour.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected a #RRGGBB colour but got {hex_colour!r}")
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return red, green, blue
