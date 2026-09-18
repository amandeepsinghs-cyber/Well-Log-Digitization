"""Check a LAS file for CWLS 2.0 structural conformance and SPWLA mnemonics.

In : The raw bytes (or text) of a LAS file.
Out: A list of QcFinding. An empty list means the file is structurally sound.
Rule: This checks STRUCTURE only — does the file say what it must say, and does
      it agree with itself. Whether the NUMBERS are physically sensible is
      qc/range_check.py's job (step 58), and how much of the log is covered is
      qc/coverage.py's job (step 57). Keeping those apart means a header defect
      is never reported as a data defect.

      Never raises. A file so broken that lasio cannot read it is itself the
      finding, and a validator that crashed on the worst input would be useless
      exactly when it is needed.
"""

from __future__ import annotations

import io
import math

import lasio
import numpy as np

try:
    from app.contracts import CWLS_NULL_VALUE, FindingSeverity, QcFinding
    from app.las.mnemonics import SPWLA_MNEMONICS, lookup, normalise
    from app.las.parse_las import decode_las_text
except ImportError:
    from contracts import CWLS_NULL_VALUE, FindingSeverity, QcFinding
    from las.mnemonics import SPWLA_MNEMONICS, lookup, normalise
    from las.parse_las import decode_las_text


# CWLS LAS 2.0, section 5: every file must carry these five ~WELL items. STRT,
# STOP and STEP define the depth frame; NULL defines the missing-data sentinel;
# WELL identifies what was logged. A file missing any of them cannot be
# interpreted without guessing.
_REQUIRED_WELL_ITEMS = ("STRT", "STOP", "STEP", "NULL", "WELL")

# Depth frame comparisons are done in feet or metres, so a tolerance of a
# thousandth of a unit is well below anything a log records.
_DEPTH_TOLERANCE = 1e-3

# A declared STEP is accepted if it is within 2% of the measured median spacing.
# Vendor headers routinely round the half-foot metric step 0.1524 m to 0.15,
# which is 1.6% low, and that is convention rather than a defect. 2% admits it
# while still catching a step declared in the wrong unit or off by a factor.
_STEP_RELATIVE_TOLERANCE = 0.02

# Findings that concern the file as a whole rather than a depth interval.
_NO_INTERVAL: tuple[float, float] = (0.0, 0.0)


def validate_las(raw: bytes | str) -> list[QcFinding]:
    """Return every structural conformance problem found in a LAS file."""
    text = decode_las_text(raw) if isinstance(raw, bytes) else raw

    try:
        las = lasio.read(io.StringIO(text))
    except Exception as exc:  # lasio raises a wide range of parse errors
        # An unreadable file is a single, total finding. There is nothing
        # further to inspect, so we stop rather than report cascading noise.
        return [
            QcFinding(
                severity=FindingSeverity.ERROR,
                mnemonic="HEADER",
                depth_interval=_NO_INTERVAL,
                message=f"File is not readable as LAS: {type(exc).__name__}: {exc}",
            )
        ]

    findings: list[QcFinding] = []
    findings += _check_version(las)
    findings += _check_required_well_items(las)
    findings += _check_null_value(las)
    findings += _check_index_unit_agreement(las)
    findings += _check_depth_frame(las)
    findings += _check_curves(las)
    return findings


def _header_item(section, name: str):
    """Return a header item by mnemonic, or None if the section omits it.

    lasio's SectionItems.get() does NOT behave like dict.get: for a missing
    mnemonic it fabricates and returns a brand-new empty HeaderItem, so a
    ``get(...) is None`` test never fires and the following subscript raises
    KeyError. Membership is the only honest way to ask.
    """
    return section[name] if name in section else None


def _check_version(las: lasio.LASFile) -> list[QcFinding]:
    """LAS 2.0 is the only version this pipeline writes and fully understands.

    1.2 lacks the ~CURVE unit conventions we rely on; 3.0 permits multiple data
    sections with different indexes. lasio reads both, so without this check a
    non-2.0 file passes silently and is then written back out as 2.0.
    """
    item = _header_item(las.version, "VERS")
    version = str(item.value).strip() if item is not None else ""
    if version == "2.0":
        return []

    return [
        QcFinding(
            severity=FindingSeverity.WARNING,
            mnemonic="HEADER",
            depth_interval=_NO_INTERVAL,
            message=(
                f"File declares LAS version {version or 'none'}, not 2.0. It was "
                "read, but any section behaviour specific to that version is "
                "not honoured."
            ),
        )
    ]


