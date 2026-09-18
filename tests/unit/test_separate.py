"""Tests for deciding which pixels belong to which curve within a track.

Two properties carry the weight here. The first is that a curve's own ink
scores highest for that curve and lowest for its neighbour — get that backwards
and Track 3 reports density as porosity, which is a plausible-looking LAS file
that is entirely wrong. The second is that where two curves genuinely overlap,
BOTH keep evidence: this module's whole reason for returning scores instead of
masks is that it must not resolve an ambiguity it cannot see the answer to.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.detect.gridlines import detect_gridlines
from app.detect.regions import detect_regions
from app.extract.separate import by_colour, by_dash, separate_curves
from app.ingest.load_image import FORMAT_RGB, from_array, to_array
from app.las.mnemonics import SPWLA_MNEMONICS
from app.preprocess.remove_annotations import remove_annotations
from app.preprocess.remove_grid import remove_grid

GR = SPWLA_MNEMONICS["GR"]
SP = SPWLA_MNEMONICS["SP"]
NPHI = SPWLA_MNEMONICS["NPHI"]
RHOB = SPWLA_MNEMONICS["RHOB"]
ILD = SPWLA_MNEMONICS["ILD"]
ILM = SPWLA_MNEMONICS["ILM"]
RXO = SPWLA_MNEMONICS["RXO"]

# Colours as they are actually PRINTED on the reference scan, measured from it.
# They are not the display colours in the mnemonic table, and the difference is
# the point: printed gamma ray is olive, not the table's true green.
PRINTED_OLIVE_GR = (120, 136, 75)
PRINTED_BROWN_RHOB = (141, 112, 82)
PRINTED_BLACK = (47, 48, 47)
PAPER = (255, 255, 255)


def _track(height: int = 200, width: int = 120) -> np.ndarray:
    return np.full((height, width, 3), 255, dtype=np.uint8)


def _draw_column(track: np.ndarray, x: int, colour: tuple[int, int, int],
                 on: int = 0, off: int = 0) -> None:
    """Draw a 2px-wide vertical stroke, solid when on/off are left at zero."""
    for y in range(track.shape[0]):
        if on and (y % (on + off)) >= on:
            continue
        track[y, x : x + 2] = colour


# -- Dispatch -----------------------------------------------------------------

def test_a_track_with_two_ink_colours_is_separated_by_colour() -> None:
    track = _track()
    _draw_column(track, 30, PRINTED_BLACK)
    _draw_column(track, 80, PRINTED_OLIVE_GR)

    scores = separate_curves(track, [GR, SP])

    # The olive column must read as gamma ray, not as the black curve.
    assert scores["GR"][100, 80] > scores["SP"][100, 80]
    assert scores["SP"][100, 30] > scores["GR"][100, 30]


def test_a_track_printed_in_one_ink_is_separated_by_dash() -> None:
    """All three resistivity curves are black, so colour cannot be the route."""
    track = _track()
    _draw_column(track, 20, PRINTED_BLACK)
    _draw_column(track, 60, PRINTED_BLACK, on=8, off=4)
    _draw_column(track, 100, PRINTED_BLACK, on=2, off=3)

    scores = separate_curves(track, [ILD, ILM, RXO])

    assert scores["ILD"][100, 20] == max(s[100, 20] for s in scores.values())


def test_a_single_curve_track_claims_all_its_ink() -> None:
    track = _track()
    _draw_column(track, 50, PRINTED_BLACK)
    scores = separate_curves(track, [SP])
    assert scores["SP"][100, 50] == 1.0
    assert scores["SP"][100, 10] == 0.0


def test_a_greyscale_track_is_refused() -> None:
    """Colour is the evidence in two of three tracks; losing it must be loud."""
    with pytest.raises(ValueError, match="RGB"):
        separate_curves(np.zeros((10, 10), dtype=np.uint8), [SP])


def test_a_track_with_no_curves_is_refused() -> None:
    with pytest.raises(ValueError, match="no curves"):
        separate_curves(_track(), [])


# -- Colour -------------------------------------------------------------------

def test_paper_scores_zero_for_every_curve() -> None:
    track = _track()
    _draw_column(track, 30, PRINTED_BLACK)
    scores = by_colour(track, [GR, SP])
    assert scores["GR"][100, 90] == 0.0
    assert scores["SP"][100, 90] == 0.0


def test_printed_olive_is_gamma_ray_despite_the_table_calling_it_green() -> None:
    """The trap this module was written around.

    The table's gamma ray green (#4C7C2F) sits at hue 97 degrees; the scan
    prints the curve olive at 53-76 degrees, which is nearer the table's
    bulk-density brown. Any separation that compared absolute hue would swap
    the curves. Separation is on distance from grey instead.
    """
    track = _track()
    _draw_column(track, 50, PRINTED_OLIVE_GR)
    scores = by_colour(track, [GR, SP])
    assert scores["GR"][100, 50] == 1.0
    assert scores["SP"][100, 50] == 0.0


def test_printed_brown_is_density_not_neutron() -> None:
    track = _track()
    _draw_column(track, 50, PRINTED_BROWN_RHOB)
    scores = by_colour(track, [NPHI, RHOB])
    assert scores["RHOB"][100, 50] == 1.0
    assert scores["NPHI"][100, 50] == 0.0


def test_a_very_dark_pixel_is_black_not_coloured() -> None:
    """Saturation is a ratio and betrays you here.

    Near-black (11, 10, 7) computes to an HSV saturation of 93 out of 255 and
    would read as strongly coloured. Its absolute spread is 4, which is the
    truth: it is black ink.
    """
    track = _track()
    _draw_column(track, 50, (11, 10, 7))
    scores = by_colour(track, [GR, SP])
    assert scores["SP"][100, 50] == 1.0
    assert scores["GR"][100, 50] == 0.0


def test_blended_ink_where_two_curves_overlap_keeps_both_alive() -> None:
    """The reason this module returns scores and not masks.

    Where the spontaneous potential curve runs along the gamma ray curve, the
    ink is a blend of the two. Awarding it wholly to one curve would leave the
    other with no evidence at all for those rows, and a gap there cannot be
    recovered later. Both must stay in play for extract/trace.py to arbitrate.
    """
    track = _track()
    # Chroma 20: between the certainly-black cutoff of 12 and the certainly-
    # coloured cutoff of 30.
    _draw_column(track, 50, (110, 120, 100))
    scores = by_colour(track, [GR, SP])
    assert 0.0 < scores["GR"][100, 50] < 1.0
    assert 0.0 < scores["SP"][100, 50] < 1.0


def test_the_two_scores_of_a_blend_account_for_the_whole_pixel() -> None:
    track = _track()
    _draw_column(track, 50, (110, 120, 100))
    scores = by_colour(track, [GR, SP])
    assert scores["GR"][100, 50] + scores["SP"][100, 50] == pytest.approx(1.0)


def test_two_coloured_curves_in_one_track_are_refused() -> None:
    """Untested branches swap curves silently; a refusal is visible."""
    with pytest.raises(ValueError, match="one black and one coloured"):
        by_colour(_track(), [GR, RHOB])


def test_pale_fill_remnants_are_not_claimed_as_curve_ink() -> None:
    """Hollowed fills leave a rim, and a rim runs alongside a curve.

    At grey 150 the remaining rim of the grey hydrocarbon fill is paler than
    any curve. If it were treated as ink, every curve bounding a fill would
    appear to be several pixels wide on one side only, biasing its value.
    """
    track = _track()
    _draw_column(track, 50, (150, 150, 150))
    scores = by_colour(track, [GR, SP])
    assert scores["SP"][100, 50] == 0.0
    assert scores["GR"][100, 50] == 0.0


# -- Dash ---------------------------------------------------------------------

def test_a_solid_stroke_scores_highest_for_the_solid_curve() -> None:
    track = _track()
    _draw_column(track, 50, PRINTED_BLACK)
    scores = by_dash(track, [ILD, ILM, RXO])
    assert scores["ILD"][100, 50] > scores["ILM"][100, 50]
    assert scores["ILM"][100, 50] > scores["RXO"][100, 50]


def test_a_dashed_stroke_scores_highest_for_the_dashed_curve() -> None:
    track = _track()
    # The medium resistivity pattern from the mnemonic table: 8 on, 4 off.
    _draw_column(track, 50, PRINTED_BLACK, on=8, off=4)
    scores = by_dash(track, [ILD, ILM, RXO])
    ranked = max(scores, key=lambda m: scores[m][100, 50])
    assert ranked == "ILM"


def test_a_dotted_stroke_scores_highest_for_the_dotted_curve() -> None:
    track = _track()
    # The shallow resistivity pattern: 2 on, 3 off.
    _draw_column(track, 50, PRINTED_BLACK, on=2, off=3)
    scores = by_dash(track, [ILD, ILM, RXO])
    ranked = max(scores, key=lambda m: scores[m][100, 50])
    assert ranked == "RXO"


def test_dash_scores_are_shared_out_across_the_curves() -> None:
    track = _track()
    _draw_column(track, 50, PRINTED_BLACK, on=8, off=4)
    scores = by_dash(track, [ILD, ILM, RXO])
    assert sum(s[100, 50] for s in scores.values()) == pytest.approx(1.0)


def test_a_horizontal_run_of_the_solid_curve_is_still_solid() -> None:
    """Why stroke length is measured, not vertical run length.

    A curve turns and runs sideways at a bed boundary. Measured vertically, a
    horizontal segment has a run of two pixels and reads as a dotted curve,
    which would hand the deep resistivity's bed boundaries to the shallow one.
    """
    track = _track()
    track[100:102, 10:110] = PRINTED_BLACK
    scores = by_dash(track, [ILD, ILM, RXO])
    assert scores["ILD"][100, 60] > scores["RXO"][100, 60]


def test_curves_sharing_ink_and_line_style_are_refused() -> None:
    with pytest.raises(ValueError, match="nothing on the page distinguishes"):
        by_dash(_track(), [ILD, SPWLA_MNEMONICS["NPHI"]])


# -- Against the real scan ----------------------------------------------------

@pytest.fixture(scope="module")
def cleaned_tracks(scan_rgb: np.ndarray) -> dict[str, np.ndarray]:
    """Each track of the reference scan, cleaned and cropped to its data area.

    Annotations are removed BEFORE the grid: whitening the grid rows first
    slices each fill into horizontal bands and leaves every band's rim behind.
    """
    image = from_array(scan_rgb, FORMAT_RGB)
    grid = detect_gridlines(image)
    layout = detect_regions(image, grid)
    array = to_array(remove_grid(remove_annotations(image), grid))
    return {
        track.name: array[
            layout.data_top + 1 : layout.data_bottom,
            # Inset past the heavy separator so the rule is not read as ink.
            track.x_left + 3 : track.x_right - 2,
        ]
        for track in layout.tracks
    }


def _longest_gap(score: np.ndarray) -> int:
    """The most consecutive depth rows on which a curve has no evidence at all."""
    empty = score.sum(axis=1) == 0
    longest = run = 0
    for is_empty in empty:
        run = run + 1 if is_empty else 0
        longest = max(longest, run)
    return longest


def _rows_with_evidence(score: np.ndarray) -> int:
    """Depth rows on which this curve has been seen at all."""
    return int((score.max(axis=1) > 0).sum())


def _typical_strength(score: np.ndarray) -> float:
    """Median strength of the best pixel per row, over the rows that have one.

    Median rather than mean because the distribution is deliberately two-humped:
    a curve on clean paper scores 1.0, and the same curve where it runs into a
    colour fill scores the wrong-lean floor. Averaging those hides both.
    """
    per_row = score.max(axis=1)
    return float(np.median(per_row[per_row > 0]))


def test_the_real_gamma_ray_and_sp_are_told_apart(cleaned_tracks) -> None:
    """Both curves run the whole log, so both must be clearly present.

    The rows that remain empty are where the two curves physically coincide and
    the sheet only shows one of them. Those are short and scattered; a colour
    rule that had actually lost a curve would leave one long blind interval,
    which is what the gap check catches.
    """
    scores = separate_curves(cleaned_tracks["Track 1"], [GR, SP])
    rows = scores["GR"].shape[0]
    for mnemonic in ("GR", "SP"):
        assert _rows_with_evidence(scores[mnemonic]) > 0.75 * rows, mnemonic
        assert _typical_strength(scores[mnemonic]) > 0.75, mnemonic
        # 25 rows is about 13 ft at this sheet's 0.52 ft per pixel — an
        # interval extract/trace.py can bridge on the continuity prior.
        assert _longest_gap(scores[mnemonic]) <= 25, mnemonic


def test_the_real_neutron_and_density_are_told_apart(cleaned_tracks) -> None:
    scores = separate_curves(cleaned_tracks["Track 3"], [NPHI, RHOB])
    rows = scores["NPHI"].shape[0]
    for mnemonic in ("NPHI", "RHOB"):
        assert _rows_with_evidence(scores[mnemonic]) > 0.75 * rows, mnemonic
        assert _typical_strength(scores[mnemonic]) > 0.75, mnemonic
        assert _longest_gap(scores[mnemonic]) <= 25, mnemonic


def test_the_sp_curve_is_not_duplicated_into_the_gamma_ray_map(
    cleaned_tracks,
) -> None:
    """The defect the channel-ordering rule was added to fix.

    Where the black curve runs along the edge of the yellow sand body its ink
    blends with the fill and reads as coloured. Scored on distance from grey
    alone, a second smooth curve — the SP curve, in the wrong units — appeared
    in the gamma ray map, and nothing downstream could have detected it.

    Measured beside the black curve, not across the whole row: the gamma ray
    curve's own ink is on those rows too, and comparing against it is the
    point. The ghost has to be clearly the weaker of the two.
    """
    scores = separate_curves(cleaned_tracks["Track 1"], [GR, SP])
    rows = np.flatnonzero(scores["SP"].max(axis=1) > 0.9)

    # Ink within three pixels of the black curve is the blend that produced the
    # ghost; the blend is beside the stroke, not on it.
    ghost = []
    genuine = []
    for row in rows:
        column = int(np.argmax(scores["SP"][row]))
        low, high = max(column - 3, 0), column + 4
        ghost.append(float(scores["GR"][row, low:high].max()))
        genuine.append(float(scores["GR"][row].max()))

    assert np.median(ghost) < 0.5 * np.median(genuine)


def test_the_real_density_curve_is_found_to_the_right_of_the_neutron(
    cleaned_tracks,
) -> None:
    """A weak but decisive check that the two were not swapped.

    Over most of this log the density curve is printed to the right of the
    neutron curve. If the colour rule had them the wrong way round, the mean
    positions would swap, and the resulting LAS would be confidently wrong
    rather than obviously broken.
    """
    scores = separate_curves(cleaned_tracks["Track 3"], [NPHI, RHOB])
    columns = np.arange(scores["NPHI"].shape[1])
    centre = {
        mnemonic: float((score * columns).sum() / score.sum())
        for mnemonic, score in scores.items()
    }
    assert centre["RHOB"] > centre["NPHI"]


def test_the_real_resistivity_curves_are_told_apart(cleaned_tracks) -> None:
    scores = separate_curves(cleaned_tracks["Track 2"], [ILD, ILM, RXO])
    # The deep curve is solid, so it is inked on effectively every row.
    solid_rows = int((scores["ILD"].sum(axis=1) > 0).sum())
    assert solid_rows > 0.95 * scores["ILD"].shape[0]
    # The dashed and dotted curves are inked on fewer rows by construction, but
    # a curve that had vanished entirely would show far less than half.
    for mnemonic in ("ILM", "RXO"):
        rows = int((scores[mnemonic].sum(axis=1) > 0).sum())
        assert rows > 0.5 * scores[mnemonic].shape[0], mnemonic
