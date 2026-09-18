"""Convert a pixel column into a curve value, on either a linear or log track.

In : a pixel column and the AxisCalibration for the curve being read.
Out: the curve value in the track's units.
Rule: one function, two branches, dispatched on the ScaleType that the printed
      header established. Splitting linear and logarithmic into separate modules
      would push the choice between them out to every caller, and the whole
      point of reading the header was to make that choice once.
"""

from __future__ import annotations

import math

try:
    from app.contracts import AxisCalibration, ScaleType
except ImportError:
    from contracts import AxisCalibration, ScaleType


def pixel_to_value(x_pixel: float, axis: AxisCalibration) -> float:
    """Read the value a curve has where it crosses column `x_pixel`.

    Args:
        x_pixel: a column within the track, not necessarily an integer — a
            traced curve's centre falls between pixels.
        axis: the calibration built from the track's printed header.

    Returns:
        The curve value, in `axis.unit`.

    Raises:
        ValueError: if the track has no width, or if a logarithmic scale reaches
            zero or below. Both mean the calibration is unusable, and returning
            a plausible number from an unusable calibration is the worst
            possible outcome: nothing downstream can tell it is wrong.
    """
    bounds = axis.track_bounds
    span_px = bounds.x_right - bounds.x_left
    if span_px <= 0:
        raise ValueError(
            f"Track {bounds.track_name} has non-positive width: columns "
            f"{bounds.x_left} to {bounds.x_right}. Region detection produced "
            "bounds that cannot be calibrated."
        )

    # Fraction of the way across the track: 0.0 at the left rule, 1.0 at the
    # right. The header's two numbers sit at exactly those two edges, which is
    # the convention every API log sheet is printed to.
    fraction = (x_pixel - bounds.x_left) / span_px

    if axis.scale_type is ScaleType.LINEAR:
        # Equal pixel distances mean equal differences, so interpolate directly.
        # This works unchanged for a reversed scale such as neutron porosity's
        # 0.45 → -0.15, because the subtraction carries the sign.
        return axis.value_min + fraction * (axis.value_max - axis.value_min)

    # Resistivity is plotted so that equal pixel distances represent equal
    # RATIOS, not equal differences: the gap from 0.2 to 2 is the same width on
    # the page as the gap from 2 to 20. Interpolating linearly across a two
    # decade track would read 10 ohm.m as roughly 1, an order of magnitude out.
    if axis.value_min <= 0 or axis.value_max <= 0:
        raise ValueError(
            f"Track {bounds.track_name} is logarithmic but its scale runs "
            f"{axis.value_min} to {axis.value_max}. A log scale cannot reach "
            "zero, so the header was misread."
        )
    log_min = math.log10(axis.value_min)
    log_max = math.log10(axis.value_max)
    return 10 ** (log_min + fraction * (log_max - log_min))


def value_to_pixel(value: float, axis: AxisCalibration) -> float:
    """The inverse: where on the track a given value would be drawn.

    Used to put a traced value back on the page — the QC plot in step 51b
    overlays the digitised curve on the source scan, and that overlay is the
    only end-to-end check that the calibration is right rather than merely
    self-consistent.

    Raises:
        ValueError: on the same unusable calibrations as pixel_to_value, and on
            a non-positive value against a logarithmic scale.
    """
    bounds = axis.track_bounds
    span_px = bounds.x_right - bounds.x_left
    if span_px <= 0:
        raise ValueError(
            f"Track {bounds.track_name} has non-positive width: columns "
            f"{bounds.x_left} to {bounds.x_right}."
        )

    if axis.scale_type is ScaleType.LINEAR:
        value_span = axis.value_max - axis.value_min
        if value_span == 0:
            raise ValueError(
                f"Track {bounds.track_name} has a zero-width value range at "
                f"{axis.value_min}, so no value maps to a column."
            )
        fraction = (value - axis.value_min) / value_span
    else:
        if axis.value_min <= 0 or axis.value_max <= 0:
            raise ValueError(
                f"Track {bounds.track_name} is logarithmic but its scale runs "
                f"{axis.value_min} to {axis.value_max}."
            )
        if value <= 0:
            raise ValueError(
                f"Value {value} cannot be placed on the logarithmic track "
                f"{bounds.track_name}: a log axis has no position for zero or "
                "a negative number."
            )
        log_min = math.log10(axis.value_min)
        log_max = math.log10(axis.value_max)
        fraction = (math.log10(value) - log_min) / (log_max - log_min)

    return bounds.x_left + fraction * span_px
