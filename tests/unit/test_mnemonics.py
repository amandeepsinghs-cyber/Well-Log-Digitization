"""Tests for the SPWLA mnemonic reference table.

This table is the single source of truth for which curves are logarithmic and
which track they sit in. A wrong entry here does not crash anything — it
silently mis-plots a curve by orders of magnitude, so each property that the
rest of the pipeline relies on is pinned explicitly below.
"""

from __future__ import annotations

import pytest

from app.contracts import ScaleType
from app.las.mnemonics import (
    ALIASES,
    SPWLA_MNEMONICS,
    is_depth,
    lookup,
    normalise,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RT", "ILD"),        # Schlumberger true resistivity
        ("LLD", "ILD"),       # laterolog deep
        ("MSFL", "RXO"),      # micro-spherically focused, a shallow reading
        ("TNPH", "NPHI"),     # thermal neutron porosity
        ("RHOZ", "RHOB"),     # Litho-Density bulk density
        ("HCAL", "CALI"),     # hole caliper
    ],
)
def test_vendor_aliases_resolve_to_canonical_mnemonics(raw: str, expected: str) -> None:
    """A LAS from any service company must read as the same measurement."""
    assert normalise(raw) == expected
    assert lookup(raw) is not None
    assert lookup(raw).mnemonic == expected


@pytest.mark.parametrize("raw", ["gr", " GR ", "GR:1", "GR.EDTC"])
def test_normalise_strips_case_whitespace_and_tool_qualifiers(raw: str) -> None:
    """Real LAS mnemonics carry run and tool suffixes that a human ignores."""
    assert normalise(raw) == "GR"


@pytest.mark.parametrize("mnemonic", ["ILD", "ILM", "RXO"])
def test_all_resistivity_curves_are_logarithmic(mnemonic: str) -> None:
    """Reading a resistivity track linearly is a 5x error at mid-track.

    0.2-20 ohm.m is two decades. Halfway across is 2.0 ohm.m in log space but
    10.1 ohm.m if interpolated linearly. The endpoints agree, which is exactly
    what makes the mistake survive a spot check.
    """
    spec = SPWLA_MNEMONICS[mnemonic]
    assert spec.scale_type is ScaleType.LOGARITHMIC
    assert spec.track == 2
    assert (spec.display_min, spec.display_max) == (0.2, 20.0)


def test_resistivity_curves_are_distinguishable_by_line_style() -> None:
    """Track 2 prints three black curves; only the dash pattern separates them."""
    styles = {m: SPWLA_MNEMONICS[m].dash for m in ("ILD", "ILM", "RXO")}
    assert styles["ILD"] == ()            # deep is solid
    assert len(set(styles.values())) == 3  # all three differ


def test_nphi_scale_is_deliberately_reversed() -> None:
    """NPHI runs 0.45 -> -0.15 so a density crossover reads as hydrocarbon.

    display_min > display_max is intentional. Any code that "corrects" this by
    sorting the pair will flip the curve and invert the crossover.
    """
    nphi = SPWLA_MNEMONICS["NPHI"]
    assert nphi.display_min > nphi.display_max
    assert (nphi.display_min, nphi.display_max) == (0.45, -0.15)


def test_dt_scale_is_also_reversed() -> None:
    """Sonic is conventionally plotted 140 -> 40 us/ft alongside RHOB."""
    dt = SPWLA_MNEMONICS["DT"]
    assert dt.display_min > dt.display_max


def test_unknown_mnemonic_returns_none_rather_than_guessing() -> None:
    """An unrecognised curve is carried through the LAS but never plotted.

    Guessing a scale type is worse than declining: a resistivity curve drawn on
    a linear axis looks plausible and is wrong by an order of magnitude.
    """
    assert lookup("XYZQ") is None
    assert lookup("") is None


@pytest.mark.parametrize("raw", ["DEPT", "depth", "MD", "TVD", "DEPT.FT"])
def test_depth_mnemonics_are_recognised_as_the_index(raw: str) -> None:
    assert is_depth(raw) is True


@pytest.mark.parametrize("raw", ["GR", "ILD", "NPHI"])
def test_measured_curves_are_not_treated_as_depth(raw: str) -> None:
    assert is_depth(raw) is False


def test_every_alias_points_at_a_real_entry() -> None:
    """A typo in the alias table would silently drop a curve from every track."""
    missing = {a: t for a, t in ALIASES.items() if t not in SPWLA_MNEMONICS}
    assert not missing, f"aliases point at unknown mnemonics: {missing}"


def test_every_curve_is_assigned_to_a_valid_spwla_track() -> None:
    """The renderer builds exactly three tracks; a track 4 would be dropped."""
    for mnemonic, spec in SPWLA_MNEMONICS.items():
        assert spec.track in (1, 2, 3), f"{mnemonic} has track {spec.track}"
        assert spec.display_min != spec.display_max, f"{mnemonic} has zero span"
