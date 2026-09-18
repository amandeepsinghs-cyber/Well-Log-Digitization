"""Turn the transcribed header text into numbers the pipeline can calibrate with.

In : the SheetText read in step 38, plus the PageLayout it was read from.
Out: one AxisCalibration per curve, the depth unit, and depth label values.
Rule: entirely deterministic. Everything here is ordinary string and arithmetic
      work on text the model transcribed verbatim, which is what makes a wrong
      value in the output traceable to either a misread character or a bug here,
      never to a judgement call.
"""

from __future__ import annotations

import logging
import re

try:
    from app.contracts import (
        AxisCalibration,
        ColumnSpan,
        CurveHeaderText,
        PageLayout,
        ScaleType,
        SheetText,
        TrackBounds,
    )
    from app.las.mnemonics import MnemonicSpec, lookup_by_title
except ImportError:
    from contracts import (
        AxisCalibration,
        ColumnSpan,
        CurveHeaderText,
        PageLayout,
        ScaleType,
        SheetText,
        TrackBounds,
    )
    from las.mnemonics import MnemonicSpec, lookup_by_title

logger = logging.getLogger(__name__)

# A scale line reads "<min> <unit> <max>", e.g. "0 gAPI 150" or "0.2 ohm.m 20".
# The unit is whatever sits between the two numbers. A leading minus may be a
# hyphen or the typographic minus U+2212, which is what a typeset log prints.
_NUMBER = r"[-\u2212]?\d+(?:[.,]\d+)?"
_SCALE_LINE = re.compile(rf"^\s*({_NUMBER})\s+(.+?)\s+({_NUMBER})\s*$")

# Printed unit → the canonical LAS unit and the factor that converts printed
# values into it. The only real conversion on a standard sheet is neutron
# porosity, which is printed in percent but written to LAS as a fraction; a
# 45 % scale stored as 45 would be off by two orders of magnitude.
_PRINTED_UNITS: dict[str, tuple[str, float]] = {
    "GAPI": ("GAPI", 1.0),
    "API": ("GAPI", 1.0),
    "MV": ("MV", 1.0),
    "OHMM": ("OHMM", 1.0),
    "%": ("V/V", 0.01),
    "PU": ("V/V", 0.01),   # porosity units are percent by another name
    "VV": ("V/V", 1.0),
    "GCM3": ("G/C3", 1.0),
    "GCC": ("G/C3", 1.0),
    "GC3": ("G/C3", 1.0),
    "KGM3": ("G/C3", 0.001),
    "IN": ("IN", 1.0),
    "USF": ("US/F", 1.0),
    "BE": ("B/E", 1.0),
}

# Depth unit words as they are printed on a sheet, e.g. "Depth, ft".
_DEPTH_UNITS: dict[str, str] = {
    "FT": "FT", "FEET": "FT", "F": "FT",
    "M": "M", "METRE": "M", "METRES": "M", "METER": "M", "METERS": "M",
}

# A scale is logarithmic if it spans whole decades. Resistivity prints
# 0.2 → 20, a ratio of exactly 100. Linear scales do not come close: the widest
# on a standard sheet is bulk density at 1.90 → 2.90, a ratio of 1.5. Ten is
# therefore a wide moat, not a fine judgement.
_LOG_SCALE_MIN_RATIO = 10.0


def parse_scales(text: SheetText, layout: PageLayout) -> tuple[AxisCalibration, ...]:
    """Convert each transcribed curve header into a calibrated value axis.

    Args:
        text: the verbatim header text.
        layout: the page decomposition, giving each track's pixel columns.

    Returns:
        One AxisCalibration per curve, in the order the headers were read.

    Raises:
        ValueError: if a title is not a curve we can place, if a scale line does
            not parse, if a unit is unknown, or if two curves in one track imply
            different scale types. Each means a value axis would have to be
            guessed, and a guessed axis produces confident, wrong numbers.
    """
    tracks = {track.name: track for track in layout.tracks}
    calibrations = [_one_axis(curve, tracks) for curve in text.curves]

    _check_one_scale_type_per_track(calibrations)

    logger.info(
        "parse_scales: OK - %s",
        [
            f"{axis.mnemonic} {axis.value_min}-{axis.value_max} {axis.unit} "
            f"{axis.scale_type.value} on {axis.track_bounds.track_name}"
            for axis in calibrations
        ],
    )
    return tuple(calibrations)


def parse_depth_unit(depth_header: str) -> str:
    """Read the depth unit from the depth column's header, e.g. "Depth, ft".

    Raises:
        ValueError: if no known unit word is present. Assuming feet on a metric
            log would misplace every sample by a factor of 3.28.
    """
    words = re.split(r"[^A-Za-z]+", depth_header.upper())
    for word in words:
        if word in _DEPTH_UNITS:
            return _DEPTH_UNITS[word]
    raise ValueError(
        f"No depth unit found in the depth header {depth_header!r}. Expected a "
        f"word such as {sorted(set(_DEPTH_UNITS))}."
    )


def parse_depth_label(label: str) -> float:
    """Read a printed depth label, e.g. "7,000" → 7000.0.

    Thousands separators are printed on log sheets and are not decimal points,
    so they are removed rather than parsed.
    """
    cleaned = label.replace(",", "").replace("\u2212", "-").strip()
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"Depth label {label!r} is not a number. Every depth in the output "
            "is fixed by these labels, so a misread one cannot be tolerated."
        ) from exc


