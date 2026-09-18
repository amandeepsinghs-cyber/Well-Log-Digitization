"""Tests for pixel-to-value conversion on linear and logarithmic tracks.

The axes here are the ones printed on `Well_log_schlum.jpg`: gamma ray 0-150
gAPI across columns 8-285, resistivity 0.2-20 ohm.m across 357-635, and neutron
porosity reversed 0.45 to -0.15 across 635-912.
"""

from __future__ import annotations

import pytest

from app.calibrate.value_axis import pixel_to_value, value_to_pixel
from app.contracts import AxisCalibration, ScaleType, TrackBounds

GR = AxisCalibration(
    mnemonic="GR", unit="GAPI", value_min=0.0, value_max=150.0,
    scale_type=ScaleType.LINEAR,
    track_bounds=TrackBounds(1, "Track 1", 8, 285, ScaleType.LINEAR),
)
ILD = AxisCalibration(
    mnemonic="ILD", unit="OHMM", value_min=0.2, value_max=20.0,
    scale_type=ScaleType.LOGARITHMIC,
    track_bounds=TrackBounds(2, "Track 2", 357, 635, ScaleType.LOGARITHMIC),
)
NPHI = AxisCalibration(
    mnemonic="NPHI", unit="V/V", value_min=0.45, value_max=-0.15,
    scale_type=ScaleType.LINEAR,
    track_bounds=TrackBounds(3, "Track 3", 635, 912, ScaleType.LINEAR),
)


# -- Linear -------------------------------------------------------------------

def test_the_track_edges_read_as_the_printed_scale() -> None:
    assert pixel_to_value(8, GR) == pytest.approx(0.0)
    assert pixel_to_value(285, GR) == pytest.approx(150.0)


def test_the_middle_of_a_linear_track_is_the_midpoint() -> None:
    assert pixel_to_value((8 + 285) / 2, GR) == pytest.approx(75.0)


def test_a_reversed_scale_decreases_left_to_right() -> None:
    """Neutron porosity is printed 45 % on the left and -15 % on the right."""
    assert pixel_to_value(635, NPHI) == pytest.approx(0.45)
    assert pixel_to_value(912, NPHI) == pytest.approx(-0.15)
    assert pixel_to_value((635 + 912) / 2, NPHI) == pytest.approx(0.15)


# -- Logarithmic --------------------------------------------------------------

def test_a_log_track_reads_its_printed_ends() -> None:
    assert pixel_to_value(357, ILD) == pytest.approx(0.2)
    assert pixel_to_value(635, ILD) == pytest.approx(20.0)


def test_the_middle_of_a_log_track_is_the_geometric_mean() -> None:
    """0.2 to 20 is two decades, so halfway across is 2 ohm.m, not 10.1.

    This is the difference the whole logarithmic branch exists for: reading it
    linearly would report 10 ohm.m where the log says 2, a five-fold error in
    the middle of a hydrocarbon indicator.
    """
    assert pixel_to_value((357 + 635) / 2, ILD) == pytest.approx(2.0)


def test_equal_pixel_steps_are_equal_ratios() -> None:
    """The defining property of a log grid, checked directly."""
    quarter = (635 - 357) / 4
    values = [pixel_to_value(357 + quarter * i, ILD) for i in range(5)]
    ratios = [b / a for a, b in zip(values, values[1:])]
    assert ratios == pytest.approx([ratios[0]] * 4)


def test_a_log_scale_reaching_zero_is_refused() -> None:
    """log10(0) is undefined, so the header must have been misread."""
    broken = AxisCalibration(
        mnemonic="ILD", unit="OHMM", value_min=0.0, value_max=20.0,
        scale_type=ScaleType.LOGARITHMIC,
        track_bounds=ILD.track_bounds,
    )
    with pytest.raises(ValueError, match="cannot reach zero"):
        pixel_to_value(400, broken)


# -- Off-track and degenerate inputs ------------------------------------------

def test_a_column_outside_the_track_extrapolates() -> None:
    """Not an error: a curve pegged against the edge is drawn just outside it.

    Refusing would drop real samples; the value is flagged out of range later,
    in qc/, where the whole trace is in view.
    """
    assert pixel_to_value(0, GR) < 0.0


def test_a_zero_width_track_is_refused() -> None:
    broken = AxisCalibration(
        mnemonic="GR", unit="GAPI", value_min=0.0, value_max=150.0,
        scale_type=ScaleType.LINEAR,
        track_bounds=TrackBounds(1, "Track 1", 100, 100, ScaleType.LINEAR),
    )
    with pytest.raises(ValueError, match="non-positive width"):
        pixel_to_value(100, broken)


# -- The inverse --------------------------------------------------------------

@pytest.mark.parametrize("axis,value", [(GR, 75.0), (ILD, 2.0), (NPHI, 0.15)])
def test_value_to_pixel_inverts_pixel_to_value(axis, value) -> None:
    column = value_to_pixel(value, axis)
    assert pixel_to_value(column, axis) == pytest.approx(value)


def test_the_inverse_places_a_log_decade_correctly() -> None:
    """2 ohm.m sits halfway across a 0.2-20 track."""
    assert value_to_pixel(2.0, ILD) == pytest.approx((357 + 635) / 2)


def test_a_non_positive_value_has_no_place_on_a_log_track() -> None:
    with pytest.raises(ValueError, match="no position for zero"):
        value_to_pixel(0.0, ILD)


def test_a_zero_width_value_range_is_refused() -> None:
    flat = AxisCalibration(
        mnemonic="GR", unit="GAPI", value_min=50.0, value_max=50.0,
        scale_type=ScaleType.LINEAR, track_bounds=GR.track_bounds,
    )
    with pytest.raises(ValueError, match="zero-width value range"):
        value_to_pixel(50.0, flat)
