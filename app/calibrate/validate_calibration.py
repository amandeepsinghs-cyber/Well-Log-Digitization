"""Decide whether a scan's calibration is trustworthy enough to digitise.

In : the fitted DepthCalibration and the GridLines it was fitted against.
Out: QcFindings — INFO, WARNING or ERROR — and a helper to pull out the
     blocking ones.
Rule: this module refuses distorted scans, it does not correct them. A skewed or
      photographed-at-an-angle sheet needs a perspective rectification we have
      no test case for; guessing at one would turn a visible failure into an
      invisible one. Saying "this scan cannot be digitised, and here is why" is
      the honest answer until a genuinely distorted scan exists to build against.
"""

from __future__ import annotations

import logging

import numpy as np

try:
    from app.contracts import (
        DepthCalibration,
        FindingSeverity,
        GridLines,
        QcFinding,
    )
except ImportError:
    from contracts import (
        DepthCalibration,
        FindingSeverity,
        GridLines,
        QcFinding,
    )

logger = logging.getLogger(__name__)

# How far the labelled ticks may sit off the fitted line, measured in PIXELS
# rather than depth units so the limits mean the same thing on a 300 ft log and
# a 3,000 ft one. A printed rule is 1-3 px thick, so a fit good to about a pixel
# is as good as the sheet it was measured from; 3 px means the ticks genuinely
# do not lie on a line.
_FIT_WARNING_PX = 1.0
_FIT_ERROR_PX = 3.0

# How much the reading grid's pitch may vary between one pair of lines and the
# next. On the reference sheet the pitch alternates 38 and 39 px purely from
# rounding to whole pixels, which is 2.6%.
_PITCH_WARNING_DEVIATION = 0.05
_PITCH_ERROR_DEVIATION = 0.10

# A sheet photographed at an angle has a pitch that changes steadily down the
# page rather than jittering about a constant. Comparing the top half of the
# grid with the bottom half separates the two: random rounding cancels when
# averaged, a perspective gradient does not.
_PERSPECTIVE_ERROR_DEVIATION = 0.03


def validate_calibration(
    depth: DepthCalibration, grid: GridLines
) -> tuple[QcFinding, ...]:
    """Judge the depth calibration and the geometry it rests on.

    Args:
        depth: the fitted depth scale.
        grid: the rule lines the fit was measured against.

    Returns:
        Findings in the order they were checked. An empty ERROR set means the
        scan may be digitised.
    """
    interval = (depth.depth_min, depth.depth_max)
    findings: list[QcFinding] = [
        _report_resolution(depth, interval),
        *_check_fit(depth, interval),
        *_check_pitch(grid, interval),
        *_check_perspective(grid, interval),
    ]

    logger.info(
        "validate_calibration: %d finding(s), %d blocking",
        len(findings),
        len(blocking_findings(tuple(findings))),
    )
    return tuple(findings)


def blocking_findings(findings: tuple[QcFinding, ...]) -> tuple[QcFinding, ...]:
    """The findings that mean digitisation must not proceed."""
    return tuple(f for f in findings if f.severity is FindingSeverity.ERROR)


def _report_resolution(
    depth: DepthCalibration, interval: tuple[float, float]
) -> QcFinding:
    """State the finest depth step the scan can honestly support.

    One pixel is the smallest distance the sheet distinguishes, so sampling the
    LAS finer than that manufactures detail the paper never had. Recorded as a
    finding so the number appears in the audit trail rather than only in a
    resampling constant.
    """
    return QcFinding(
        severity=FindingSeverity.INFO,
        mnemonic="DEPTH",
        depth_interval=interval,
        message=(
            f"One pixel is {depth.depth_per_pixel:.3f} {depth.depth_units}, so "
            f"the scan supports a sample step no finer than that over "
            f"{interval[0]:.1f}-{interval[1]:.1f} {depth.depth_units}."
        ),
    )


