"""Tests for turning transcribed header text into calibrated value axes.

The scale lines used here are the ones printed on `Well_log_schlum.jpg`, read
off the image: "0 gAPI 150", "-80 mV 20", "0.2 ohm.m 20", "45 % -15" and
"1.90 g/cm³ 2.90". Everything is pure string and arithmetic work, so these are
exact assertions, not tolerances.
"""

from __future__ import annotations

import pytest

from app.contracts import ColumnSpan, CurveHeaderText, PageLayout, ScaleType, SheetText
from app.header.parse_header import parse_depth_label, parse_depth_unit, parse_scales

LAYOUT = PageLayout(
    data_top=194,
    data_bottom=772,
    tracks=(
        ColumnSpan("Track 1", 8, 285),
        ColumnSpan("Track 2", 357, 635),
        ColumnSpan("Track 3", 635, 912),
    ),
    depth_column=ColumnSpan("Depth", 285, 357),
    header_rows=(13, 194),
)

SCAN_CURVES = (
    CurveHeaderText("Track 1", "Gamma Ray", "0 gAPI 150"),
    CurveHeaderText("Track 1", "Spontaneous Potential", "-80 mV 20"),
    CurveHeaderText("Track 2", "Resistivity, Shallow", "0.2 ohm.m 20"),
    CurveHeaderText("Track 2", "Resistivity, Medium", "0.2 ohm.m 20"),
    CurveHeaderText("Track 2", "Resistivity, Deep", "0.2 ohm.m 20"),
    CurveHeaderText("Track 3", "Neutron Porosity", "45 % -15"),
    CurveHeaderText("Track 3", "Bulk Density", "1.90 g/cm³ 2.90"),
)


def _sheet(curves=SCAN_CURVES, depth_header="Depth, ft", labels=("7,000",)) -> SheetText:
    return SheetText(curves=curves, depth_header=depth_header, depth_labels=labels)


def _axes(curves=SCAN_CURVES):
    return {axis.mnemonic: axis for axis in parse_scales(_sheet(curves), LAYOUT)}


# -- The whole sheet ----------------------------------------------------------

def test_every_printed_curve_is_resolved() -> None:
    """Gate 2 requires seven curves to be reported from the image alone."""
    axes = parse_scales(_sheet(), LAYOUT)
    assert [axis.mnemonic for axis in axes] == [
        "GR", "SP", "RXO", "ILM", "ILD", "NPHI", "RHOB"
    ]


def test_titles_are_resolved_to_mnemonics() -> None:
    """The sheet prints "Resistivity, Deep"; LAS needs "ILD"."""
    assert _axes()["ILD"].mnemonic == "ILD"


# -- Scale type ---------------------------------------------------------------

def test_track_two_is_logarithmic() -> None:
    """The Gate 2 assertion. Derived from "0.2 ohm.m 20" spanning two decades."""
    for mnemonic in ("ILD", "ILM", "RXO"):
        assert _axes()[mnemonic].scale_type is ScaleType.LOGARITHMIC
        assert _axes()[mnemonic].track_bounds.track_name == "Track 2"


def test_the_other_tracks_are_linear() -> None:
    for mnemonic in ("GR", "SP", "NPHI", "RHOB"):
        assert _axes()[mnemonic].scale_type is ScaleType.LINEAR


def test_a_narrow_positive_scale_is_not_logarithmic() -> None:
    """Bulk density runs 1.90 to 2.90 — positive, but nowhere near a decade."""
    assert _axes()["RHOB"].scale_type is ScaleType.LINEAR


def test_a_scale_that_contradicts_the_mnemonic_is_refused() -> None:
    """A resistivity printed 0 to 20 cannot be plotted on a log grid.

    Either the scale was misread or the header is not resistivity. Both are
    reasons to stop rather than plot two decades of values linearly.
    """
    with pytest.raises(ValueError, match="looks LINEAR"):
        parse_scales(
            _sheet((CurveHeaderText("Track 2", "Resistivity, Deep", "0 ohm.m 20"),)),
            LAYOUT,
        )


def test_mixed_scale_types_in_one_track_are_refused() -> None:
    """A track is one printed grid, so it cannot be both."""
    curves = (
        CurveHeaderText("Track 1", "Gamma Ray", "0 gAPI 150"),
        CurveHeaderText("Track 1", "Resistivity, Deep", "0.2 ohm.m 20"),
    )
    with pytest.raises(ValueError, match="carries curves on both"):
        parse_scales(_sheet(curves), LAYOUT)


# -- Values and units ---------------------------------------------------------

def test_a_plain_scale_reads_left_to_right() -> None:
    axis = _axes()["GR"]
    assert (axis.value_min, axis.value_max, axis.unit) == (0.0, 150.0, "GAPI")


