"""Digitise a scanned well log sheet into a LAS file in the agent's bucket.

In : the object key of a raster scan in the agent's GCS bucket.
Out: a DigitisationResult — the LAS document, where it was written, and every
     quality finding raised on the way.
Rule: wiring only. Every decision belongs to a module in app/detect, app/header,
      app/calibrate, app/extract or app/las; this file calls them in order.
      Nothing here may decide where a track is, which pixel is which curve, or
      what a number means.

      The one thing this file does own is ORDER, and the order is load-bearing
      at three points, each marked below.
"""

from __future__ import annotations

import logging
from datetime import date

try:
    from app.calibrate.depth_axis import depth_at_row, fit_depth_axis
    from app.calibrate.validate_calibration import (
        blocking_findings,
        validate_calibration,
    )
    from app.calibrate.value_axis import pixel_to_value
    from app.contracts import (
        CWLS_NULL_VALUE,
        CurveSample,
        CurveTrace,
        DigitisationResult,
        FindingSeverity,
        LasDocument,
    )
    from app.detect.depth_ticks import detect_depth_ticks
    from app.detect.gridlines import detect_gridlines
    from app.detect.regions import detect_regions
    from app.extract.clean import despike, resample, sampling_grid
    from app.extract.separate import separate_curves
    from app.extract.trace import trace_curve
    from app.gcs.paths import gcs_uri, las_object_name, well_name_from
    from app.gcs.read_bytes import read_bytes
    from app.gcs.write_bytes import write_bytes
    from app.header.ocr_header import read_sheet_text
    from app.header.parse_header import (
        parse_depth_label,
        parse_depth_unit,
        parse_scales,
    )
    from app.ingest.load_image import load_image, to_array
    from app.las.mnemonics import SPWLA_MNEMONICS
    from app.las.validate_las import validate_las
    from app.las.write_las import write_las
    from app.preprocess.remove_annotations import remove_annotations
    from app.preprocess.remove_grid import remove_grid
except ImportError:
    from calibrate.depth_axis import depth_at_row, fit_depth_axis
    from calibrate.validate_calibration import (
        blocking_findings,
        validate_calibration,
    )
    from calibrate.value_axis import pixel_to_value
    from contracts import (
        CWLS_NULL_VALUE,
        CurveSample,
        CurveTrace,
        DigitisationResult,
        FindingSeverity,
        LasDocument,
    )
    from detect.depth_ticks import detect_depth_ticks
    from detect.gridlines import detect_gridlines
    from detect.regions import detect_regions
    from extract.clean import despike, resample, sampling_grid
    from extract.separate import separate_curves
    from extract.trace import trace_curve
    from gcs.paths import gcs_uri, las_object_name, well_name_from
    from gcs.read_bytes import read_bytes
    from gcs.write_bytes import write_bytes
    from header.ocr_header import read_sheet_text
    from header.parse_header import (
        parse_depth_label,
        parse_depth_unit,
        parse_scales,
    )
    from ingest.load_image import load_image, to_array
    from las.mnemonics import SPWLA_MNEMONICS
    from las.validate_las import validate_las
    from las.write_las import write_las
    from preprocess.remove_annotations import remove_annotations
    from preprocess.remove_grid import remove_grid

logger = logging.getLogger(__name__)

# The heavy rule dividing two tracks is several pixels thick and is ink, so
# cropping right up to it hands the rule itself to the separator as though it
# were a curve. Three pixels clears it on the reference scan.
_TRACK_INSET_PX = 3

# There is no registered MIME type for LAS. It is a plain-text format, and
# saying so is what lets a petrophysicist open one in the GCS console instead of
# downloading it.
_LAS_CONTENT_TYPE = "text/plain"