def _check_required_well_items(las: lasio.LASFile) -> list[QcFinding]:
    """Every ~WELL item CWLS 2.0 mandates must be present and non-empty."""
    missing = [
        name
        for name in _REQUIRED_WELL_ITEMS
        if (item := _header_item(las.well, name)) is None or item.value in (None, "")
    ]
    if not missing:
        return []

    return [
        QcFinding(
            severity=FindingSeverity.ERROR,
            mnemonic="HEADER",
            depth_interval=_NO_INTERVAL,
            message=(
                f"~WELL section is missing required item(s): {', '.join(missing)}. "
                "CWLS LAS 2.0 requires STRT, STOP, STEP, NULL and WELL."
            ),
        )
    ]


def _check_null_value(las: lasio.LASFile) -> list[QcFinding]:
    """The declared NULL should be the -999.25 convention.

    lasio honours whatever the file declares, so a different sentinel reads
    correctly here. It matters downstream: anything comparing against -999.25
    without consulting the header would count real readings as gaps.
    """
    item = _header_item(las.well, "NULL")
    if item is None or item.value in (None, ""):
        return []  # Already reported as a missing required item.

    declared = float(item.value)
    if declared == CWLS_NULL_VALUE:
        return []

    return [
        QcFinding(
            severity=FindingSeverity.WARNING,
            mnemonic="HEADER",
            depth_interval=_NO_INTERVAL,
            message=(
                f"NULL is declared as {declared}, not the {CWLS_NULL_VALUE} "
                "convention. Values were read correctly, but the file will be "
                f"rewritten using {CWLS_NULL_VALUE}."
            ),
        )
    ]


def _check_index_unit_agreement(las: lasio.LASFile) -> list[QcFinding]:
    """~WELL and ~CURVE must agree on whether depth is in feet or metres.

    This is the most damaging header defect there is. One foot is 0.3048 m, so
    reading a metric log as feet stretches every depth by 3.28x while every
    curve value stays plausible. Nothing downstream can detect it.
    """
    curve_unit = _unit_family(las.curves[0].unit if las.curves else "")
    depth_items = (_header_item(las.well, name) for name in ("STRT", "STOP", "STEP"))
    well_units = {
        _unit_family(item.unit) for item in depth_items if item is not None and item.unit
    }
    # Drop unrecognised or absent units; only a genuine FT-vs-M clash counts.
    well_units.discard("")

    if not curve_unit or not well_units or well_units == {curve_unit}:
        return []

    return [
        QcFinding(
            severity=FindingSeverity.ERROR,
            mnemonic="DEPTH",
            depth_interval=_NO_INTERVAL,
            message=(
                f"Depth unit conflict: ~CURVE index says {curve_unit} but ~WELL "
                f"says {'/'.join(sorted(well_units))}. Feet and metres differ by "
                "3.28x, so every depth in this file is suspect."
            ),
        )
    ]


def _unit_family(unit: str | None) -> str:
    """Collapse F/FT/FEET to FT and M/METRES to M; anything else to ''."""
    text = (unit or "").strip().upper()
    if text.startswith("F"):
        return "FT"
    if text.startswith("M"):
        return "M"
    return ""


def _check_depth_frame(las: lasio.LASFile) -> list[QcFinding]:
    """The declared depth frame must match the data actually present."""
    # lasio reads a data section it cannot parse as numbers as an array of
    # strings rather than failing, so the conversion is where a corrupt ~ASCII
    # section first shows itself. It is a finding, never an exception.
    try:
        depth = np.asarray(las.index, dtype=float)
    except (TypeError, ValueError) as exc:
        return [
            QcFinding(
                severity=FindingSeverity.ERROR,
                mnemonic="DEPTH",
                depth_interval=_NO_INTERVAL,
                message=(
                    f"~ASCII depth column is not numeric, so no depth frame can "
                    f"be established: {exc}."
                ),
            )
        ]
    if depth.size < 2:
        return [
            QcFinding(
                severity=FindingSeverity.ERROR,
                mnemonic="DEPTH",
                depth_interval=_NO_INTERVAL,
                message=(
                    f"~ASCII section holds {depth.size} depth sample(s). At least "
                    "2 are needed to establish a depth step."
                ),
            )
        ]

    findings: list[QcFinding] = []
    interval = (float(depth.min()), float(depth.max()))
    spacings = np.diff(depth)

    # Depth must increase downward and never repeat. A non-monotonic index means
    # spliced or mis-ordered runs, and every curve would plot as a scribble.
    if not np.all(spacings > 0):
        offenders = int(np.count_nonzero(spacings <= 0))
        findings.append(
            QcFinding(
                severity=FindingSeverity.ERROR,
                mnemonic="DEPTH",
                depth_interval=interval,
                message=(
                    f"Depth index is not strictly increasing: {offenders} of "
                    f"{spacings.size} steps go backwards or repeat."
                ),
            )
        )

    # STRT and STOP are the header's claim about the data's extent.
    for name, actual in (("STRT", depth[0]), ("STOP", depth[-1])):
        declared = _well_float(las, name)
        if declared is not None and abs(declared - actual) > _DEPTH_TOLERANCE:
            findings.append(
                QcFinding(
                    severity=FindingSeverity.WARNING,
                    mnemonic="DEPTH",
                    depth_interval=interval,
                    message=(
                        f"Header declares {name} {declared} but the data "
                        f"{'starts' if name == 'STRT' else 'ends'} at {actual}."
                    ),
                )
            )

    findings += _check_step(las, depth, spacings, interval)
    return findings