def _one_axis(
    curve: CurveHeaderText, tracks: dict[str, ColumnSpan]
) -> AxisCalibration:
    """Build the value axis for one curve from its two header lines."""
    spec = lookup_by_title(curve.title)
    if spec is None:
        raise ValueError(
            f"The header title {curve.title!r} on {curve.track_name} is not a "
            "curve in the SPWLA mnemonic table, so it cannot be assigned a "
            "scale type or written to LAS."
        )

    printed_min, printed_unit, printed_max = _split_scale_line(curve)
    unit, factor = _canonical_unit(printed_unit, spec, curve)
    value_min = printed_min * factor
    value_max = printed_max * factor

    scale_type = _scale_type(value_min, value_max, spec, curve)
    column = tracks[curve.track_name]

    return AxisCalibration(
        mnemonic=spec.mnemonic,
        unit=unit,
        value_min=value_min,
        value_max=value_max,
        scale_type=scale_type,
        track_bounds=TrackBounds(
            # "Track 2" is the second entry in layout.tracks, and the name was
            # generated from that position, so parsing it back is exact.
            track_index=int(column.name.rsplit(" ", 1)[1]),
            track_name=column.name,
            x_left=column.x_left,
            x_right=column.x_right,
            scale_type=scale_type,
        ),
    )


def _split_scale_line(curve: CurveHeaderText) -> tuple[float, str, float]:
    """Split "0.2 ohm.m 20" into its left value, unit and right value.

    The order is left-to-right across the track, NOT low-to-high. Neutron
    porosity prints "45 % -15" and is plotted that way; sorting the two numbers
    would mirror the curve.
    """
    match = _SCALE_LINE.match(curve.scale)
    if match is None:
        raise ValueError(
            f"The scale line {curve.scale!r} for {curve.title!r} is not of the "
            "form '<value> <unit> <value>', so the track's value range is "
            "unknown."
        )
    left, unit, right = match.groups()
    return _to_float(left, curve), unit, _to_float(right, curve)


def _to_float(token: str, curve: CurveHeaderText) -> float:
    """Convert one number from a scale line, allowing a typographic minus.

    A comma is ambiguous in print: "2,000" is two thousand ohm.m on an American
    sheet, while "0,45" is nought point four five on a European one. Three
    digits after the comma settles it, because no European sheet writes a scale
    to three decimal places and no thousands group is any other length. Reading
    "2,000" as 2.0 would understate a resistivity scale a thousandfold.
    """
    cleaned = token.replace("\u2212", "-")
    if re.search(r",\d{3}$", cleaned):
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"{token!r} in the scale line {curve.scale!r} for {curve.title!r} "
            "is not a number."
        ) from exc


def _canonical_unit(
    printed: str, spec: MnemonicSpec, curve: CurveHeaderText
) -> tuple[str, float]:
    """Map a printed unit to its LAS form and the factor to apply.

    Raises:
        ValueError: if the unit is unrecognised, or recognised but belongs to a
            different measurement than the title claims — which means the two
            header lines were transcribed from different curves.
    """
    key = _unit_key(printed)
    if key not in _PRINTED_UNITS:
        raise ValueError(
            f"Unit {printed!r} on {curve.title!r} is not a unit this pipeline "
            f"knows ({sorted(_PRINTED_UNITS)}). Writing an unknown unit into a "
            "LAS file would make the values uninterpretable."
        )

    unit, factor = _PRINTED_UNITS[key]
    if unit != spec.unit:
        raise ValueError(
            f"{curve.title!r} is {spec.mnemonic}, which is measured in "
            f"{spec.unit}, but its scale line reads {printed!r} ({unit}). The "
            "title and the scale line do not describe the same curve."
        )
    return unit, factor


def _unit_key(printed: str) -> str:
    """Reduce a printed unit to a comparable key: "g/cm³" → "GCM3"."""
    key = printed.strip().upper().replace("\u00b3", "3").replace("\u00b2", "2")
    # Percent is the one unit that is punctuation, so it is kept as itself.
    if "%" in key:
        return "%"
    return re.sub(r"[^A-Z0-9]", "", key)


def _scale_type(
    value_min: float, value_max: float, spec: MnemonicSpec, curve: CurveHeaderText
) -> ScaleType:
    """Decide whether the track is linear or logarithmic, two ways, and agree.

    The printed scale is the primary evidence: a range spanning decades and
    never reaching zero can only be a log grid. The mnemonic table is the second
    opinion. Requiring both means the answer is derived from the sheet rather
    than assumed from the curve name, while still catching a misread scale.
    """
    from_print = ScaleType.LINEAR
    if value_min > 0 and value_max > 0:
        ratio = max(value_min, value_max) / min(value_min, value_max)
        if ratio >= _LOG_SCALE_MIN_RATIO:
            from_print = ScaleType.LOGARITHMIC

    if from_print is not spec.scale_type:
        raise ValueError(
            f"The printed scale {curve.scale!r} for {curve.title!r} looks "
            f"{from_print.value}, but {spec.mnemonic} is {spec.scale_type.value} "
            "in the SPWLA table. Either the scale was misread or this is not "
            "the curve the title says it is."
        )
    return from_print


def _check_one_scale_type_per_track(calibrations: list[AxisCalibration]) -> None:
    """A track is one printed grid, so every curve on it shares its scale type."""
    by_track: dict[str, set[ScaleType]] = {}
    for axis in calibrations:
        by_track.setdefault(axis.track_bounds.track_name, set()).add(axis.scale_type)

    for track_name, types in by_track.items():
        if len(types) > 1:
            raise ValueError(
                f"{track_name} carries curves on both "
                f"{sorted(t.value for t in types)} scales, but a track has one "
                "printed grid. One of its scale lines was misread."
            )
