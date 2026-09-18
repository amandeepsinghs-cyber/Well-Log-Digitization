"""Fit the depth scale: pixel row → measured depth.

In : the depth ticks, the depth value read off each one, the depth unit, and the
     page layout.
Out: a DepthCalibration — a straight line through the ticks, plus the residual
     error of that fit.
Rule: a fit over observed positions, deliberately not an interpolation between
      two declared end points. A log sheet's depth grid is printed evenly, so
      every labelled tick is evidence about the same line; using only the first
      and last would throw away the middle ones and, worse, would hide the fact
      that they disagree. The residual error reported here is what
      calibrate/validate_calibration.py judges the scan on.
"""

from __future__ import annotations

import logging

import numpy as np

try:
    from app.contracts import DepthCalibration, DepthTick, PageLayout
except ImportError:
    from contracts import DepthCalibration, DepthTick, PageLayout

logger = logging.getLogger(__name__)


def fit_depth_axis(
    ticks: tuple[DepthTick, ...],
    depths: tuple[float, ...],
    depth_units: str,
    layout: PageLayout,
) -> DepthCalibration:
    """Fit depth against pixel row through the labelled ticks.

    Args:
        ticks: the labelled grid lines, in depth order.
        depths: the value read from each tick's label, in the same order.
        depth_units: "FT" or "M", from the depth column's header.
        layout: used to report the depth at the top and bottom of the data area.

    Returns:
        DepthCalibration, where depth = y_origin_depth + depth_per_pixel * row.

    Raises:
        ValueError: if there are fewer than two distinct ticks, if the counts do
            not match, or if depth does not increase down the page. The last is
            worth its own check: a fit that comes out with a negative slope means
            the ticks were paired with the wrong labels, and the log would be
            written upside down.
    """
    if len(ticks) != len(depths):
        raise ValueError(
            f"{len(ticks)} depth tick(s) but {len(depths)} depth value(s). Each "
            "tick supplies the pixel row and its label supplies the depth, so "
            "they must correspond one to one."
        )

    rows = np.asarray([tick.grid_row for tick in ticks], dtype=float)
    values = np.asarray(depths, dtype=float)
    if np.unique(rows).size < 2:
        raise ValueError(
            f"Depth ticks are at rows {rows.tolist()}, which do not give two "
            "distinct positions. A depth scale cannot be fitted to one point."
        )

    # Least squares through all the ticks. Degree 1 because a scanned log sheet
    # is printed on a linear depth scale; a curved fit would be modelling paper
    # distortion, which step 44 refuses outright rather than corrects.
    slope, intercept = np.polyfit(rows, values, 1)
    if slope <= 0:
        raise ValueError(
            f"The fitted depth scale runs {slope:.4f} {depth_units} per pixel, "
            "so depth would decrease down the page. Depth increases downward on "
            "every wireline log, so the ticks and labels are mismatched."
        )

    # Residuals in depth units, which is what a petrophysicist can judge: an
    # RMSE of 0.5 ft on a 300 ft log is a good fit, 20 ft is a misread tick.
    predicted = intercept + slope * rows
    fit_rmse = float(np.sqrt(np.mean((values - predicted) ** 2)))

    # The display's extent is the data area, not the outermost label: curves are
    # plotted right up to the frame, past the last labelled line.
    depth_min = float(intercept + slope * layout.data_top)
    depth_max = float(intercept + slope * layout.data_bottom)

    logger.info(
        "fit_depth_axis: OK - %.5f %s/px over %d tick(s), display %.1f-%.1f %s, "
        "RMSE %.3f %s",
        slope,
        depth_units,
        len(ticks),
        depth_min,
        depth_max,
        depth_units,
        fit_rmse,
        depth_units,
    )
    return DepthCalibration(
        depth_min=depth_min,
        depth_max=depth_max,
        depth_units=depth_units,
        depth_per_pixel=float(slope),
        y_origin_depth=float(intercept),
        fit_rmse=fit_rmse,
    )


def depth_at_row(calibration: DepthCalibration, row: float) -> float:
    """Convert a pixel row to a measured depth using a fitted calibration.

    Every curve sample in the pipeline gets its depth from here, so the fit is
    applied in exactly one place and cannot drift between callers.
    """
    return calibration.y_origin_depth + calibration.depth_per_pixel * row
