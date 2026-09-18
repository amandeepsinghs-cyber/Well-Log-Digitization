"""Tests for the LasDocument -> CWLS LAS 2.0 writer.

The writer has one job that is easy to get subtly wrong: putting curves of
different extent onto a single depth index. The failure mode is not a crash —
it is a file that opens cleanly, validates, and is full of nothing. Most of the
tests below exist to pin that down.
"""

from __future__ import annotations

import pytest

from app.contracts import (
    CWLS_NULL_VALUE,
    CurveSample,
    CurveTrace,
    FindingSeverity,
    LasDocument,
)
from app.las.parse_las import parse_las
from app.las.validate_las import validate_las
from app.las.write_las import write_las


def _curve(
    mnemonic: str,
    depths: list[float],
    values: list[float],
    *,
    unit: str = "GAPI",
    description: str = "Gamma Ray",
) -> CurveTrace:
    """A curve at the given depths, full confidence except where NULL."""
    return CurveTrace(
        mnemonic=mnemonic,
        unit=unit,
        description=description,
        samples=[
            CurveSample(
                depth=d,
                value=v,
                confidence=0.0 if v == CWLS_NULL_VALUE else 0.9,
            )
            for d, v in zip(depths, values, strict=True)
        ],
    )


def _document(curves: list[CurveTrace], **overrides) -> LasDocument:
    """A well-formed document spanning 7000-7002 ft at a half-foot step."""
    fields = {
        "well_name": "TEST_WELL",
        "depth_min": 7000.0,
        "depth_max": 7002.0,
        "depth_step": 0.5,
        "depth_units": "FT",
        "curves": curves,
        "provenance_comments": ["Digitised from a raster scan."],
    }
    fields.update(overrides)
    return LasDocument(**fields)


_FULL_DEPTHS = [7000.0, 7000.5, 7001.0, 7001.5, 7002.0]


# -- The contract with validate_las ------------------------------------------

def test_output_has_no_error_findings() -> None:
    """The gate the whole project hangs on: what we write, we accept."""
    data = write_las(_document([_curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50])]))

    errors = [f for f in validate_las(data) if f.severity is FindingSeverity.ERROR]
    assert errors == []


def test_output_declares_las_version_2_0() -> None:
    data = write_las(_document([_curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50])]))

    assert "VERS.   2.0" in data.decode("utf-8")


# -- Round trip ---------------------------------------------------------------

def test_curve_values_survive_a_round_trip() -> None:
    written = write_las(_document([_curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50])]))

    doc = parse_las(written)
    assert [s.value for s in doc.curves[0].samples] == [10, 20, 30, 40, 50]
    assert [s.depth for s in doc.curves[0].samples] == _FULL_DEPTHS


def test_mnemonic_unit_and_description_survive_a_round_trip() -> None:
    written = write_las(
        _document(
            [
                _curve(
                    "RHOB",
                    _FULL_DEPTHS,
                    [2.1, 2.2, 2.3, 2.4, 2.5],
                    unit="G/C3",
                    description="Bulk Density",
                )
            ]
        )
    )

    curve = parse_las(written).curves[0]
    assert (curve.mnemonic, curve.unit, curve.description) == (
        "RHOB",
        "G/C3",
        "Bulk Density",
    )


def test_well_name_and_depth_frame_survive_a_round_trip() -> None:
    written = write_las(_document([_curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50])]))

    doc = parse_las(written)
    assert doc.well_name == "TEST_WELL"
    assert (doc.depth_min, doc.depth_max, doc.depth_step) == (7000.0, 7002.0, 0.5)
    assert doc.depth_units == "FT"


# -- Nulls are gaps, not numbers ----------------------------------------------

def test_a_null_sample_is_written_as_the_cwls_sentinel() -> None:
    written = write_las(
        _document([_curve("GR", _FULL_DEPTHS, [10, CWLS_NULL_VALUE, 30, 40, 50])])
    )

    assert "-999.2500" in written.decode("utf-8")


def test_a_null_is_not_interpolated_across_on_the_way_back() -> None:
    """A gap must still be a gap after a write-then-read.

    An interpolated value here would be indistinguishable from a measurement,
    which is the one thing a digitised log must never produce.
    """
    written = write_las(
        _document([_curve("GR", _FULL_DEPTHS, [10, CWLS_NULL_VALUE, 30, 40, 50])])
    )

    values = [s.value for s in parse_las(written).curves[0].samples]
    assert values[1] == CWLS_NULL_VALUE
    assert values == [10, CWLS_NULL_VALUE, 30, 40, 50]


def test_a_null_sample_carries_no_confidence_back() -> None:
    written = write_las(
        _document([_curve("GR", _FULL_DEPTHS, [10, CWLS_NULL_VALUE, 30, 40, 50])])
    )

    assert parse_las(written).curves[0].samples[1].confidence == 0.0


# -- Curves of differing extent onto one index --------------------------------

def test_a_curve_starting_late_is_null_above_its_first_sample() -> None:
    """The real case: GR is traced from the top of the sheet, RHOB is not."""
    written = write_las(
        _document(
            [
                _curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50]),
                _curve(
                    "RHOB",
                    [7001.0, 7001.5, 7002.0],
                    [2.3, 2.4, 2.5],
                    unit="G/C3",
                    description="Bulk Density",
                ),
            ]
        )
    )

    doc = parse_las(written)
    rhob = next(c for c in doc.curves if c.mnemonic == "RHOB")
    assert [s.value for s in rhob.samples] == [
        CWLS_NULL_VALUE,
        CWLS_NULL_VALUE,
        2.3,
        2.4,
        2.5,
    ]


