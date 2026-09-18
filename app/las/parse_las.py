"""Read a CWLS LAS 2.0 file into a LasDocument.

In : The raw bytes (or text) of a LAS 2.0 file, as returned by gcs/read_bytes.
Out: A LasDocument with one CurveTrace per data curve, depth-indexed.
Rule: Thin adapter only. lasio already solves LAS section parsing, wrapped data,
      malformed headers and the -999.25 NULL convention, so nothing here
      re-implements any of that. This file owns exactly one job: mapping lasio's
      objects onto our dataclasses.

      Mnemonics are carried through VERBATIM. Deciding what a curve means, which
      track it belongs to and whether its axis is logarithmic is the job of
      render/track_layout.py, not of the reader.
"""

from __future__ import annotations

import io
import math

import lasio
import numpy as np

try:
    from app.contracts import CWLS_NULL_VALUE, CurveSample, CurveTrace, LasDocument
except ImportError:
    from contracts import CWLS_NULL_VALUE, CurveSample, CurveTrace, LasDocument


# A value read out of a LAS file is not an estimate: it is exactly the number the
# file states. Confidence therefore only becomes meaningful later, when curves
# are traced off a scanned image. Reading records full confidence so that a
# digitised curve with genuine uncertainty is visibly different from a parsed one.
_PARSED_CONFIDENCE = 1.0

# LAS 2.0 is specified as ASCII, but files exported by older vendor software
# routinely carry Latin-1 characters in comments and company names. Latin-1
# decodes any byte sequence without error, so it is the safe second attempt.
_ENCODINGS = ("utf-8", "latin-1")


def parse_las(raw: bytes | str) -> LasDocument:
    """Parse LAS bytes into a LasDocument, preserving nulls as gaps."""
    text = decode_las_text(raw) if isinstance(raw, bytes) else raw

    # lasio reads from any file-like object, which lets us stay in memory and
    # never touch the local filesystem inside the agent runtime.
    las = lasio.read(io.StringIO(text))

    # The LAS 2.0 standard fixes the first curve as the depth index. lasio
    # exposes it as .index, so there is no mnemonic guessing to do here.
    depth = np.asarray(las.index, dtype=float)
    if depth.size < 2:
        raise ValueError(
            f"LAS has {depth.size} depth sample(s); at least 2 are needed to "
            "establish a depth step and plot a curve"
        )

    # Everything after the index is a measured curve.
    data_curves = list(las.curves)[1:]
    if not data_curves:
        raise ValueError(
            "LAS contains a depth index but no measured curves, so there is "
            "nothing to plot or validate"
        )

    curves = [_to_trace(curve, depth) for curve in data_curves]

    return LasDocument(
        well_name=_header_value(las, "WELL") or "UNKNOWN",
        depth_min=float(depth.min()),
        depth_max=float(depth.max()),
        depth_step=_depth_step(las, depth),
        depth_units=_depth_units(las),
        null_value=CWLS_NULL_VALUE,
        curves=curves,
        metadata=_metadata(las),
        provenance_comments=_provenance(las),
    )


def decode_las_text(raw: bytes) -> str:
    """Decode LAS bytes, tolerating the Latin-1 text vendors leave in headers.

    Shared with las/validate_las.py so both read the same bytes the same way. A
    validator that decoded differently from the parser could pass a file the
    parser then chokes on.
    """
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    # Unreachable in practice because latin-1 accepts every byte, but a silent
    # empty string here would surface as "no curves" and send us hunting in the
    # wrong place.
    raise ValueError(f"Could not decode {len(raw)} bytes of LAS as {_ENCODINGS}")


def _to_trace(curve: lasio.CurveItem, depth: np.ndarray) -> CurveTrace:
    """Convert one lasio curve into a depth-aligned CurveTrace.

    lasio converts the file's NULL sentinel into NaN. We convert it back to
    -999.25 because that is what CurveTrace.coverage_fraction counts and what a
    LAS writer must emit. A gap stays a gap: it is never interpolated across.
    """
    values = np.asarray(curve.data, dtype=float)

    samples = [
        CurveSample(
            depth=float(d),
            value=CWLS_NULL_VALUE if math.isnan(v) else float(v),
            # A null carries no reading, so it carries no confidence either.
            confidence=0.0 if math.isnan(v) else _PARSED_CONFIDENCE,
        )
        for d, v in zip(depth, values, strict=True)
    ]

    return CurveTrace(
        mnemonic=curve.mnemonic,
        unit=curve.unit or "",
        description=curve.descr or "",
        samples=samples,
    )


def _depth_step(las: lasio.LASFile, depth: np.ndarray) -> float:
    """Depth increment per sample, taken from the header or measured.

    ~WELL STEP is the authoritative value, but it is written as 0 whenever the
    log is irregularly sampled, and some exports omit it entirely. In those
    cases the median spacing of the actual index is the honest answer, and it is
    measured rather than assumed.
    """
    declared = _header_value(las, "STEP")
    if declared not in (None, ""):
        step = float(declared)
        if step != 0.0:
            return step

    return float(np.median(np.diff(depth)))


def _depth_units(las: lasio.LASFile) -> str:
    """Depth unit normalised to the 'FT' / 'M' the LasDocument contract declares.

    LAS files write the index unit as any of F, FT, ft, FEET, M, m, METRES.
    Normalising on the first letter is safe because those are the only two
    depth units the standard permits.
    """
    raw_unit = (las.curves[0].unit or "").strip().upper()
    if raw_unit.startswith("F"):
        return "FT"
    if raw_unit.startswith("M"):
        return "M"
    raise ValueError(
        f"Depth index has unrecognised unit {raw_unit!r}; LAS 2.0 permits only "
        "feet or metres, and guessing would mis-scale every depth"
    )


def _header_value(las: lasio.LASFile, mnemonic: str) -> str | None:
    """Read one ~WELL header item, or None if the file omits it.

    Membership, not lasio's SectionItems.get(): that method fabricates an empty
    HeaderItem for a missing mnemonic rather than returning None, so asking it
    for something absent quietly succeeds.
    """
    if mnemonic not in las.well:
        return None
    item = las.well[mnemonic]
    if item.value in (None, ""):
        return None
    return str(item.value).strip()


def _metadata(las: lasio.LASFile) -> dict[str, str]:
    """Flatten the ~WELL and ~PARAMETER sections into plain strings.

    Kept verbatim so that anything the original file recorded about the well
    survives a round trip, even fields this pipeline has no opinion about.
    """
    flat: dict[str, str] = {}
    for section in (las.well, las.params):
        for item in section:
            if item.value not in (None, ""):
                flat[item.mnemonic] = str(item.value).strip()
    return flat


def _provenance(las: lasio.LASFile) -> list[str]:
    """Lines from the ~OTHER section, which is where provenance is stamped.

    This is how a synthetic or machine-digitised LAS declares itself. Losing it
    would let a digitised curve be mistaken for a recorded measurement.
    """
    other = (las.other or "").strip()
    return [line.strip() for line in other.splitlines() if line.strip()]
