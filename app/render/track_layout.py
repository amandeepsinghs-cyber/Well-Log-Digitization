"""Decide which curve is drawn on which track, and on what scale.

In : a LasDocument parsed out of a digitised LAS file.
Out: a LogPlot — one shared depth index, and the curves grouped onto tracks.
Rule: this file makes layout decisions. It does not know what Vega is.

      A well log is not a chart with seven lines on it. It is three side-by-side
      panels, fixed by SPWLA convention, and a petrophysicist reads them by
      position: lithology on the left, resistivity in the middle, porosity on
      the right. Putting a curve on the wrong track makes the plot wrong in a
      way that still looks plausible, so the assignment is taken from the
      mnemonic table and never guessed here.
"""

from __future__ import annotations

try:
    from app.contracts import (
        CWLS_NULL_VALUE,
        CurvePlot,
        CurveTrace,
        LasDocument,
        LogPlot,
        TrackPlot,
    )
    from app.las.mnemonics import SPWLA_MNEMONICS, lookup
except ImportError:
    from contracts import (
        CWLS_NULL_VALUE,
        CurvePlot,
        CurveTrace,
        LasDocument,
        LogPlot,
        TrackPlot,
    )
    from las.mnemonics import SPWLA_MNEMONICS, lookup

# Importing the mnemonic table is the one project import beyond contracts.py
# that this module makes. It is allowed because SPWLA_MNEMONICS is domain data
# — a constant lookup table of petrophysical convention — and because the table
# is declared to be the only place a track number or scale type may live. The
# alternative, passing the table in as a parameter, would let a caller supply a
# different one and quietly move curves onto the wrong track.


# SPWLA standard display palette for rendered well logs.
# Paper scans historically printed curves in black ink or whatever pen was in
# the plotter. For digital petrophysical presentation, SPWLA standard colours
# and line styles are used:
# - Track 1: GR (solid green), SP (red dashed), CALI (black dashed)
# - Track 2: Deep resistivity ILD (solid red), Medium ILM (blue dashed),
#            Shallow RXO (black dotted)
# - Track 3: Porosity / Density: NPHI (dashed blue), RHOB (solid red)
#            yielding the classic red/blue gas crossover, PEF (purple dashed),
#            DT (cyan/blue solid)
SPWLA_DISPLAY_PALETTE: dict[str, tuple[str, tuple[int, ...]]] = {
    "GR": ("#2E7D32", ()),            # Green solid
    "SP": ("#D32F2F", (4, 2)),         # Red dashed
    "CALI": ("#000000", (4, 2)),       # Black dashed
    "ILD": ("#D32F2F", ()),            # Deep resistivity: Red solid
    "ILM": ("#1976D2", (6, 3)),        # Medium resistivity: Blue dashed
    "RXO": ("#000000", (2, 2)),        # Shallow resistivity: Black dotted
    "NPHI": ("#1976D2", (6, 3)),       # Neutron Porosity: Blue dashed
    "RHOB": ("#D32F2F", ()),           # Bulk Density: Red solid
    "PEF": ("#7B1FA2", (4, 2)),        # Photoelectric Factor: Purple
    "DT": ("#0288D1", ()),             # Sonic: Cyan/Blue
}


def build_log_plot(document: LasDocument) -> LogPlot:
    """Group a LAS document's curves onto their conventional tracks.

    Curves whose mnemonic is not in the SPWLA table are dropped rather than
    guessed onto a track: an unrecognised curve has no known scale, and drawing
    it against another curve's axis would produce a confident wrong reading.
    """
    depths = _shared_depth_index(document)

    # Bucket the curves by track number, preserving the order they appear in
    # the LAS. That order comes from the ~CURVE section, which for a digitised
    # file follows the order the curves were traced off the sheet.
    by_track: dict[int, list[CurvePlot]] = {}
    for trace in document.curves:
        spec = lookup(trace.mnemonic)
        if spec is None:
            # Not an error: a LAS may legitimately carry curves we do not draw.
            continue
        display_colour, display_dash = SPWLA_DISPLAY_PALETTE.get(
            spec.mnemonic, (spec.colour, spec.dash)
        )
        by_track.setdefault(spec.track, []).append(
            CurvePlot(
                mnemonic=spec.mnemonic,
                unit=trace.unit or spec.unit,
                description=spec.description,
                colour=display_colour,
                dash=display_dash,
                scale_type=spec.scale_type,
                display_min=spec.display_min,
                display_max=spec.display_max,
                values=_values_on(depths, trace),
            )
        )

    if not by_track:
        raise ValueError(
            f"No curve in {document.well_name!r} matches the SPWLA mnemonic "
            f"table, so there is nothing to draw. Curves present: "
            f"{[c.mnemonic for c in document.curves]}. "
            f"Known mnemonics: {sorted(SPWLA_MNEMONICS)}."
        )

    # Tracks are drawn left to right in SPWLA track order, and empty tracks are
    # skipped entirely — a log with no resistivity shows two panels, not three
    # with a blank in the middle.
    tracks = tuple(
        TrackPlot(number=number, curves=tuple(by_track[number]))
        for number in sorted(by_track)
    )

    return LogPlot(
        well_name=document.well_name,
        depth_units=document.depth_units,
        depths=depths,
        tracks=tracks,
    )


def _shared_depth_index(document: LasDocument) -> tuple[float, ...]:
    """The depth column every curve in the file is sampled on.

    A LAS 2.0 file has exactly one depth index by definition, so every curve
    must already agree on it. Checking rather than assuming matters because a
    silent mismatch would slide one curve against the others by a few feet and
    still draw a perfectly convincing plot.
    """
    if not document.curves:
        raise ValueError(
            f"{document.well_name!r} has no curves, so there is no depth index."
        )

    first = document.curves[0]
    depths = tuple(sample.depth for sample in first.samples)

    for trace in document.curves[1:]:
        if len(trace.samples) != len(depths):
            raise ValueError(
                f"{trace.mnemonic} has {len(trace.samples)} samples but "
                f"{first.mnemonic} has {len(depths)}. A LAS 2.0 file has one "
                f"depth index shared by every curve; this file does not."
            )

    return depths


def _values_on(depths: tuple[float, ...], trace: CurveTrace) -> tuple[float | None, ...]:
    """Pull a curve's readings out, turning the LAS NULL into a real gap.

    -999.25 is the CWLS convention for "not measured here". Plotted literally it
    would drag the curve far off the left of the track and back again; carried
    as None it becomes JSON null and Vega-Lite simply breaks the line, which is
    what the absence of a measurement actually looks like.
    """
    values = tuple(
        None if sample.value == CWLS_NULL_VALUE else sample.value
        for sample in trace.samples
    )

    if len(values) != len(depths):
        raise ValueError(
            f"{trace.mnemonic} has {len(values)} values against a depth index "
            f"of {len(depths)}."
        )

    return values