def test_every_curve_shares_one_depth_index() -> None:
    written = write_las(
        _document(
            [
                _curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50]),
                _curve(
                    "RHOB",
                    [7001.0, 7001.5, 7002.0],
                    [2.3, 2.4, 2.5],
                    unit="G/C3",
                    description="Bulk Density",
                ),
            ]
        )
    )

    doc = parse_las(written)
    depths = {tuple(s.depth for s in curve.samples) for curve in doc.curves}
    assert depths == {tuple(_FULL_DEPTHS)}


def test_a_curve_ending_early_is_null_below_its_last_sample() -> None:
    written = write_las(
        _document(
            [
                _curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50]),
                _curve("SP", [7000.0, 7000.5], [-10, -20], unit="MV", description="SP"),
            ]
        )
    )

    sp = next(c for c in parse_las(written).curves if c.mnemonic == "SP")
    assert [s.value for s in sp.samples][2:] == [CWLS_NULL_VALUE] * 3


# -- The silent-empty-file guard ----------------------------------------------

def test_samples_that_miss_the_index_are_refused() -> None:
    """Off-grid samples would write a file that is valid and entirely empty.

    This is the writer's most important guard: an unresampled curve produces no
    error anywhere else in the pipeline, and the resulting LAS opens perfectly.
    """
    off_grid = _curve("GR", [7000.1, 7000.6, 7001.1], [10, 20, 30])

    with pytest.raises(ValueError, match="fall on the file's depth index"):
        write_las(_document([off_grid]))


def test_the_refusal_names_the_curve_and_counts_the_losses() -> None:
    off_grid = _curve("GR", [7000.1, 7000.6, 7001.1], [10, 20, 30])

    with pytest.raises(ValueError) as caught:
        write_las(_document([off_grid]))

    message = str(caught.value)
    assert "GR" in message
    assert "3 samples" in message
    assert "0 of them" in message


def test_a_partially_off_grid_curve_is_refused() -> None:
    """Two of three land, one does not — still a loss, still refused."""
    mixed = _curve("GR", [7000.0, 7000.5, 7000.7], [10, 20, 30])

    with pytest.raises(ValueError, match="2 of them"):
        write_las(_document([mixed]))


def test_a_repeated_depth_is_refused() -> None:
    repeated = _curve("GR", [7000.0, 7000.0, 7000.5], [10, 20, 30])

    with pytest.raises(ValueError, match="cannot repeat a depth"):
        write_las(_document([repeated]))


# -- Depth frame guards -------------------------------------------------------

def test_a_non_positive_step_is_refused() -> None:
    with pytest.raises(ValueError, match="Depth step must be positive"):
        write_las(_document([_curve("GR", [7000.0], [10])], depth_step=0.0))


def test_a_backwards_depth_range_is_refused() -> None:
    with pytest.raises(ValueError, match="runs backwards"):
        write_las(
            _document(
                [_curve("GR", [7000.0], [10])], depth_min=7002.0, depth_max=7000.0
            )
        )


def test_the_header_depth_frame_matches_the_data_written() -> None:
    """STRT and STOP describe the ~ASCII section, not the caller's intent."""
    written = write_las(
        _document([_curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50])])
    ).decode("utf-8")

    assert "STRT.FT" in written and "7000.0000" in written
    assert "STOP.FT" in written and "7002.0000" in written
    assert "STEP.FT" in written


def test_the_depth_unit_is_stated_in_both_well_and_curve_sections() -> None:
    """Feet read as metres is the one header defect nothing downstream catches."""
    written = write_las(
        _document([_curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50])])
    ).decode("utf-8")

    assert "STRT.FT" in written
    assert "DEPT.FT" in written


def test_a_single_sample_log_is_refused() -> None:
    """One row establishes no depth step, so our reader would reject it.

    The writer refuses it first, rather than emitting a file that this
    pipeline's own parse_las and validate_las both throw out.
    """
    with pytest.raises(ValueError, match="at least 2 to establish a step"):
        write_las(
            _document(
                [_curve("GR", [7000.0], [10])], depth_min=7000.0, depth_max=7000.0
            )
        )


# -- Provenance and metadata --------------------------------------------------

def test_provenance_is_written_to_the_other_section() -> None:
    """Without this, a traced curve is indistinguishable from a measurement."""
    written = write_las(
        _document(
            [_curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50])],
            provenance_comments=["Traced from gs://bucket/scan.jpg", "Not a recording"],
        )
    )

    assert parse_las(written).provenance_comments == [
        "Traced from gs://bucket/scan.jpg",
        "Not a recording",
    ]


def test_non_ascii_provenance_survives() -> None:
    """The source sheet prints g/cm3 with a superscript; LAS 2.0 says ASCII."""
    written = write_las(
        _document(
            [_curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50])],
            provenance_comments=["Bulk density read in g/cm\u00b3"],
        )
    )

    assert "g/cm\u00b3" in parse_las(written).provenance_comments[0]


def test_metadata_is_carried_into_the_well_section() -> None:
    written = write_las(
        _document(
            [_curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50])],
            metadata={"DATE": "2026-09-18", "SRVC": "Digitised"},
        )
    )

    doc = parse_las(written)
    assert doc.metadata["DATE"] == "2026-09-18"
    assert doc.metadata["SRVC"] == "Digitised"


def test_metadata_cannot_contradict_the_typed_depth_frame() -> None:
    """A stale STRT carried in metadata must not overwrite the real one."""
    written = write_las(
        _document(
            [_curve("GR", _FULL_DEPTHS, [10, 20, 30, 40, 50])],
            metadata={"STRT": "1234.0", "WELL": "WRONG_WELL"},
        )
    )

    doc = parse_las(written)
    assert doc.depth_min == 7000.0
    assert doc.well_name == "TEST_WELL"
