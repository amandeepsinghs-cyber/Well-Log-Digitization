"""Remove tracing artefacts from a curve and put it on a fixed depth step.

In : the traced path of a curve, and later the calibrated curve itself.
Out: the path with artefact rows marked unobserved, and the curve on a uniform
     depth step ready to be written to LAS.
Rule: neither operation may invent a reading. A spike is deleted, not replaced
      with a plausible neighbour, because a fabricated value is indistinguishable
      from a measured one once it is in the file. And resampling carries values
      across the short gaps a dashed line leaves between its dashes, but never
      across an interval where the curve was genuinely not visible: that stays
      NULL, which is the one honest thing to write.

      The two halves work on different things on purpose. Despiking is done in
      PIXELS, before calibration, because a spike is a tracing error and the
      tracer works in pixels — and because a threshold in pixels means the same
      thing everywhere on the page, which a threshold in curve units does not.
      On a logarithmic resistivity track, a fixed number of ohm-metres is a
      hundredth of a pixel at the top of the scale and a third of the track at
      the bottom; measured that way the despiker deleted a twentieth of every
      curve. Resampling, by contrast, is a statement about depth, so it belongs
      after the depth calibration.
"""

from __future__ import annotations

import logging

import numpy as np
from scipy.ndimage import median_filter

try:
    from app.contracts import CWLS_NULL_VALUE, CurveSample, CurveTrace, PixelPath
except ImportError:
    from contracts import CWLS_NULL_VALUE, CurveSample, CurveTrace, PixelPath

logger = logging.getLogger(__name__)

# Rows the running median is taken over. Five is enough: a spike is a tracing
# error at a single depth, so the window only has to be wide enough that the
# spike cannot be the median — three would be swayed by two adjacent bad rows,
# five will not be.
#
# Widening to seven was tried, to see whether it would also clean up the two
# near-straight runs in the gamma ray at the top and base of the sand. It did
# not, and it should not have: those runs are not spikes. They are the tracer
# bridging the gaps where the "Shale" callouts and their leader lines sit on
# top of the curve — a gap that resample() fills with a straight line, by
# design, and that despiking cannot see because there is no sample there to
# reject. Widening removed one further sample out of 599 and is not kept.
_DESPIKE_WINDOW = 5

# How far from the running median a sample must be before it is treated as an
# artefact, in robust standard deviations of the path's own residual. Five is
# deliberately timid. Over-removal is the worse failure here: a deleted sample
# leaves a hole in the log that a petrophysicist will notice and distrust,
# whereas a surviving spike is visible and can be judged.
_DESPIKE_SIGMAS = 5.0

# ...and never less than this many pixels, however quiet the curve is. A
# well-traced curve is smooth enough that five robust sigmas can fall below one
# pixel, at which point the test is measuring the tracer's own rounding and
# deletes perfectly good samples — it removed 6% of the neutron curve at a
# threshold of half a pixel before this floor was added. Three pixels is the
# width of a printed stroke plus the sub-pixel centring's own uncertainty, so
# nothing below it can be distinguished from where the curve simply is.
_DESPIKE_MIN_PIXELS = 3.0

# Converts a median absolute deviation into the equivalent standard deviation
# for normally distributed noise. The median absolute deviation is used rather
# than the standard deviation because the spikes being looked for would
# otherwise inflate the very scale they are measured against.
_MAD_TO_SIGMA = 1.4826


def despike(path: PixelPath) -> PixelPath:
    """Mark as unobserved any row whose column jumps away and straight back.

    Args:
        path: the curve as traced, in pixel columns.

    Returns:
        A new PixelPath with artefact rows set to zero confidence, which is how
        every later stage recognises a row that was not measured. The columns
        are left as they are: they are still the best guess at where the path
        ran, and nothing reads them once the confidence is zero.

    Raises:
        ValueError: if the path has no rows.
    """
    if not path.columns:
        raise ValueError("Cannot despike a path with no rows")

    observed = [
        index for index, value in enumerate(path.confidence) if value > 0.0
    ]
    if len(observed) < _DESPIKE_WINDOW:
        # Too short for a running median to mean anything. Nothing is removed,
        # which is the safe direction.
        return path

    columns = np.array([path.columns[index] for index in observed], dtype=float)
    residual = columns - median_filter(columns, size=_DESPIKE_WINDOW, mode="nearest")

    scale = _MAD_TO_SIGMA * float(np.median(np.abs(residual - np.median(residual))))
    threshold = max(_DESPIKE_SIGMAS * scale, _DESPIKE_MIN_PIXELS)
    spikes = np.abs(residual) > threshold

    confidence = list(path.confidence)
    for position, is_spike in enumerate(spikes):
        if is_spike:
            confidence[observed[position]] = 0.0

    logger.info(
        "despike: OK - %d of %d observed row(s) removed beyond %.2f px",
        int(spikes.sum()),
        len(observed),
        threshold,
    )
    return PixelPath(columns=path.columns, confidence=tuple(confidence))


# Longest run of unprinted depth that resampling may bridge, in PIXELS of the
# source scan. Ten covers the two things that legitimately interrupt a curve:
# the blank stretch between the dashes of a dashed resistivity curve, and the
# few rows lost where two curves touch and one is drawn over the other. It does
# not cover a label sitting on the curve, which is longer and must stay NULL.
_BRIDGEABLE_GAP_PIXELS = 10

