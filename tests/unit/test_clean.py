"""Tests for removing tracing artefacts and putting a curve on a fixed step.

Both operations are places where it would be easy, and disastrous, to invent
data. A despiker that patches a hole with the local median produces a file in
which fabricated readings are indistinguishable from measured ones. A resampler
that interpolates across a 50 ft interval where the curve was invisible draws a
straight line through rock nobody looked at. Most of these tests exist to pin
those two behaviours shut.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.contracts import CWLS_NULL_VALUE, CurveSample, CurveTrace, PixelPath
from app.extract.clean import despike, resample, sampling_grid


def _trace(values: list[float], start: float = 7000.0, step: float = 0.5,
           confidence: float = 1.0) -> CurveTrace:
    """A gamma ray curve with one sample per entry, NaN meaning NULL."""
    return CurveTrace(
        mnemonic="GR",
        unit="GAPI",
        description="Gamma Ray",
        samples=[
            CurveSample(
                depth=start + index * step,
                value=CWLS_NULL_VALUE if np.isnan(value) else value,
                confidence=0.0 if np.isnan(value) else confidence,
            )
            for index, value in enumerate(values)
        ],
    )


def _path(columns: list[float]) -> PixelPath:
    """A traced path, NaN meaning the curve was not visible on that row."""
    return PixelPath(
        columns=tuple(0.0 if np.isnan(c) else c for c in columns),
        confidence=tuple(0.0 if np.isnan(c) else 1.0 for c in columns),
    )


def _values(trace: CurveTrace) -> list[float]:
    return [sample.value for sample in trace.samples]


def _observed(path: PixelPath) -> list[bool]:
    return [value > 0.0 for value in path.confidence]


# -- Despiking ----------------------------------------------------------------

def test_a_single_row_excursion_is_removed() -> None:
    columns = [50.0] * 20
    columns[10] = 130.0
    cleaned = despike(_path(columns))
    assert cleaned.confidence[10] == 0.0


def test_a_removed_spike_is_not_replaced_with_a_plausible_value() -> None:
    """Deleting is honest; patching is fabrication.

    A column interpolated from the neighbours becomes a value in the LAS file
    indistinguishable from a measured one, and nothing downstream can tell them
    apart. Zero confidence is what makes the row a NULL later.
    """
    columns = [50.0] * 20
    columns[10] = 130.0
    cleaned = despike(_path(columns))
    assert cleaned.confidence[10] == 0.0
    assert sum(_observed(cleaned)) == 19


def test_a_real_bed_boundary_is_kept() -> None:
    """The distinction the whole method rests on.

    A spike goes out and comes straight back. A bed boundary goes and stays. A
    short running median follows the second and ignores the first, which is why
    the window is five rows and not fifty.
    """
    cleaned = despike(_path([30.0] * 20 + [190.0] * 20))
    assert all(_observed(cleaned))


def test_ordinary_tracing_jitter_is_kept() -> None:
    """Over-removal leaves holes a petrophysicist will not forgive."""
    generator = np.random.default_rng(seed=0)
    cleaned = despike(_path(list(50.0 + generator.normal(0.0, 0.6, size=200))))
    assert all(_observed(cleaned))


def test_sub_pixel_wiggle_is_never_treated_as_a_spike() -> None:
    """The defect that moved despiking into pixel space.

    Sub-pixel centring makes a traced curve very smooth, so five robust sigmas
    can come out below a single pixel. Measured against that, the tracer's own
    rounding looks like a spike: on the reference sheet it deleted 6% of the
    neutron curve at a threshold of half a pixel. Nothing finer than the width
    of a printed stroke can be distinguished from where the curve simply is.
    """
    columns = [50.0] * 40
    columns[20] = 51.0  # one pixel out, which is as precise as tracing gets
    cleaned = despike(_path(columns))
    assert all(_observed(cleaned))


def test_a_perfectly_smooth_curve_survives() -> None:
    """A curve lying on its own running median has no spikes to find.

    Before the pixel floor existed this case had to be special-cased, because
    the robust scale came out at exactly zero and every row was infinitely
    deviant. The floor removes the need: the threshold can never reach zero.
    """
    cleaned = despike(_path([42.0] * 30))
    assert all(_observed(cleaned))


def test_rows_already_unobserved_stay_unobserved() -> None:
    columns = [50.0] * 20
    columns[7] = float("nan")
    cleaned = despike(_path(columns))
    assert cleaned.confidence[7] == 0.0
    assert cleaned.confidence[6] == 1.0


def test_a_path_too_short_to_judge_is_returned_untouched() -> None:
    path = _path([10.0, 90.0, 10.0])
    assert despike(path) == path


def test_an_empty_path_is_refused() -> None:
    with pytest.raises(ValueError, match="no rows"):
        despike(PixelPath(columns=(), confidence=()))


def test_despiking_leaves_the_columns_alone() -> None:
    """The column is still the best guess at where the path ran.

    Nothing reads it once the confidence is zero, and rewriting it would only
    hide where the artefact was from anyone debugging the trace.
    """
    columns = [50.0] * 20
    columns[10] = 130.0
    cleaned = despike(_path(columns))
    assert cleaned.columns[10] == 130.0


# -- Resampling ---------------------------------------------------------------

def test_the_output_is_on_a_uniform_step() -> None:
    resampled = resample(_trace([50.0] * 20, step=0.52), step=0.5, max_gap=5.0)
    depths = np.array([sample.depth for sample in resampled.samples])
    assert np.allclose(np.diff(depths), 0.5)


def test_the_grid_starts_on_a_round_multiple_of_the_step() -> None:
    """So that every curve from one sheet shares a single depth index.

    A LAS file has one depth column for all curves. If two curves were
    resampled onto grids offset from one another, they could not be written to
    the same file without resampling one of them a second time.
    """
    resampled = resample(_trace([50.0] * 20, start=7000.3), step=0.5, max_gap=5.0)
    assert resampled.samples[0].depth == pytest.approx(7000.5)


def test_a_value_between_two_samples_is_interpolated() -> None:
    resampled = resample(
        _trace([10.0, 20.0], start=7000.0, step=1.0), step=0.5, max_gap=5.0
    )
    assert resampled.samples[1].value == pytest.approx(15.0)


def test_a_short_gap_is_bridged() -> None:
    """A dashed curve is not printed between its dashes.

    The medium resistivity curve's pattern is 8 on, 4 off, which at this
    sheet's resolution leaves about two feet unprinted between dashes. Leaving
    those as NULL would report two thirds of a perfectly legible curve as
    missing.
    """
    values = [50.0, float("nan"), float("nan"), 56.0]
    resampled = resample(_trace(values, step=1.0), step=0.5, max_gap=5.0)
    assert CWLS_NULL_VALUE not in _values(resampled)


def test_a_long_gap_is_left_null() -> None:
    """An interval where the curve was not visible was not measured."""
    values = [50.0] + [float("nan")] * 30 + [56.0]
    resampled = resample(_trace(values, step=1.0), step=0.5, max_gap=5.0)
    assert CWLS_NULL_VALUE in _values(resampled)


def test_the_ends_of_a_long_gap_are_still_written() -> None:
    values = [50.0] + [float("nan")] * 30 + [56.0]
    resampled = resample(_trace(values, step=1.0), step=0.5, max_gap=5.0)
    assert resampled.samples[0].value == pytest.approx(50.0)
    assert resampled.samples[-1].value == pytest.approx(56.0)


def test_a_bridged_sample_inherits_the_weaker_confidence() -> None:
    """Interpolating between a crisp reading and a doubtful one is doubtful."""
    trace = CurveTrace(
        mnemonic="GR", unit="GAPI", description="Gamma Ray",
        samples=[
            CurveSample(depth=7000.0, value=50.0, confidence=1.0),
            CurveSample(depth=7002.0, value=60.0, confidence=0.2),
        ],
    )
    resampled = resample(trace, step=0.5, max_gap=5.0)
    assert resampled.samples[1].confidence == pytest.approx(0.2)


def test_a_sample_landing_on_an_observed_depth_is_never_treated_as_a_gap() -> None:
    """The bracketing must include the sample itself.

    Otherwise a grid depth sitting exactly on an observed sample is judged
    against the distance to its neighbours, and a perfectly good reading
    surrounded by a sparse interval is thrown away.
    """
    trace = CurveTrace(
        mnemonic="GR", unit="GAPI", description="Gamma Ray",
        samples=[
            CurveSample(depth=7000.0, value=50.0, confidence=1.0),
            CurveSample(depth=7050.0, value=60.0, confidence=1.0),
        ],
    )
    resampled = resample(trace, step=50.0, max_gap=5.0)
    assert resampled.samples[0].value == pytest.approx(50.0)
    assert resampled.samples[-1].value == pytest.approx(60.0)


def test_the_grid_never_runs_past_the_last_observed_depth() -> None:
    """Extrapolation beyond the log is not a gap, it is invention."""
    resampled = resample(_trace([50.0] * 5, start=7000.0, step=0.3), step=0.5, max_gap=5.0)
    assert resampled.samples[-1].depth <= 7000.0 + 4 * 0.3


def test_a_step_finer_than_zero_is_refused() -> None:
    with pytest.raises(ValueError, match="step must be positive"):
        resample(_trace([50.0] * 5), step=0.0, max_gap=5.0)


def test_a_gap_allowance_of_zero_is_refused() -> None:
    with pytest.raises(ValueError, match="gap must be positive"):
        resample(_trace([50.0] * 5), step=0.5, max_gap=0.0)


def test_a_curve_that_was_never_observed_is_refused() -> None:
    """Returning an empty curve would put an empty column in the LAS silently."""
    with pytest.raises(ValueError, match="at least two"):
        resample(_trace([float("nan")] * 10), step=0.5, max_gap=5.0)


def test_resampling_keeps_the_curve_identity() -> None:
    resampled = resample(_trace([50.0] * 20), step=0.5, max_gap=5.0)
    assert (resampled.mnemonic, resampled.unit, resampled.description) == (
        "GR", "GAPI", "Gamma Ray"
    )


# -- The two together ---------------------------------------------------------

def test_a_spike_removed_by_despiking_is_not_reinstated_by_resampling() -> None:
    """The two run in a fixed order and this is what it buys.

    Despiking marks a row unobserved while the curve is still in pixels. By the
    time resampling sees it the row is NULL, so if the hole is short enough to
    bridge, a line is drawn across it — which is right, because the rock either
    side was measured and the spike was not rock. What must not happen is the
    original spike value reappearing.
    """
    columns = [50.0] * 20
    columns[10] = 130.0
    cleaned = despike(_path(columns))

    # Calibration is a straight scaling here: one pixel is one unit.
    trace = CurveTrace(
        mnemonic="GR", unit="GAPI", description="Gamma Ray",
        samples=[
            CurveSample(
                depth=7000.0 + index * 0.5,
                value=CWLS_NULL_VALUE if quality == 0.0 else column,
                confidence=quality,
            )
            for index, (column, quality) in enumerate(
                zip(cleaned.columns, cleaned.confidence)
            )
        ],
    )

    resampled = resample(trace, step=0.5, max_gap=5.0)
    assert max(_values(resampled)) < 100.0


# -- Choosing the depth step and the bridgeable gap ---------------------------

def test_the_reference_sheet_gets_the_standard_half_foot_step() -> None:
    """0.52002 ft per pixel must land on 0.5 ft, the wireline convention.

    This is the number the reference scan actually produces, and the reason
    rounding up was rejected: it would give 0.55 ft.
    """
    step, _ = sampling_grid(0.52002)

    assert step == pytest.approx(0.5)


def test_the_step_is_a_round_fraction_of_a_foot() -> None:
    """A step of 0.52002 would be arithmetically honest and unusable."""
    for resolution in (0.31, 0.47, 0.68, 1.03):
        step, _ = sampling_grid(resolution)
        assert step == pytest.approx(round(step / 0.05) * 0.05)


def test_the_step_tracks_the_sheets_resolution() -> None:
    """A coarser scan must get a coarser step; detail cannot be invented."""
    fine, _ = sampling_grid(0.25)
    coarse, _ = sampling_grid(1.0)

    assert fine < coarse


def test_a_very_fine_scan_still_gets_a_positive_step() -> None:
    """Rounding to the nearest 0.05 would otherwise snap to zero."""
    step, _ = sampling_grid(0.001)

    assert step > 0


def test_the_bridgeable_gap_is_several_pixels_not_several_feet() -> None:
    """It must cover a dashed curve's blank stretch and nothing longer."""
    step, max_gap = sampling_grid(0.52002)

    assert max_gap == pytest.approx(5.2002)
    assert max_gap > step  # or a dashed curve could never be joined up


def test_a_failed_depth_fit_is_refused() -> None:
    """A non-positive resolution means every depth downstream is meaningless."""
    with pytest.raises(ValueError, match="must be positive"):
        sampling_grid(0.0)


def test_a_negative_resolution_is_refused() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        sampling_grid(-0.5)


def test_the_chosen_grid_is_one_resample_accepts() -> None:
    """The two functions must agree, or nothing can be written."""
    step, max_gap = sampling_grid(0.52002)
    trace = _trace([10.0, 11.0, 12.0, 13.0, 14.0])

    resampled = resample(trace, step, max_gap)

    assert len(resampled.samples) > 0