def digitise(object_name: str) -> DigitisationResult:
    """Turn one scanned log sheet into a LAS file in the bucket.

    Args:
        object_name: the scan's key in the agent's bucket, for example
            'Scanned Well Logs/Well_log_schlum.jpg'.

    Returns:
        A DigitisationResult carrying the LAS document, the URI it was written
        to, and every QC finding raised by the calibration and by the LAS
        validator.

    Raises:
        ValueError: if the sheet cannot be calibrated, or if the LAS produced
            fails structural validation. Both are refusals to publish, not
            crashes — see the comments at each.
        google.api_core.exceptions.Forbidden: if the bucket rejects the read or
            the write.
    """
    image = load_image(read_bytes(object_name))

    # ORDER 1: geometry, then the single language-model call, then calibration.
    # The model is shown crops the geometry located, so it cannot run first.
    grid = detect_gridlines(image)
    layout = detect_regions(image, grid)
    ticks = detect_depth_ticks(image, layout, grid)

    text = read_sheet_text(image, layout, ticks)
    axes = parse_scales(text, layout)
    depth_units = parse_depth_unit(text.depth_header)
    depth_labels = tuple(parse_depth_label(label) for label in text.depth_labels)
    depth = fit_depth_axis(ticks, depth_labels, depth_units, layout)

    calibration_findings = validate_calibration(depth, grid)
    blocking = blocking_findings(calibration_findings)
    if blocking:
        # Every value in the file would be positioned by this calibration. A
        # LAS built on one known to be wrong is worse than no LAS at all,
        # because it looks exactly like a good one.
        raise ValueError(
            f"Refusing to digitise {object_name}: the depth calibration is not "
            f"usable. {'; '.join(finding.message for finding in blocking)}"
        )

    curves = extract_curves(image, layout, grid, depth, axes)

    document = _assemble(object_name, depth, curves)
    data = write_las(document)

    # ORDER 2: validate the bytes BEFORE publishing them. The output prefix is
    # read by other agents, so a LAS that lands there is a published artefact;
    # withdrawing one is far harder than never writing it.
    las_findings = validate_las(data)
    errors = [f for f in las_findings if f.severity is FindingSeverity.ERROR]
    if errors:
        raise ValueError(
            f"Refusing to publish a LAS for {object_name} that fails its own "
            f"validator: {'; '.join(finding.message for finding in errors)}"
        )

    output_uri = write_bytes(las_object_name(object_name), data, _LAS_CONTENT_TYPE)

    # validate_calibration returns a tuple and validate_las a list, so both are
    # coerced rather than concatenated as they come.
    findings = list(calibration_findings) + list(las_findings)
    result = DigitisationResult(
        source_gcs_uri=gcs_uri(object_name),
        output_las_uri=output_uri,
        well_name=document.well_name,
        depth_range=(document.depth_min, document.depth_max),
        las_document=document,
        qc_findings=findings,
        overall_confidence=_overall_confidence(curves),
    )

    logger.info(
        "digitise: OK - %s -> %s, %d curve(s), %.1f-%.1f %s, confidence %.2f",
        object_name,
        output_uri,
        len(document.curves),
        document.depth_min,
        document.depth_max,
        document.depth_units,
        result.overall_confidence,
    )
    return result


def extract_curves(image, layout, grid, depth, axes) -> dict[str, CurveTrace]:
    """Trace every curve on the sheet and return them cleaned and resampled.

    Separated from digitise() so that scripts/qc_plot.py can render exactly
    what the agent writes. A QC plot generated by a second, parallel copy of
    this wiring would be evidence about the copy, not about the product.
    """
    # ORDER 3: annotations before the grid. Whitening the grid rows first cuts
    # every colour fill into horizontal bands and leaves each band's rim behind,
    # which the separator then reads as curve ink.
    cleaned = to_array(remove_grid(remove_annotations(image), grid))

    step, max_gap = sampling_grid(depth.depth_per_pixel)

    curves: dict[str, CurveTrace] = {}
    for track in layout.tracks:
        on_track = [axis for axis in axes if axis.track_bounds.track_name == track.name]
        if not on_track:
            continue

        left = track.x_left + _TRACK_INSET_PX
        right = track.x_right - _TRACK_INSET_PX + 1
        crop = cleaned[layout.data_top + 1 : layout.data_bottom, left:right]

        specs = [SPWLA_MNEMONICS[axis.mnemonic] for axis in on_track]
        affinities = separate_curves(crop, specs)

        for axis in on_track:
            # Despike in pixels, calibrate, then resample in depth. Forced: a
            # spike is a tracing error and only looks like one while the curve
            # is still measured in pixels.
            path = despike(trace_curve(affinities[axis.mnemonic]))
            traced = _calibrate(path, axis, depth, layout.data_top + 1, left)
            curves[axis.mnemonic] = resample(traced, step, max_gap)

    return curves


