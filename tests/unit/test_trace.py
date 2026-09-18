"""Tests for following a curve down the page under the continuity prior.

The tests are built around the four situations the prior exists for, because
each of them produces a plausible-looking LAS file when it goes wrong rather
than an obvious failure:

  * a crossing, where two candidate columns exist and only one is continuous;
  * an occlusion, where the curve is hidden and the path must carry across it
    rather than jump to whatever else is nearby;
  * a real bed boundary, where the curve genuinely does move a long way fast
    and the prior must NOT smooth it away;
  * a leftover leader line, which is a short sideways excursion into nothing.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.detect.gridlines import detect_gridlines
from app.detect.regions import detect_regions
from app.extract.separate import separate_curves
from app.extract.trace import trace_curve
from app.ingest.load_image import FORMAT_RGB, from_array, to_array
from app.las.mnemonics import SPWLA_MNEMONICS
from app.preprocess.remove_annotations import remove_annotations
from app.preprocess.remove_grid import remove_grid


def _blank(rows: int = 200, columns: int = 150) -> np.ndarray:
    return np.zeros((rows, columns), dtype=np.float32)


def _draw(affinity: np.ndarray, column: int, top: int = 0, bottom: int | None = None,
          strength: float = 1.0, width: int = 1) -> None:
    bottom = affinity.shape[0] if bottom is None else bottom
    affinity[top:bottom, column : column + width] = strength


# -- The basics ---------------------------------------------------------------

def test_a_straight_curve_is_followed_exactly() -> None:
    affinity = _blank()
    _draw(affinity, 40)
    path = trace_curve(affinity)
    assert all(column == 40.0 for column in path.columns)


def test_there_is_one_sample_per_depth_row() -> None:
    affinity = _blank(rows=57)
    _draw(affinity, 40)
    path = trace_curve(affinity)
    assert len(path.columns) == 57
    assert len(path.confidence) == 57


def test_a_sloping_curve_is_followed() -> None:
    affinity = _blank()
    for row in range(200):
        affinity[row, 20 + row // 4] = 1.0
    path = trace_curve(affinity)
    assert path.columns[0] == 20.0
    assert path.columns[-1] == pytest.approx(20 + 199 // 4)


def test_confidence_is_the_evidence_at_the_chosen_pixel() -> None:
    affinity = _blank()
    _draw(affinity, 40, strength=0.6)
    path = trace_curve(affinity)
    assert all(value == pytest.approx(0.6, abs=1e-6) for value in path.confidence)


def test_an_affinity_map_of_the_wrong_shape_is_refused() -> None:
    with pytest.raises(ValueError, match="rows, columns"):
        trace_curve(np.zeros((10, 10, 3), dtype=np.float32))


def test_an_empty_track_is_refused() -> None:
    with pytest.raises(ValueError, match="empty track"):
        trace_curve(np.zeros((0, 0), dtype=np.float32))


# -- The continuity prior -----------------------------------------------------

def test_at_a_crossing_the_continuous_path_wins() -> None:
    """The reason tracing and the prior are one operation.

    A second curve crosses this one. On the rows where they meet there are two
    inked columns and nothing local distinguishes them; only the fact that one
    of them continues where the curve already was settles it.
    """
    affinity = _blank()
    _draw(affinity, 60)
    for row in range(200):
        affinity[row, 10 + row // 2] = 1.0  # a diagonal crossing at row 100

    path = trace_curve(affinity)
    # Away from the crossing the path must be on the vertical curve, not the
    # diagonal, and it must still be there after passing through.
    assert path.columns[20] == 60.0
    assert path.columns[180] == 60.0


def test_a_short_occlusion_is_carried_across_rather_than_jumped_over() -> None:
    """What happens where two curves physically coincide.

    On the reference sheet the gamma ray curve disappears behind the black
    curve for up to 16 rows. The faint ghost of the other curve is the only
    other thing inked. Following it would report the wrong curve's values in
    the right curve's units, which no later check could detect.
    """
    affinity = _blank()
    _draw(affinity, 20, bottom=100)
    _draw(affinity, 20, top=116)
    _draw(affinity, 70, strength=0.15)  # the ghost, 50 px away

    path = trace_curve(affinity)
    assert all(column == 20.0 for column in path.columns[100:116])


def test_an_occluded_interval_is_marked_unobserved() -> None:
    """A carried path is not a measurement and must not be presented as one."""
    affinity = _blank()
    _draw(affinity, 20, bottom=100)
    _draw(affinity, 20, top=116)

    path = trace_curve(affinity)
    assert all(value == 0.0 for value in path.confidence[100:116])
    assert path.confidence[99] == 1.0
    assert path.confidence[116] == 1.0


def test_a_real_bed_boundary_is_followed_not_smoothed() -> None:
    """The prior must not become a low-pass filter.

    Rock changes abruptly, and a log curve moves right across the track when it
    does. Rounding that off would erase the bed boundary that the whole log was
    run to find.
    """
    affinity = _blank()
    _draw(affinity, 20, bottom=50)
    _draw(affinity, 120, top=50)

    path = trace_curve(affinity)
    assert path.columns[40] == 20.0
    assert path.columns[60] == 120.0


def test_a_leftover_leader_line_is_not_followed() -> None:
    """The backstop for what remove_annotations cannot safely erase.

    A label whose leader line touches its curve becomes one shape with it and
    is deliberately left in place. It shows as a short horizontal stroke: cheap
    to step onto, expensive to come back from, and worth nothing on the way.
    """
    affinity = _blank()
    _draw(affinity, 40)
    affinity[100:103, 40:110] = 1.0  # the leader, running 70 px sideways

    path = trace_curve(affinity)
    assert path.columns[101] < 45.0


def test_a_stronger_ghost_still_loses_to_an_unbroken_curve() -> None:
    affinity = _blank()
    _draw(affinity, 30, strength=0.9)
    _draw(affinity, 100, strength=1.0, top=95, bottom=105)

    path = trace_curve(affinity)
    assert all(column == 30.0 for column in path.columns)


def test_a_curve_that_was_never_printed_is_reported_as_unobserved() -> None:
    """No ink anywhere must not become a confident column of numbers."""
    path = trace_curve(_blank())
    assert all(value == 0.0 for value in path.confidence)


# -- Sub-pixel placement ------------------------------------------------------

def test_the_centre_of_a_two_pixel_stroke_falls_between_them() -> None:
    affinity = _blank()
    _draw(affinity, 40, width=2)
    path = trace_curve(affinity)
    assert path.columns[10] == pytest.approx(40.5)


def test_the_centre_is_weighted_towards_the_darker_side() -> None:
    """Anti-aliasing puts the true centre off the strongest pixel."""
    affinity = _blank()
    affinity[:, 40] = 1.0
    affinity[:, 41] = 0.5
    path = trace_curve(affinity)
    assert 40.0 < path.columns[10] < 40.5


def test_a_long_horizontal_run_does_not_drag_the_reported_centre() -> None:
    """Why the sub-pixel search is capped.

    Where a curve runs along a leader line or the rim of a fill, the inked run
    through the chosen pixel can be a hundred pixels wide. Its centroid is a
    value that was never printed anywhere on the sheet.
    """
    affinity = _blank()
    _draw(affinity, 40)
    affinity[100, 40:140] = 1.0
    path = trace_curve(affinity)
    assert path.columns[100] < 45.0


def test_a_carried_row_keeps_the_column_it_was_carried_to() -> None:
    affinity = _blank()
    _draw(affinity, 20, bottom=100)
    _draw(affinity, 20, top=116)
    path = trace_curve(affinity)
    assert path.columns[108] == 20.0


# -- Against the real scan ----------------------------------------------------

@pytest.fixture(scope="module")
def traced(scan_rgb: np.ndarray) -> dict[str, object]:
    """Every curve on the reference sheet, separated and then traced."""
    image = from_array(scan_rgb, FORMAT_RGB)
    grid = detect_gridlines(image)
    layout = detect_regions(image, grid)
    array = to_array(remove_grid(remove_annotations(image), grid))

    by_track = {
        "Track 1": ("GR", "SP"),
        "Track 2": ("ILD", "ILM", "RXO"),
        "Track 3": ("NPHI", "RHOB"),
    }
    paths: dict[str, object] = {}
    for track in layout.tracks:
        crop = array[
            layout.data_top + 1 : layout.data_bottom,
            track.x_left + 3 : track.x_right - 2,
        ]
        specs = [SPWLA_MNEMONICS[m] for m in by_track[track.name]]
        for mnemonic, affinity in separate_curves(crop, specs).items():
            paths[mnemonic] = trace_curve(affinity)
    return paths


def test_every_curve_on_the_sheet_is_traced_end_to_end(traced) -> None:
    assert set(traced) == {"GR", "SP", "ILD", "ILM", "RXO", "NPHI", "RHOB"}
    for mnemonic, path in traced.items():
        assert len(path.columns) == 577, mnemonic


def test_most_of_every_curve_is_actually_observed(traced) -> None:
    """A path is always produced; the confidence says how much of it is real.

    A curve carried across most of the log would still return 577 columns, so
    this is the check that distinguishes a trace from a straight line drawn
    through an empty track.

    The bar depends on the line style, because a dashed curve is genuinely not
    printed between its dashes. The medium resistivity's pattern is 8 on and 4
    off, so it can never be observed on more than two thirds of rows however
    well it is traced, and holding it to the same standard as a solid curve
    would only teach the tracer to invent ink.
    """
    for mnemonic, path in traced.items():
        dash = SPWLA_MNEMONICS[mnemonic].dash
        printed_fraction = dash[0] / (dash[0] + dash[1]) if dash else 1.0
        observed = sum(1 for value in path.confidence if value > 0.0)
        assert observed > 0.75 * printed_fraction * len(path.confidence), mnemonic


def test_the_traced_curves_stay_inside_their_track(traced) -> None:
    for mnemonic, path in traced.items():
        assert min(path.columns) >= 0.0, mnemonic
        assert max(path.columns) <= 277.0, mnemonic


def test_the_three_resistivity_curves_are_traced_apart(traced) -> None:
    """Deep, medium and shallow must not collapse onto one another.

    They are printed in the same ink and only the line style separates them, so
    a failure here looks like three identical curves — which is a physically
    meaningful reading (an invaded-zone-free formation) and therefore will not
    be questioned by anyone reading the LAS.
    """
    deep = np.array(traced["ILD"].columns)
    medium = np.array(traced["ILM"].columns)
    shallow = np.array(traced["RXO"].columns)
    assert float(np.mean(np.abs(deep - medium))) > 5.0
    assert float(np.mean(np.abs(medium - shallow))) > 5.0


def test_the_density_curve_stays_right_of_the_neutron_curve(traced) -> None:
    """Over this log the two are separated, and swapping them is undetectable.

    Checked on the traced paths rather than the affinity maps so that a tracer
    that crossed from one curve onto the other is caught, which the separation
    test upstream cannot see.
    """
    neutron = np.array(traced["NPHI"].columns)
    density = np.array(traced["RHOB"].columns)
    assert float(np.mean(density - neutron)) > 0.0


def test_no_traced_curve_teleports_across_the_track(traced) -> None:
    """A step of half the track in one row is not rock, it is a tracing error.

    Real bed boundaries move a curve a long way, but over several rows. This
    catches the path jumping onto a different curve entirely.
    """
    for mnemonic, path in traced.items():
        steps = np.abs(np.diff(np.array(path.columns)))
        assert float(steps.max()) < 140.0, mnemonic
