"""Write a LasDocument out as the bytes of a CWLS LAS 2.0 file.

In : a LasDocument whose curves were resampled onto a common depth step.
Out: LAS 2.0 bytes, ready to hand to gcs/write_bytes.py.
Rule: thin adapter over lasio, and the inverse of las/parse_las.py. lasio owns
      section formatting, column alignment and header padding; this file owns
      exactly two things lasio cannot do for us — laying curves of differing
      extent onto one shared depth index, and stating the depth frame.

      Its output must pass las/validate_las.py with zero ERROR findings. That
      is the contract between the two halves of this project.
"""

from __future__ import annotations

import io
import logging

import lasio
import numpy as np

try:
    from app.contracts import CWLS_NULL_VALUE, CurveTrace, LasDocument
except ImportError:
    from contracts import CWLS_NULL_VALUE, CurveTrace, LasDocument

logger = logging.getLogger(__name__)

# Decimal places used to match a curve's sample depth against the shared index.
# Every curve on one sheet is resampled onto multiples of the same step, so the
# depths agree to the last bit in almost every case — but they are reached by
# different amounts of floating-point addition, so exact equality is not safe
# to rely on. Four decimals is 0.0001 ft, which is three orders of magnitude
# finer than any logging tool resolves and many orders coarser than the error
# being guarded against.
_DEPTH_MATCH_DECIMALS = 4

# Numeric format for the ~ASCII section. Four decimals is set by the smallest
# quantity written: neutron porosity in V/V, where one pixel of the source scan
# is about 0.001 V/V. Four decimals therefore preserves a tenth of a pixel and
# rounds nothing away, while keeping the file readable.
_DATA_FORMAT = "%.4f"

# CWLS LAS 2.0 descriptions for the mandatory ~WELL items. Written explicitly
# rather than left to lasio's defaults so the file says the same thing whatever
# version of lasio produced it.
_STRT_DESCR = "START DEPTH"
_STOP_DESCR = "STOP DEPTH"
_STEP_DESCR = "STEP"
_NULL_DESCR = "NULL VALUE"
_WELL_DESCR = "WELL"

# The five ~WELL items written from LasDocument's own typed fields. Anything of
# the same name arriving in .metadata is ignored rather than allowed to
# contradict them.
_AUTHORITATIVE_WELL_ITEMS = frozenset({"STRT", "STOP", "STEP", "NULL", "WELL"})

# LAS 2.0 specifies an ASCII file, but a scanned header can legitimately put a
# non-ASCII character into a provenance line — "g/cm³" is printed on the source
# sheet. UTF-8 encodes those and is byte-identical to ASCII for everything else.
_ENCODING = "utf-8"


def write_las(document: LasDocument) -> bytes:
    """Serialise a LasDocument as CWLS LAS 2.0 bytes."""
    grid = _depth_grid(document)

    las = lasio.LASFile()
    _write_well_section(las, document, grid)

    # LAS 2.0 fixes the first curve as the depth index, and lasio follows that
    # rule: whichever curve is appended first becomes .index on the way back in.
    las.append_curve(
        "DEPT", grid, unit=document.depth_units, descr="DEPTH"
    )
    for curve in document.curves:
        las.append_curve(
            curve.mnemonic,
            _curve_on_grid(curve, grid),
            unit=curve.unit,
            descr=curve.description,
        )

    # ~Other is where a file declares how it came to exist. Without it a curve
    # traced off a photograph is indistinguishable from a recorded measurement.
    las.other = "\n".join(document.provenance_comments)

    text = io.StringIO()
    las.write(text, version=2.0, fmt=_DATA_FORMAT)
    data = text.getvalue().encode(_ENCODING)

    logger.info(
        "write_las: OK - %s, %d curve(s), %d depth(s) %.1f-%.1f %s -> %d byte(s)",
        document.well_name,
        len(document.curves),
        grid.size,
        document.depth_min,
        document.depth_max,
        document.depth_units,
        len(data),
    )
    return data