def _calibrate(path, axis, depth, first_row: int, crop_left: int) -> CurveTrace:
    """Apply the depth and value calibrations to a path of pixel columns.

    The path's columns are measured from the left edge of the cropped track,
    whereas the value calibration is stated in whole-image columns, so the crop
    offset has to be added back before converting.
    """
    spec = SPWLA_MNEMONICS[axis.mnemonic]
    samples = [
        CurveSample(
            depth=depth_at_row(depth, first_row + index),
            # Zero confidence means the curve was not visible on this row and
            # the path was carried across on the continuity prior alone. That
            # is not a reading and must not be written as one.
            value=(
                CWLS_NULL_VALUE
                if confidence == 0.0
                else pixel_to_value(crop_left + column, axis)
            ),
            confidence=confidence,
        )
        for index, (column, confidence) in enumerate(
            zip(path.columns, path.confidence, strict=True)
        )
    ]
    return CurveTrace(
        mnemonic=axis.mnemonic,
        unit=axis.unit,
        description=spec.description,
        samples=samples,
    )


def _assemble(
    object_name: str, depth, curves: dict[str, CurveTrace]
) -> LasDocument:
    """Gather the traced curves into a LAS document with its provenance."""
    ordered = list(curves.values())
    if not ordered:
        raise ValueError(
            f"No curves could be traced from {object_name}; there is nothing to "
            "write. The sheet was calibrated, so this is a separation or "
            "tracing failure rather than a geometry one."
        )

    # The written depth frame spans every curve. Individual curves start and
    # end where they were first and last seen, and are NULL outside that.
    all_depths = [sample.depth for curve in ordered for sample in curve.samples]
    step, _ = sampling_grid(depth.depth_per_pixel)

    return LasDocument(
        well_name=well_name_from(object_name),
        depth_min=min(all_depths),
        depth_max=max(all_depths),
        depth_step=step,
        depth_units=depth.depth_units,
        curves=ordered,
        # DATE is a standard ~WELL item, and for a digitised log the honest
        # value is when it was digitised — not when it was logged, which the
        # sheet does not state.
        metadata={"DATE": date.today().isoformat()},
        provenance_comments=_provenance(object_name, depth, ordered),
    )


def _provenance(object_name: str, depth, curves: list[CurveTrace]) -> list[str]:
    """State how this file came to exist, in the ~OTHER section.

    Without this a curve traced off a photograph is indistinguishable from a
    recorded measurement, and every number here carries uncertainty that a real
    logging run does not.
    """
    lines = [
        "DIGITISED LOG - values were traced from a raster image of a printed",
        "log sheet. They are estimates read off paper, NOT recorded tool",
        "measurements, and must not be used as such without review.",
        f"Source image: {gcs_uri(object_name)}",
        f"Digitised on: {date.today().isoformat()}",
        # The reference sheet prints no well name, and most printed figures do
        # not. Saying where the name came from stops it being read as authority.
        "Well name derived from the source filename; the sheet prints none.",
        f"Depth calibration: {depth.depth_per_pixel:.5f} {depth.depth_units} per "
        f"pixel, fit RMSE {depth.fit_rmse:.3f} {depth.depth_units}.",
        "Per-curve coverage and mean tracing confidence:",
    ]
    lines += [
        f"  {curve.mnemonic:<6s} {curve.coverage_fraction:6.1%} observed, "
        f"confidence {curve.mean_confidence:.2f}"
        for curve in curves
    ]
    return lines


def _overall_confidence(curves: dict[str, CurveTrace]) -> float:
    """One number for how much of this sheet was read, and how clearly.

    Coverage multiplied by confidence, averaged over the curves. Both factors
    are needed: a curve traced crisply for a third of the log is not a good
    result, and neither is one guessed weakly the whole way down.
    """
    if not curves:
        return 0.0
    return sum(
        curve.coverage_fraction * curve.mean_confidence for curve in curves.values()
    ) / len(curves)