def test_a_negative_scale_keeps_its_sign() -> None:
    axis = _axes()["SP"]
    assert (axis.value_min, axis.value_max) == (-80.0, 20.0)


def test_a_reversed_scale_is_not_sorted() -> None:
    """Neutron porosity prints 45 on the left and -15 on the right.

    Sorting the two would mirror the curve across the track, and the
    neutron/density crossover that indicates hydrocarbon would disappear.
    """
    axis = _axes()["NPHI"]
    assert axis.value_min > axis.value_max


def test_percent_is_converted_to_a_fraction() -> None:
    """LAS records neutron porosity as V/V. 45 % stored as 45 is 100x wrong."""
    axis = _axes()["NPHI"]
    assert (axis.value_min, axis.value_max, axis.unit) == (0.45, -0.15, "V/V")


def test_a_superscript_unit_is_recognised() -> None:
    axis = _axes()["RHOB"]
    assert (axis.value_min, axis.value_max, axis.unit) == (1.90, 2.90, "G/C3")


def test_a_typographic_minus_is_read_as_a_minus() -> None:
    """Typeset logs print U+2212, not a hyphen."""
    curves = (CurveHeaderText("Track 1", "Spontaneous Potential", "\u221280 mV 20"),)
    assert _axes(curves)["SP"].value_min == -80.0


def test_a_thousands_separator_is_not_a_decimal_point() -> None:
    """"2,000 ohm.m" read as 2.0 would understate the scale a thousandfold."""
    curves = (CurveHeaderText("Track 2", "Resistivity, Deep", "0.2 ohm.m 2,000"),)
    assert _axes(curves)["ILD"].value_max == 2000.0


def test_a_european_decimal_comma_is_a_decimal_point() -> None:
    curves = (CurveHeaderText("Track 3", "Bulk Density", "1,90 g/cm³ 2,90"),)
    axis = _axes(curves)["RHOB"]
    assert (axis.value_min, axis.value_max) == (1.90, 2.90)


def test_an_unknown_unit_is_refused() -> None:
    curves = (CurveHeaderText("Track 1", "Gamma Ray", "0 blorks 150"),)
    with pytest.raises(ValueError, match="not a unit this pipeline knows"):
        parse_scales(_sheet(curves), LAYOUT)


def test_a_unit_that_contradicts_the_title_is_refused() -> None:
    """Two header lines transcribed from different curves would read like this."""
    curves = (CurveHeaderText("Track 1", "Gamma Ray", "0 mV 150"),)
    with pytest.raises(ValueError, match="do not describe the same curve"):
        parse_scales(_sheet(curves), LAYOUT)


def test_a_malformed_scale_line_is_refused() -> None:
    curves = (CurveHeaderText("Track 1", "Gamma Ray", "gAPI"),)
    with pytest.raises(ValueError, match="is not of the form"):
        parse_scales(_sheet(curves), LAYOUT)


def test_an_unknown_title_is_refused() -> None:
    curves = (CurveHeaderText("Track 1", "Mud Cake Thickness", "0 in 2"),)
    with pytest.raises(ValueError, match="not a curve in the SPWLA mnemonic table"):
        parse_scales(_sheet(curves), LAYOUT)


# -- Track geometry carried through -------------------------------------------

def test_the_axis_carries_its_track_pixel_bounds() -> None:
    """Without these the value axis cannot be applied to any pixel."""
    bounds = _axes()["ILD"].track_bounds
    assert (bounds.track_index, bounds.x_left, bounds.x_right) == (2, 357, 635)


def test_track_index_skips_the_depth_column() -> None:
    """Track 2 is the resistivity column, not the depth column beside it."""
    assert _axes()["NPHI"].track_bounds.track_index == 3


# -- Depth header and labels --------------------------------------------------

@pytest.mark.parametrize(
    "header,expected",
    [
        ("Depth, ft", "FT"),
        ("DEPTH (FEET)", "FT"),
        ("Depth, m", "M"),
        ("Depth metres", "M"),
    ],
)
def test_depth_units_are_read(header: str, expected: str) -> None:
    assert parse_depth_unit(header) == expected


def test_a_depth_header_without_a_unit_is_refused() -> None:
    """Assuming feet on a metric log misplaces every sample by 3.28x."""
    with pytest.raises(ValueError, match="No depth unit found"):
        parse_depth_unit("Depth")


@pytest.mark.parametrize(
    "label,expected",
    [("7,000", 7000.0), ("7300", 7300.0), ("1,234.5", 1234.5), ("\u2212100", -100.0)],
)
def test_depth_labels_are_read(label: str, expected: float) -> None:
    assert parse_depth_label(label) == expected


def test_an_unreadable_depth_label_is_refused() -> None:
    with pytest.raises(ValueError, match="is not a number"):
        parse_depth_label("7,O00")