def _check_step(
    las: lasio.LASFile,
    depth: np.ndarray,
    spacings: np.ndarray,
    interval: tuple[float, float],
) -> list[QcFinding]:
    """Compare the declared STEP against the spacing actually present.

    A declared STEP of 0 is the LAS convention for an irregularly sampled log
    and is not a defect, but the sampling should then genuinely be irregular.
    """
    declared = _well_float(las, "STEP")
    if declared is None:
        return []

    measured = float(np.median(spacings))
    is_regular = bool(np.allclose(spacings, measured, rtol=_STEP_RELATIVE_TOLERANCE))

    if declared == 0.0:
        if is_regular:
            return [
                QcFinding(
                    severity=FindingSeverity.WARNING,
                    mnemonic="DEPTH",
                    depth_interval=interval,
                    message=(
                        "Header declares STEP 0 (irregular sampling) but the "
                        f"data is evenly sampled at {measured}. Tools that trust "
                        "the header will resample unnecessarily."
                    ),
                )
            ]
        return []

    if not math.isclose(declared, measured, rel_tol=_STEP_RELATIVE_TOLERANCE):
        return [
            QcFinding(
                severity=FindingSeverity.WARNING,
                mnemonic="DEPTH",
                depth_interval=interval,
                message=(
                    f"Header declares STEP {declared} but the median measured "
                    f"spacing is {measured}. Declare STEP 0 if the log is "
                    "genuinely irregular."
                ),
            )
        ]
    return []


def _well_float(las: lasio.LASFile, name: str) -> float | None:
    """Read a numeric ~WELL item, or None if absent or non-numeric."""
    item = _header_item(las.well, name)
    if item is None or item.value in (None, ""):
        return None
    try:
        return float(item.value)
    except (TypeError, ValueError):
        return None


def _check_curves(las: lasio.LASFile) -> list[QcFinding]:
    """Check the ~CURVE section: no duplicates, and mnemonics we can plot."""
    findings: list[QcFinding] = []
    data_curves = list(las.curves)[1:]

    if not data_curves:
        return [
            QcFinding(
                severity=FindingSeverity.ERROR,
                mnemonic="HEADER",
                depth_interval=_NO_INTERVAL,
                message="~CURVE declares a depth index but no measured curves.",
            )
        ]

    # lasio disambiguates repeated mnemonics by appending ':1', ':2'. That is a
    # rescue, not a fix: two curves called GR mean the file does not say which
    # reading is which.
    seen: dict[str, int] = {}
    for curve in data_curves:
        base = normalise(curve.mnemonic)
        seen[base] = seen.get(base, 0) + 1
    for mnemonic, count in seen.items():
        if count > 1:
            findings.append(
                QcFinding(
                    severity=FindingSeverity.WARNING,
                    mnemonic=mnemonic,
                    depth_interval=_NO_INTERVAL,
                    message=(
                        f"{count} curves resolve to the mnemonic {mnemonic}. Only "
                        "one can be plotted on its track; the rest are ambiguous."
                    ),
                )
            )

    for curve in data_curves:
        findings += _check_one_curve(curve)
    return findings


def _check_one_curve(curve: lasio.CurveItem) -> list[QcFinding]:
    """Recognise the mnemonic, and check its unit against SPWLA convention."""
    spec = lookup(curve.mnemonic)
    if spec is None:
        return [
            QcFinding(
                severity=FindingSeverity.WARNING,
                mnemonic=curve.mnemonic,
                depth_interval=_NO_INTERVAL,
                message=(
                    f"{curve.mnemonic!r} is not a recognised SPWLA mnemonic, so "
                    "it has no track or scale type and will not be plotted. It "
                    f"is carried through unchanged. Known: "
                    f"{', '.join(sorted(SPWLA_MNEMONICS))}."
                ),
            )
        ]

    # A unit mismatch is informational: the curve still plots, but the axis
    # label would be wrong, and a V/V porosity plotted on a 0-45 percent scale
    # is off by 100x.
    declared = (curve.unit or "").strip().upper()
    if declared and declared != spec.unit:
        return [
            QcFinding(
                severity=FindingSeverity.INFO,
                mnemonic=spec.mnemonic,
                depth_interval=_NO_INTERVAL,
                message=(
                    f"{spec.mnemonic} is in {declared}, not the conventional "
                    f"{spec.unit}. Confirm the scale before reading values off "
                    "the track."
                ),
            )
        ]
    return []
