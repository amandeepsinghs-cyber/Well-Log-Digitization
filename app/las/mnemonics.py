"""SPWLA curve mnemonic reference: name, unit, track assignment and scale type.

In : A curve mnemonic as it appears in a LAS ~CURVE section.
Out: A MnemonicSpec describing how that curve should be read and drawn.
Rule: Domain data, not an algorithm. This is the single place that decides which
      curves are logarithmic and which track they belong to. Nothing else in the
      codebase may hardcode a scale type or a track number.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    from app.contracts import ScaleType
except ImportError:
    from contracts import ScaleType


@dataclass(frozen=True)
class MnemonicSpec:
    """How a single curve is interpreted and rendered."""

    mnemonic: str        # Canonical SPWLA mnemonic
    description: str     # Full name shown in the track header
    unit: str            # Conventional LAS unit string
    track: int           # SPWLA track number: 1, 2 or 3
    scale_type: ScaleType
    display_min: float   # Conventional scale at the track's left edge
    display_max: float   # Conventional scale at the track's right edge
    colour: str          # Hex colour, chosen to resemble a printed log
    dash: tuple[int, ...] = ()  # Vega strokeDash; empty means solid


# Resistivity curves are logarithmic and share track 2. The conventional API
# display is two decades, 0.2 to 20 ohm.m, which matches the source scan header.
# Deep/medium/shallow are distinguished by line style exactly as they are in
# print: deep solid, medium dashed, shallow dotted.
_RESISTIVITY_SCALE = (0.2, 20.0)

SPWLA_MNEMONICS: dict[str, MnemonicSpec] = {
    # -- Track 1: lithology and permeability indicators, linear -------------
    "GR": MnemonicSpec(
        "GR", "Gamma Ray", "GAPI", 1, ScaleType.LINEAR, 0.0, 150.0, "#4C7C2F"
    ),
    "SP": MnemonicSpec(
        "SP", "Spontaneous Potential", "MV", 1, ScaleType.LINEAR, -80.0, 20.0, "#1A1A1A"
    ),
    "CALI": MnemonicSpec(
        "CALI", "Caliper", "IN", 1, ScaleType.LINEAR, 6.0, 16.0, "#8C6E3F", (4, 2)
    ),

    # -- Track 2: resistivity, logarithmic ----------------------------------
    "ILD": MnemonicSpec(
        "ILD", "Resistivity, Deep", "OHMM", 2, ScaleType.LOGARITHMIC,
        *_RESISTIVITY_SCALE, "#1A1A1A",
    ),
    "ILM": MnemonicSpec(
        "ILM", "Resistivity, Medium", "OHMM", 2, ScaleType.LOGARITHMIC,
        *_RESISTIVITY_SCALE, "#1A1A1A", (8, 4),
    ),
    "RXO": MnemonicSpec(
        "RXO", "Resistivity, Shallow", "OHMM", 2, ScaleType.LOGARITHMIC,
        *_RESISTIVITY_SCALE, "#1A1A1A", (2, 3),
    ),

    # -- Track 3: porosity and density, linear ------------------------------
    # NPHI runs high-to-low: the conventional display is reversed so that a
    # neutron/density crossover reads as hydrocarbon. display_min is therefore
    # GREATER than display_max, which is intentional and must be preserved.
    "NPHI": MnemonicSpec(
        "NPHI", "Neutron Porosity", "V/V", 3, ScaleType.LINEAR, 0.45, -0.15, "#1A1A1A"
    ),
    "RHOB": MnemonicSpec(
        "RHOB", "Bulk Density", "G/C3", 3, ScaleType.LINEAR, 1.90, 2.90, "#B5651D"
    ),
    "PEF": MnemonicSpec(
        "PEF", "Photoelectric Factor", "B/E", 3, ScaleType.LINEAR, 0.0, 10.0, "#7A4FBF", (4, 2)
    ),
    "DT": MnemonicSpec(
        "DT", "Sonic Transit Time", "US/F", 3, ScaleType.LINEAR, 140.0, 40.0, "#2F6C9E"
    ),
}


# Vendor and tool variants that mean the same measurement. Mapped to the
# canonical mnemonic above so a LAS from any service company reads correctly.
ALIASES: dict[str, str] = {
    # Gamma ray
    "GRD": "GR", "SGR": "GR", "GRGC": "GR", "GAMMA": "GR",
    # Deep resistivity
    "RT": "ILD", "LLD": "ILD", "RD": "ILD", "AT90": "ILD", "RES_DEEP": "ILD",
    "RDEEP": "ILD", "M2R9": "ILD",
    # Medium resistivity
    "LLM": "ILM", "RM": "ILM", "AT30": "ILM", "RES_MED": "ILM", "M2R6": "ILM",
    # Shallow resistivity
    "LLS": "RXO", "SFL": "RXO", "MSFL": "RXO", "RS": "RXO", "AT10": "RXO",
    "RES_SHAL": "RXO", "M2R1": "RXO",
    # Porosity / density
    "NPOR": "NPHI", "TNPH": "NPHI", "NPRL": "NPHI", "CNC": "NPHI",
    "RHOZ": "RHOB", "DEN": "RHOB", "ZDEN": "RHOB",
    "PE": "PEF", "PEFZ": "PEF",
    "DTC": "DT", "AC": "DT", "DT24": "DT",
    # Caliper
    "CAL": "CALI", "CALS": "CALI", "HCAL": "CALI",
}

# Depth mnemonics are the index, never a plotted curve.
DEPTH_MNEMONICS: frozenset[str] = frozenset({"DEPT", "DEPTH", "MD", "TVD"})


def normalise(mnemonic: str) -> str:
    """Upper-case and strip a raw LAS mnemonic, then resolve any known alias.

    LAS files in the wild carry trailing tool qualifiers and inconsistent case,
    so a raw lookup misses curves that are plainly recognisable to a human.
    """
    key = (mnemonic or "").strip().upper()
    # Drop a trailing tool/run qualifier such as "GR:1" or "RHOB.LDL".
    for separator in (":", "."):
        if separator in key:
            key = key.split(separator, 1)[0]
    return ALIASES.get(key, key)


def lookup(mnemonic: str) -> MnemonicSpec | None:
    """Return the spec for a mnemonic, or None if it is not one we plot.

    Returning None is meaningful: an unrecognised curve is carried through to
    the LAS unchanged but is not assigned to a track, because guessing its
    scale type could silently mis-plot it by orders of magnitude.
    """
    return SPWLA_MNEMONICS.get(normalise(mnemonic))


def is_depth(mnemonic: str) -> bool:
    """True if the mnemonic is the depth index rather than a measured curve."""
    return (mnemonic or "").strip().upper().split(".")[0] in DEPTH_MNEMONICS


def _title_key(title: str) -> str:
    """Reduce a printed header title to a comparable key.

    A scanned header prints the curve's full name, not its mnemonic, and prints
    it with whatever spacing the draughtsman used. Case and runs of whitespace
    are therefore discarded; the words themselves are not.
    """
    return " ".join((title or "").upper().split()).strip(" .,:")


# Printed header titles, e.g. "Resistivity, Deep", resolved to their spec. The
# descriptions above were written to match what a log sheet actually prints,
# which is what makes this index possible.
_BY_TITLE: dict[str, MnemonicSpec] = {
    _title_key(spec.description): spec for spec in SPWLA_MNEMONICS.values()
}


def lookup_by_title(title: str) -> MnemonicSpec | None:
    """Return the spec for a printed track-header title, or None if unknown.

    Used when digitising a scan, where the header reads "Gamma Ray" rather than
    "GR". None means the curve is not one we can place on a track, and the
    caller must say so rather than guess a scale.
    """
    return _BY_TITLE.get(_title_key(title))