# Depth steps are conventionally written as a round fraction of a foot, so the
# computed step is snapped to the nearest 0.05. Writing 0.52002 ft because that
# happens to be one pixel would be arithmetically honest and practically
# unusable: no tool would show a depth index that could be read off against the
# paper. On the reference sheet this yields 0.5 ft, which is the standard
# wireline sampling interval and almost certainly what the original tool used.
#
# Snapping to the NEAREST multiple, not up, can leave the step as much as 0.025
# finer than one pixel — 5% on this sheet. That is accepted: resample()
# interpolates linearly between adjacent pixel readings, so the extra samples
# carry sub-pixel error, which is the same error already documented as
# negligible there. Rounding up instead would give 0.55 ft here, a step no
# petrophysicist has ever seen on a log.
_STEP_ROUNDING = 0.05


def sampling_grid(depth_per_pixel: float) -> tuple[float, float]:
    """Choose the depth step and bridgeable gap for a sheet, from its resolution.

    Both follow from one fact: a pixel is the finest distinction the paper
    holds. Sampling far finer than a pixel claims detail that was never
    printed, and bridging a gap longer than a few pixels invents curve that was
    never drawn. Returning them together keeps the pair consistent — they are
    two answers to the same question and are always used as a pair.

    Args:
        depth_per_pixel: the sheet's depth resolution, from DepthCalibration.

    Returns:
        (step, max_gap), both in the sheet's depth units.

    Raises:
        ValueError: if the resolution is not positive, which would mean the
            depth calibration failed and every depth downstream is meaningless.
    """
    if depth_per_pixel <= 0:
        raise ValueError(
            f"Depth resolution must be positive to choose a sampling step, got "
            f"{depth_per_pixel}. The depth axis fit has failed."
        )

    # max(1, ...) because a scan fine enough that one pixel is under half the
    # rounding unit would otherwise snap to a step of zero.
    steps = max(1, round(depth_per_pixel / _STEP_ROUNDING))
    step = steps * _STEP_ROUNDING

    return step, _BRIDGEABLE_GAP_PIXELS * depth_per_pixel


def resample(trace: CurveTrace, step: float, max_gap: float) -> CurveTrace:
    """Put the curve on a uniform depth step, leaving real gaps as NULL.

    Args:
        trace: the curve, on whatever irregular depths tracing produced.
        step: the depth increment to write, in the curve's depth units. Use
            sampling_grid() to derive it from the sheet's resolution rather
            than choosing one: a step much finer than a pixel makes the file
            claim detail the paper never held.
        max_gap: the longest run of missing depth that may be bridged. A dashed
            curve is not printed between its dashes and those must be joined
            up; an interval where the curve was hidden must not be. Also from
            sampling_grid().

    Returns:
        A new CurveTrace sampled every `step` from the first to the last
        observed depth.

    Raises:
        ValueError: if the step or the gap allowance is not positive.
    """
    if step <= 0:
        raise ValueError(f"Depth step must be positive, got {step}")
    if max_gap <= 0:
        raise ValueError(f"Maximum bridged gap must be positive, got {max_gap}")

    observed = [
        sample for sample in trace.samples if sample.value != CWLS_NULL_VALUE
    ]
    if len(observed) < 2:
        raise ValueError(
            f"Curve {trace.mnemonic} has {len(observed)} observed sample(s); "
            "at least two are needed to resample between them."
        )

    depths = np.array([sample.depth for sample in observed], dtype=float)
    values = np.array([sample.value for sample in observed], dtype=float)
    confidence = np.array([sample.confidence for sample in observed], dtype=float)

    # Start on a round multiple of the step so that two curves from the same
    # sheet land on identical depths and can share one LAS depth index. The
    # count is worked out rather than using a stop value, so the grid can never
    # overshoot the last observed depth on a rounding error and ask to be
    # extrapolated.
    first = np.ceil(depths[0] / step) * step
    grid = first + step * np.arange(int(np.floor((depths[-1] - first) / step)) + 1)

    # Interpolation is linear in value even on a logarithmic track. Over the
    # sub-pixel distances involved the difference from interpolating in log
    # space is under a tenth of a percent, and the only samples where it grows
    # are bridged ones, which already carry reduced confidence.
    interpolated = np.interp(grid, depths, values)

    # The observed samples either side of each grid depth. A grid depth that
    # coincides with an observed one brackets itself, so it is never mistaken
    # for a gap.
    above = np.searchsorted(depths, grid, side="left")
    below = np.searchsorted(depths, grid, side="right") - 1

    # The weaker of the two samples either side governs: interpolating between
    # a crisp reading and a doubtful one does not produce a crisp reading.
    bridged_confidence = np.minimum(confidence[below], confidence[above])

    # np.interp draws a straight line across any gap, however wide, so the gaps
    # have to be found separately and punched back out.
    too_far = (depths[above] - depths[below]) > max_gap

    samples = [
        CurveSample(
            depth=float(depth),
            value=CWLS_NULL_VALUE if skip else float(value),
            confidence=0.0 if skip else float(quality),
        )
        for depth, value, quality, skip in zip(
            grid, interpolated, bridged_confidence, too_far
        )
    ]

    logger.info(
        "resample: OK - %s, %d observed sample(s) -> %d at %g %s, %d left NULL",
        trace.mnemonic,
        len(observed),
        len(samples),
        step,
        trace.unit,
        int(too_far.sum()),
    )
    return _replace_samples(trace, samples)


def _replace_samples(trace: CurveTrace, samples: list[CurveSample]) -> CurveTrace:
    """A copy of the curve carrying different samples, its identity unchanged."""
    return CurveTrace(
        mnemonic=trace.mnemonic,
        unit=trace.unit,
        description=trace.description,
        samples=samples,
    )
