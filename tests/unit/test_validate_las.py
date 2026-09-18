"""Tests for the CWLS LAS 2.0 structural validator.

Two properties matter more than any individual check, and both are pinned here:

1. A known-good file produces NO findings. A validator that cries wolf on the
   scaffold fixture would be switched off within a week.
2. The validator NEVER raises. It is the thing we reach for when a file is
   suspect, so crashing on a broken file removes the only tool that could
   explain it.

Every other test breaks exactly one header rule in an otherwise valid file, so a
failure names the check that regressed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts import FindingSeverity
from app.las.validate_las import validate_las

FIXTURE = Path(__file__).parents[1] / "fixtures" / "las" / "SCAFFOLD-TEST-0001.las"


def _las(
    *,
    version: str = "2.0",
    strt: str = "7000.0",
    stop: str = "7001.0",
    step: str = "0.5",
    null: str = "-999.25",
    well: str = "TEST-1",
    well_unit: str = "FT",
    curve_lines: str = "DEPT.FT  : Depth\nGR  .GAPI : Gamma Ray",
    rows: str = "7000.0 10.0\n7000.5 20.0\n7001.0 30.0",
    drop: tuple[str, ...] = (),
) -> str:
    """Smallest LAS 2.0 file lasio accepts, with every checked field open.

    `drop` removes a ~WELL line entirely, which is how a file ends up missing a
    required item — writing it blank is a different defect that lasio itself
    normalises away.
    """
    well_lines = {
        "STRT": f"STRT.{well_unit} {strt} :",
        "STOP": f"STOP.{well_unit} {stop} :",
        "STEP": f"STEP.{well_unit} {step} :",
        "NULL": f"NULL.     {null} :",
        "WELL": f"WELL.     {well} :",
    }
    body = "\n".join(line for name, line in well_lines.items() if name not in drop)
    return (
        "~VERSION\n"
        f"VERS. {version} : CWLS LOG ASCII STANDARD\n"
        "WRAP. NO  : ONE LINE PER DEPTH STEP\n"
        "~WELL\n"
        f"{body}\n"
        "~CURVE\n"
        f"{curve_lines}\n"
        "~ASCII\n"
        f"{rows}\n"
    )


def _messages(las: str) -> str:
    """All finding messages joined, for substring assertions."""
    return " | ".join(f.message for f in validate_las(las))


def _severities(las: str) -> list[FindingSeverity]:
    return [f.severity for f in validate_las(las)]


# -- The two properties that matter ------------------------------------------

def test_the_scaffold_fixture_is_clean() -> None:
    """The hand-authored step-14 LAS must validate with zero findings.

    It is the reference for everything the writer will later emit, so if the
    validator disagrees with it, one of the two is wrong and it is not obvious
    which.
    """
    assert validate_las(FIXTURE.read_bytes()) == []


def test_a_minimal_valid_file_is_clean() -> None:
    assert validate_las(_las()) == []


@pytest.mark.parametrize(
    "rubbish",
    [
        b"",
        b"this is not a LAS file at all",
        b"\x00\x01\x02\x03",
        "~VERSION\nVERS. 2.0 :\n~ASCII\nnot numbers here\n".encode(),
    ],
)
def test_unreadable_input_returns_a_finding_and_never_raises(rubbish: bytes) -> None:
    """Input this broken must still come back as findings, not a traceback.

    lasio salvages whatever structure it can and invents defaults for the rest,
    so lesser warnings may accompany the errors. What matters is that at least
    one ERROR is raised as a finding and nothing propagates out.
    """
    findings = validate_las(rubbish)
    assert any(f.severity == FindingSeverity.ERROR for f in findings)


def test_an_unreadable_file_reports_once_not_in_cascade() -> None:
    """Stopping at the first total failure keeps the report readable."""
    findings = validate_las(b"not a LAS file")
    assert len(findings) == 1
    assert findings[0].mnemonic == "HEADER"
    assert "not readable as LAS" in findings[0].message


def test_bytes_and_text_are_validated_identically() -> None:
    """The validator must decode exactly as the parser does, or it guards nothing."""
    raw = FIXTURE.read_bytes()
    assert validate_las(raw) == validate_las(raw.decode("utf-8"))


# -- Version ------------------------------------------------------------------

@pytest.mark.parametrize("version", ["1.2", "3.0"])
def test_non_2_0_version_warns_but_still_validates(version: str) -> None:
    """Other versions are readable, but their section rules are not honoured.

    Only the presence of the warning is asserted, not that it stands alone.
    lasio applies the declared version's section semantics, so a 1.2-labelled
    file has its ~WELL values read from the other side of the colon and drops
    items — which is precisely the damage the warning exists to announce.
    """
    findings = validate_las(_las(version=version))
    version_warnings = [
        f for f in findings
        if f.severity == FindingSeverity.WARNING and f"version {version}" in f.message
    ]
    assert len(version_warnings) == 1


# -- Required ~WELL items ------------------------------------------------------

@pytest.mark.parametrize("missing", ["STRT", "STOP", "NULL", "WELL"])
def test_missing_required_well_item_is_an_error(missing: str) -> None:
    findings = validate_las(_las(drop=(missing,)))
    assert FindingSeverity.ERROR in [f.severity for f in findings]
    assert missing in _messages(_las(drop=(missing,)))


def test_all_missing_items_are_reported_in_one_finding() -> None:
    """One header finding listing four gaps beats four findings saying the same."""
    findings = [
        f for f in validate_las(_las(drop=("STRT", "STOP", "NULL", "WELL")))
        if f.mnemonic == "HEADER"
    ]
    assert len(findings) == 1
    for name in ("STRT", "STOP", "NULL", "WELL"):
        assert name in findings[0].message


# -- NULL convention -----------------------------------------------------------

def test_non_standard_null_sentinel_warns() -> None:
    """-9999 reads fine here but would be counted as a real value downstream."""
    assert "-999.25 convention" in _messages(_las(null="-9999.0"))


def test_missing_null_is_not_reported_twice() -> None:
    """It is already covered as a missing required item."""
    messages = _messages(_las(drop=("NULL",)))
    assert "convention" not in messages


# -- Depth unit agreement ------------------------------------------------------

def test_feet_versus_metres_conflict_is_an_error() -> None:
    """The one defect nothing downstream can detect: depths off by 3.28x."""
    las = _las(well_unit="M", curve_lines="DEPT.FT : Depth\nGR .GAPI : Gamma Ray")
    findings = [f for f in validate_las(las) if f.mnemonic == "DEPTH"]
    assert findings
    assert findings[0].severity == FindingSeverity.ERROR
    assert "3.28x" in findings[0].message


@pytest.mark.parametrize(("well_unit", "curve_unit"), [("F", "FT"), ("FEET", "FT"), ("M", "METRES")])
def test_equivalent_unit_spellings_do_not_conflict(well_unit: str, curve_unit: str) -> None:
    """F, FT and FEET are the same unit; flagging them would train users to ignore us."""
    las = _las(
        well_unit=well_unit,
        curve_lines=f"DEPT.{curve_unit} : Depth\nGR .GAPI : Gamma Ray",
    )
    assert validate_las(las) == []


# -- Depth frame ---------------------------------------------------------------

def test_declared_strt_disagreeing_with_the_data_warns() -> None:
    assert "declares STRT" in _messages(_las(strt="6000.0"))


def test_declared_stop_disagreeing_with_the_data_warns() -> None:
    assert "declares STOP" in _messages(_las(stop="8000.0"))


def test_non_monotonic_depth_is_an_error() -> None:
    """Spliced or mis-ordered runs plot as a scribble, not as a log."""
    las = _las(stop="7000.5", rows="7000.0 10.0\n7001.0 20.0\n7000.5 30.0")
    findings = [f for f in validate_las(las) if "not strictly increasing" in f.message]
    assert findings and findings[0].severity == FindingSeverity.ERROR


def test_a_single_sample_cannot_establish_a_step() -> None:
    las = _las(stop="7000.0", rows="7000.0 10.0")
    findings = [f for f in validate_las(las) if f.mnemonic == "DEPTH"]
    assert findings and findings[0].severity == FindingSeverity.ERROR


def test_declared_step_disagreeing_with_measured_spacing_warns() -> None:
    assert "declares STEP" in _messages(_las(step="0.25"))


def test_step_rounding_within_one_percent_is_accepted() -> None:
    """Vendors round 0.1524 m to 0.15, and that is convention, not a defect."""
    las = _las(
        well_unit="M",
        strt="100.0000",
        stop="100.3048",
        step="0.15",
        curve_lines="DEPT.M : Depth\nGR .GAPI : Gamma Ray",
        rows="100.0000 10.0\n100.1524 20.0\n100.3048 30.0",
    )
    assert validate_las(las) == []


def test_step_zero_on_irregular_data_is_accepted() -> None:
    """STEP 0 is the LAS convention for irregular sampling."""
    las = _las(stop="7001.5", step="0.0", rows="7000.0 10.0\n7000.5 20.0\n7001.5 30.0")
    assert validate_las(las) == []


def test_step_zero_on_evenly_sampled_data_warns() -> None:
    """Honest, but it makes every downstream tool resample for nothing."""
    assert "irregular sampling" in _messages(_las(step="0.0"))


# -- ~CURVE section ------------------------------------------------------------

def test_a_file_with_no_measured_curves_is_an_error() -> None:
    las = _las(curve_lines="DEPT.FT : Depth", rows="7000.0\n7000.5\n7001.0")
    findings = validate_las(las)
    assert any("no measured curves" in f.message for f in findings)


def test_duplicate_mnemonics_warn() -> None:
    """lasio renames the second to 'GR:1'; that hides the ambiguity, not fixes it."""
    las = _las(
        curve_lines="DEPT.FT : Depth\nGR .GAPI : Gamma Ray\nGR .GAPI : Gamma Ray",
        rows="7000.0 10.0 11.0\n7000.5 20.0 21.0\n7001.0 30.0 31.0",
    )
    findings = [f for f in validate_las(las) if f.mnemonic == "GR"]
    assert findings and "2 curves resolve" in findings[0].message


def test_an_alias_is_recognised_and_not_flagged() -> None:
    """A Schlumberger 'RT' is a deep resistivity; flagging it would be noise."""
    las = _las(curve_lines="DEPT.FT : Depth\nRT .OHMM : Resistivity, Deep")
    assert validate_las(las) == []


def test_an_unknown_mnemonic_warns_and_is_not_dropped() -> None:
    """We cannot plot it, but we say so — silently discarding a curve is worse."""
    las = _las(curve_lines="DEPT.FT : Depth\nXYZQ .UNIT : Mystery tool")
    findings = validate_las(las)
    assert [f.severity for f in findings] == [FindingSeverity.WARNING]
    assert "not a recognised SPWLA mnemonic" in findings[0].message
    assert "carried through unchanged" in findings[0].message


def test_an_unconventional_unit_is_informational_only() -> None:
    """A V/V porosity plotted on a 0-45 percent scale is off by 100x."""
    las = _las(curve_lines="DEPT.FT : Depth\nNPHI .PU : Neutron Porosity")
    findings = validate_las(las)
    assert [f.severity for f in findings] == [FindingSeverity.INFO]
    assert findings[0].mnemonic == "NPHI"
    assert "PU" in findings[0].message


def test_a_curve_with_no_declared_unit_is_not_flagged() -> None:
    """An absent unit is a gap in the file, not a contradiction of convention."""
    las = _las(curve_lines="DEPT.FT : Depth\nGR . : Gamma Ray")
    assert validate_las(las) == []


# -- Findings carry enough context to act on ----------------------------------

def test_every_finding_names_a_mnemonic_and_an_interval() -> None:
    """A finding a petrophysicist cannot locate in the log is not actionable."""
    las = _las(version="1.2", strt="6000.0", null="-9999.0", step="0.25")
    findings = validate_las(las)
    assert len(findings) >= 4
    for finding in findings:
        assert finding.mnemonic
        assert len(finding.depth_interval) == 2
        assert finding.message.endswith(".")


def test_header_only_findings_carry_no_depth_interval() -> None:
    """(0.0, 0.0) is how a whole-file defect says it belongs to no depth."""
    findings = [f for f in validate_las(_las(version="1.2")) if f.mnemonic == "HEADER"]
    assert findings and all(f.depth_interval == (0.0, 0.0) for f in findings)