def _depth_grid(document: LasDocument) -> np.ndarray:
    """Build the single depth index every curve is written against.

    The curves reaching this point have a common step but not a common extent:
    a curve first seen twenty feet below the top of the sheet starts twenty
    feet down. LAS has one index column, so the file spans the union and any
    curve absent at a given depth is NULL there.

    The grid is built from a sample COUNT rather than by walking from min to
    max, so accumulated floating-point error cannot make it overshoot STOP.
    """
    if document.depth_step <= 0:
        raise ValueError(
            f"Depth step must be positive to build a LAS index, got "
            f"{document.depth_step} for well {document.well_name!r}"
        )
    span = document.depth_max - document.depth_min
    if span < 0:
        raise ValueError(
            f"Depth range runs backwards for well {document.well_name!r}: "
            f"{document.depth_min} to {document.depth_max}"
        )

    count = round(span / document.depth_step) + 1
    if count < 2:
        # las/parse_las.py and las/validate_las.py both reject a log with fewer
        # than two depths, because no step can be established from one row.
        # Refusing here too means this pipeline can never emit a file its own
        # reader will not take back.
        raise ValueError(
            f"Depth range {document.depth_min} to {document.depth_max} at step "
            f"{document.depth_step} yields {count} sample(s) for well "
            f"{document.well_name!r}; a LAS needs at least 2 to establish a step"
        )
    return document.depth_min + document.depth_step * np.arange(count)


def _curve_on_grid(curve: CurveTrace, grid: np.ndarray) -> np.ndarray:
    """Lay one curve's samples onto the shared index, NULL where it has none.

    Raises if any sample fails to land. A curve that was never resampled onto
    this step would otherwise write out as a column of nothing but NULL — a
    file that is structurally valid, opens cleanly, and is silently empty.
    """
    by_depth = {
        round(sample.depth, _DEPTH_MATCH_DECIMALS): sample.value
        for sample in curve.samples
    }
    if len(by_depth) != len(curve.samples):
        raise ValueError(
            f"Curve {curve.mnemonic} has {len(curve.samples)} samples but only "
            f"{len(by_depth)} distinct depths; a LAS index cannot repeat a depth"
        )

    values = np.full(grid.size, CWLS_NULL_VALUE, dtype=float)
    landed = 0
    for position, depth in enumerate(grid):
        value = by_depth.get(round(float(depth), _DEPTH_MATCH_DECIMALS))
        if value is not None:
            values[position] = value
            landed += 1

    if landed != len(curve.samples):
        raise ValueError(
            f"Curve {curve.mnemonic} has {len(curve.samples)} samples but "
            f"{landed} of them fall on the file's depth index "
            f"({grid[0]} to {grid[-1]} every {grid[1] - grid[0]}). "
            "Curves must be resampled onto a common step before writing."
        )
    return values


def _write_well_section(
    las: lasio.LASFile, document: LasDocument, grid: np.ndarray
) -> None:
    """Populate ~WELL: the five mandatory items, then anything else carried.

    STRT and STOP are taken from the grid actually written rather than from the
    document's own figures, because the header's job is to describe the data
    section beneath it. If those ever disagreed, the header would be lying.
    """
    unit = document.depth_units
    las.well["STRT"] = lasio.HeaderItem(
        "STRT", unit=unit, value=float(grid[0]), descr=_STRT_DESCR
    )
    las.well["STOP"] = lasio.HeaderItem(
        "STOP", unit=unit, value=float(grid[-1]), descr=_STOP_DESCR
    )
    las.well["STEP"] = lasio.HeaderItem(
        "STEP", unit=unit, value=document.depth_step, descr=_STEP_DESCR
    )
    las.well["NULL"] = lasio.HeaderItem(
        "NULL", value=document.null_value, descr=_NULL_DESCR
    )
    las.well["WELL"] = lasio.HeaderItem(
        "WELL", value=document.well_name, descr=_WELL_DESCR
    )

    # Anything else the document carries — a logging date, a service company,
    # whatever a parsed source file recorded — is written back out. Dropping it
    # would make a read-then-write round trip quietly lossy.
    for mnemonic, value in document.metadata.items():
        if mnemonic in _AUTHORITATIVE_WELL_ITEMS:
            continue
        las.well[mnemonic] = lasio.HeaderItem(mnemonic, value=value)