def _check_fit(
    depth: DepthCalibration, interval: tuple[float, float]
) -> list[QcFinding]:
    """Compare the depth fit's residual with the thickness of a printed line."""
    rmse_px = depth.fit_rmse / depth.depth_per_pixel
    if rmse_px <= _FIT_WARNING_PX:
        return []

    severity = (
        FindingSeverity.ERROR
        if rmse_px > _FIT_ERROR_PX
        else FindingSeverity.WARNING
    )
    return [
        QcFinding(
            severity=severity,
            mnemonic="DEPTH",
            depth_interval=interval,
            message=(
                f"The depth labels do not lie on a straight line: the fit is "
                f"off by {depth.fit_rmse:.2f} {depth.depth_units} "
                f"({rmse_px:.1f} px) on average. A label was probably misread, "
                "and every depth in the output depends on them."
            ),
        )
    ]


def _check_pitch(
    grid: GridLines, interval: tuple[float, float]
) -> list[QcFinding]:
    """Check the reading grid is printed at a constant interval."""
    normalised = _normalised_gaps(grid)
    if normalised is None:
        return []

    deviation = float(np.max(np.abs(normalised - grid.grid_spacing_px))) / (
        grid.grid_spacing_px
    )
    if deviation <= _PITCH_WARNING_DEVIATION:
        return []

    severity = (
        FindingSeverity.ERROR
        if deviation > _PITCH_ERROR_DEVIATION
        else FindingSeverity.WARNING
    )
    return [
        QcFinding(
            severity=severity,
            mnemonic="DEPTH",
            depth_interval=interval,
            message=(
                f"The depth grid is not evenly spaced: one interval differs "
                f"from the {grid.grid_spacing_px:.1f} px pitch by "
                f"{deviation:.0%}. An even grid is what makes a single depth "
                "scale valid for the whole page."
            ),
        )
    ]


def _check_perspective(
    grid: GridLines, interval: tuple[float, float]
) -> list[QcFinding]:
    """Look for a grid pitch that drifts steadily from the top of the page to the bottom.

    That is the signature of a sheet photographed at an angle rather than
    scanned flat. It cannot be fixed by a linear depth fit — the top of the log
    would read correctly while the bottom drifted — so it is refused.
    """
    normalised = _normalised_gaps(grid)
    if normalised is None or normalised.size < 4:
        return []

    half = normalised.size // 2
    top = float(np.mean(normalised[:half]))
    bottom = float(np.mean(normalised[-half:]))
    drift = abs(bottom - top) / grid.grid_spacing_px
    if drift <= _PERSPECTIVE_ERROR_DEVIATION:
        return []

    return [
        QcFinding(
            severity=FindingSeverity.ERROR,
            mnemonic="DEPTH",
            depth_interval=interval,
            message=(
                f"The depth grid pitch drifts {drift:.0%} from the top of the "
                f"page ({top:.1f} px) to the bottom ({bottom:.1f} px), which "
                "means the sheet was photographed at an angle rather than "
                "scanned flat. This scan cannot be digitised on a straight "
                "depth scale; rescan it square to the page."
            ),
        )
    ]


def _normalised_gaps(grid: GridLines) -> np.ndarray | None:
    """Gaps between grid lines, each divided by how many pitches it spans.

    A grid line hidden under a colour fill leaves a double-width gap, which is
    expected and not a defect. Dividing by the nearest whole number of pitches
    puts such a gap back on the same footing as the rest, so the regularity
    checks measure printing accuracy rather than how much of the sheet is
    coloured in.

    Returns None when there is not enough grid to say anything.
    """
    if len(grid.depth_grid_rows) < 3 or grid.grid_spacing_px <= 0:
        return None

    gaps = np.diff(np.asarray(grid.depth_grid_rows, dtype=float))
    multiples = np.maximum(np.round(gaps / grid.grid_spacing_px), 1.0)
    return gaps / multiples
