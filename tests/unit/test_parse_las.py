"""Tests for the lasio -> LasDocument adapter.

The adapter's whole value is that it does not lose anything: not a null, not a
mnemonic spelling, not the provenance stamp. Each test below pins one thing
that a careless "tidy-up" of the reader would quietly discard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts import CWLS_NULL_VALUE
from app.las.parse_las import parse_las

FIXTURE = Path(__file__).parents[1] / "fixtures" / "las" / "SCAFFOLD-TEST-0001.las"


def _minimal_las(
    *,
    step: str = "0.5",
    depth_unit: str = "FT",
    rows: str = "7000.0   10.0\n7000.5   20.0\n7001.0   30.0",
    curve_lines: str | None = None,
) -> str:
    """Smallest LAS 2.0 file that lasio accepts, with the bits under test open.

    depth_unit is written into BOTH ~WELL and ~CURVE. A file that disagrees with
    itself about feet versus metres is a different defect, and lasio warns about
    it separately; conflating the two here would hide whichever one broke.
    """
    if curve_lines is None:
        curve_lines = f"DEPT.{depth_unit}  : Depth\nGR  .GAPI : Gamma Ray"
    return (
        "~VERSION\n"
        "VERS. 2.0 : CWLS LOG ASCII STANDARD\n"
        "WRAP. NO  : ONE LINE PER DEPTH STEP\n"
        "~WELL\n"
        f"STRT.{depth_unit} 7000.0  :\n"
        f"STOP.{depth_unit} 7001.0  :\n"
        f"STEP.{depth_unit} {step}  :\n"
        "NULL.     -999.25 :\n"
        "WELL.     TEST-1  :\n"
        "~CURVE\n"
        f"{curve_lines}\n"
        "~ASCII\n"
        f"{rows}\n"
    )



# -- Round trip on the real fixture ------------------------------------------

def test_reads_the_scaffold_fixture_end_to_end() -> None:
    doc = parse_las(FIXTURE.read_bytes())

    assert doc.well_name == "SCAFFOLD-TEST-0001"
    assert (doc.depth_min, doc.depth_max) == (7000.0, 7009.5)
    assert doc.depth_step == 0.5
    assert doc.depth_units == "FT"
    # Six curves in the file, one of which is the depth index.
    assert [c.mnemonic for c in doc.curves] == ["GR", "SP", "ILD", "NPHI", "RHOB"]


def test_null_becomes_a_gap_and_is_never_interpolated() -> None:
    """The fixture carries one deliberate NULL in SP at 7004.0 ft."""
    doc = parse_las(FIXTURE.read_bytes())
    sp = next(c for c in doc.curves if c.mnemonic == "SP")

    gaps = [s for s in sp.samples if s.value == CWLS_NULL_VALUE]
    assert len(gaps) == 1
    assert gaps[0].depth == 7004.0
    # A null carries no reading, so it must carry no confidence either.
    assert gaps[0].confidence == 0.0
    # 19 of 20 samples valid.
    assert sp.coverage_fraction == pytest.approx(0.95)


def test_nan_never_reaches_the_dataclass() -> None:
    """lasio hands back NaN; a NaN leaking into Vega renders as a broken chart."""
    doc = parse_las(FIXTURE.read_bytes())
    for curve in doc.curves:
        for sample in curve.samples:
            assert sample.value == sample.value, f"NaN in {curve.mnemonic}"


def test_provenance_stamp_survives_the_read() -> None:
    """~OTHER is how a synthetic or digitised LAS declares it is not measured."""
    doc = parse_las(FIXTURE.read_bytes())
    joined = " ".join(doc.provenance_comments)
    assert "SYNTHETIC TEST SCAFFOLDING" in joined


def test_header_metadata_is_carried_through_verbatim() -> None:
    doc = parse_las(FIXTURE.read_bytes())
    assert doc.metadata["WELL"] == "SCAFFOLD-TEST-0001"
    assert "SRVC" in doc.metadata


def test_all_curves_share_the_depth_index() -> None:
    """Vega layers a shared y-axis; mismatched depths would silently mis-stack."""
    doc = parse_las(FIXTURE.read_bytes())
    depths = [tuple(s.depth for s in c.samples) for c in doc.curves]
    assert len(set(depths)) == 1


# -- Mnemonics are the reader's business only to copy ------------------------

def test_mnemonics_are_not_normalised_by_the_reader() -> None:
    """Interpretation belongs to track_layout; the reader must not second-guess.

    A file calling deep resistivity "RT" keeps "RT" here. Rewriting it to "ILD"
    at read time would make the LAS we later write disagree with the source.
    """
    las = _minimal_las(curve_lines="DEPT.FT : Depth\nRT  .OHMM : Resistivity")
    doc = parse_las(las)
    assert doc.curves[0].mnemonic == "RT"


# -- Depth step -------------------------------------------------------------

def test_declared_step_is_used_when_present() -> None:
    assert parse_las(_minimal_las(step="0.5")).depth_step == 0.5


def test_step_is_measured_when_the_header_declares_zero() -> None:
    """STEP 0 is the LAS convention for irregular sampling, not a real step."""
    las = _minimal_las(step="0.0", rows="7000.0 10.0\n7000.5 20.0\n7001.5 30.0")
    # Spacings are 0.5 and 1.0; the median is the honest single number.
    assert parse_las(las).depth_step == pytest.approx(0.75)


# -- Depth units ------------------------------------------------------------

@pytest.mark.parametrize(("written", "expected"), [("FT", "FT"), ("F", "FT"), ("M", "M")])
def test_depth_units_normalise_to_the_contract(written: str, expected: str) -> None:
    assert parse_las(_minimal_las(depth_unit=written)).depth_units == expected


def test_unrecognised_depth_unit_raises_rather_than_guessing() -> None:
    """Assuming feet on a metric log mis-scales every depth by 3.28x."""
    with pytest.raises(ValueError, match="unrecognised unit"):
        parse_las(_minimal_las(depth_unit="XX"))


# -- Degenerate files fail loudly -------------------------------------------

def test_single_sample_raises() -> None:
    """One row gives no spacing, so no depth step can be established."""
    with pytest.raises(ValueError, match="at least 2"):
        parse_las(_minimal_las(rows="7000.0 10.0"))


def test_depth_only_file_raises() -> None:
    with pytest.raises(ValueError, match="no measured curves"):
        parse_las(_minimal_las(curve_lines="DEPT.FT : Depth", rows="7000.0\n7000.5"))


# -- Encoding ---------------------------------------------------------------

def test_latin1_header_text_does_not_break_the_read() -> None:
    """Older vendor exports put accented company names in the ~WELL section."""
    las = _minimal_las().replace("WELL.     TEST-1  :", "COMP.     PETROL\xe9O :")
    doc = parse_las(las.encode("latin-1"))
    assert doc.metadata["COMP"] == "PETROL\xe9O"
