"""Agent tools for the digitisation pipeline.

In : an object key in the agent's bucket.
Out: a plain-language report of what the pipeline found on that scan.
Rule: wiring only. Every decision belongs to a module in app/detect, app/header
      or app/calibrate; this file calls them in order and formats the answer.
      Nothing here may work out where a track is or what a scale says.
"""

from __future__ import annotations

import logging

from google.adk.tools import ToolContext

try:
    from app.calibrate.depth_axis import fit_depth_axis
    from app.calibrate.validate_calibration import (
        blocking_findings,
        validate_calibration,
    )
    from app.detect.depth_ticks import detect_depth_ticks
    from app.detect.gridlines import detect_gridlines
    from app.detect.regions import detect_regions
    from app.gcs.inventory import scan_bucket_inventory
    from app.gcs.paths import PDF_EXTENSION, gcs_uri, las_object_name
    from app.gcs.read_bytes import read_bytes
    from app.header.ocr_header import read_sheet_text
    from app.header.parse_header import (
        parse_depth_label,
        parse_depth_unit,
        parse_scales,
    )
    from app.ingest.load_image import load_image
    from app.integration import pipeline_render
    from app.integration.pipeline_digitise import digitise
except ImportError:
    from calibrate.depth_axis import fit_depth_axis
    from calibrate.validate_calibration import (
        blocking_findings,
        validate_calibration,
    )
    from detect.depth_ticks import detect_depth_ticks
    from detect.gridlines import detect_gridlines
    from detect.regions import detect_regions
    from gcs.inventory import scan_bucket_inventory
    from gcs.paths import PDF_EXTENSION, gcs_uri, las_object_name
    from gcs.read_bytes import read_bytes
    from header.ocr_header import read_sheet_text
    from header.parse_header import (
        parse_depth_label,
        parse_depth_unit,
        parse_scales,
    )
    from ingest.load_image import load_image
    from integration import pipeline_render
    from integration.pipeline_digitise import digitise

logger = logging.getLogger(__name__)

# The session-state key under which a pending chart request is left for the
# after-agent callback to pick up. A tool cannot attach a rendered surface to
# the response itself — only a callback runs late enough to do that — so the
# tool records WHICH well to draw and the callback draws it. Both
# digitise_scanned_log and render_well_log set it.
PENDING_CHART_KEY: str = "pending_chart_object"

# The same arrangement for the scanned sheet itself. Kept as a separate key
# rather than one key with a type tag, so that a turn which both digitises and
# is asked for the scan cannot end up with the two overwriting each other; the
# callback decides which takes precedence, and does so in one visible place.
PENDING_SCAN_KEY: str = "pending_scan_object"


def list_well_logs() -> dict:
    """List the well logs in the agent's bucket and say which are digitised.

    Answers questions of the form 'how many logs are still only scans?' or
    'what have we got?'. The inventory card attached to the reply shows the
    same files, but the card is drawn after the model has finished writing, so
    the model needs this tool to state a count it can stand behind.

    A scan counts as digitised when a LAS named after it already exists in the
    output prefix. That pairing is derived from the filename by the same rule
    the digitiser uses to name its output, so the two can never disagree.

    Returns:
        A dictionary of counts and per-file detail. On failure it returns
        {'ok': False, 'error': ...} rather than raising, because an ADK tool
        that raises loses the agent its turn.
    """
    inventory = scan_bucket_inventory()
    if not inventory.ok:
        logger.error("list_well_logs: FAILED - %s", inventory.error)
        return {
            "ok": False,
            "bucket": f"gs://{inventory.bucket}",
            "error": inventory.error or "inventory scan failed",
        }

    published = {las.name for las in inventory.las_files}
    scans = []
    for scan in inventory.scans:
        expected_las = las_object_name(scan.name)
        scans.append(
            {
                "object_name": scan.name,
                "format": _input_format(scan.name),
                "size_kib": round(scan.size_kib, 1),
                "digitised": expected_las in published,
                # Given even when it does not exist yet: it is the key the
                # digitiser will write to, so the agent can name the
                # destination when it asks permission to run.
                "las_object_name": expected_las,
            }
        )

    awaiting = [s for s in scans if not s["digitised"]]
    logger.info(
        "list_well_logs: OK - %d scan(s), %d digitised, %d awaiting",
        len(scans),
        len(scans) - len(awaiting),
        len(awaiting),
    )
    return {
        "ok": True,
        "bucket": f"gs://{inventory.bucket}",
        "scan_count": len(scans),
        "digitised_count": len(scans) - len(awaiting),
        "awaiting_digitisation": len(awaiting),
        "scans": scans,
        "las_files": [
            {"object_name": las.name, "size_kib": round(las.size_kib, 1)}
            for las in inventory.las_files
        ],
    }


def _input_format(object_name: str) -> str:
    """Name the input format of a scan, for a reader rather than a parser.

    Reported because 'PDF' and 'image' are the distinction the petrophysicist
    actually asks about, even though the pipeline treats them identically once
    the page has been rendered.
    """
    if object_name.lower().endswith(PDF_EXTENSION):
        return "PDF"
    extension = object_name.rsplit(".", 1)[-1].upper() if "." in object_name else ""
    return extension or "unknown"


def show_scanned_log(object_name: str, tool_context: ToolContext) -> dict:
    """Display the scanned sheet itself — the picture, not the digitised curves.

    Use this when the user asks to see, look at or be shown a scan. It shows
    the paper as it was filed, which is what someone checking the digitisation
    against the original needs. A scanned PDF displays as readily as an image.

    If the name given matches more than one scan, nothing is shown and the
    candidates are returned so the user can be asked which one they meant.
    Choosing on their behalf is worse than asking: the two files may be
    different runs of the same well.

    Args:
        object_name: the scan's key, for example
            'Scanned Well Logs/Well_log_schlum.jpg', or just the file name.
        tool_context: supplied by the ADK runtime.

    Returns:
        A dictionary saying what is being shown. On failure it returns
        {'ok': False, 'error': ...} rather than raising, because an ADK tool
        that raises loses the agent its turn.
    """
    inventory = scan_bucket_inventory()
    if not inventory.ok:
        logger.error("show_scanned_log: FAILED - %s", inventory.error)
        return {
            "ok": False,
            "requested": object_name,
            "error": inventory.error or "inventory scan failed",
        }

    candidates = _match_scans(object_name, [scan.name for scan in inventory.scans])
    if len(candidates) != 1:
        logger.info(
            "show_scanned_log: %s - %r matched %d of %d scan(s)",
            "AMBIGUOUS" if candidates else "NO MATCH",
            object_name,
            len(candidates),
            len(inventory.scans),
        )
        return {
            "ok": False,
            "requested": object_name,
            "error": (
                f"{object_name!r} matches {len(candidates)} scans; ask which one."
                if candidates
                else f"No scan in the bucket matches {object_name!r}."
            ),
            "candidates": candidates or [scan.name for scan in inventory.scans],
        }

    resolved = candidates[0]

    try:
        # Decoded here, rather than left entirely to the callback, so that an
        # unreadable scan fails while the model can still say so. The callback
        # reads it a second time to draw it: a RasterImage is a megabyte of raw
        # pixels and is not JSON-serialisable, so it cannot be carried in
        # session state, and a second read of a 50 KiB object is cheap.
        image = load_image(read_bytes(resolved))
    except Exception as exc:
        logger.error(
            "show_scanned_log: FAILED on %s, %s: %s",
            resolved,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return {
            "ok": False,
            "requested": object_name,
            "error": f"{type(exc).__name__}: {exc}",
        }

    tool_context.state[PENDING_SCAN_KEY] = resolved

    logger.info("show_scanned_log: OK - %s queued for display", resolved)
    return {
        "ok": True,
        "source": gcs_uri(resolved),
        "object_name": resolved,
        "format": _input_format(resolved),
        "pixel_size": [image.width, image.height],
        "image_attached": True,
        "ui_guidance": (
            "Image successfully queued for UI display by the renderer. "
            "Do NOT output image data, base64 strings, or UI markup in your prose. "
            "Simply confirm to the user in one sentence that the scan is displayed below."
        ),
    }


def _match_scans(requested: str, available: list[str]) -> list[str]:
    """Find the scans a loosely-typed name could mean.

    People refer to a scan by its file name, not its full key, and rarely match
    the case. An exact key wins outright — if the user gave the real key there
    is nothing to interpret. Failing that, the basename is matched
    case-insensitively, and then the extension is dropped, so that 'the
    Schlumberger scan' typed as 'Well_log_schlum' finds the JPEG.

    Returns every match rather than the best one, so the caller can tell one
    answer from several and ask instead of guessing.
    """
    if requested in available:
        return [requested]

    wanted = requested.strip().lower()
    by_basename = [
        name for name in available if name.rsplit("/", 1)[-1].lower() == wanted
    ]
    if by_basename:
        return by_basename

    return [
        name
        for name in available
        if name.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() == wanted
    ]


def inspect_scanned_log(object_name: str) -> dict:
    """Read a scanned well log and report its structure, scales and depth range.

    Everything reported is measured or read from the image itself. Nothing about
    this sheet's layout is built into the code, so the same call works on a
    different log with different tracks.

    Args:
        object_name: the object's key in the agent's bucket, for example
            'Scanned Well Logs/Well_log_schlum.jpg'.

    Returns:
        A dictionary describing the sheet: its tracks and their scales, the
        depth range and units, the achievable depth resolution, and any quality
        findings. On failure it returns {'ok': False, 'error': ...} rather than
        raising, because an ADK tool that raises loses the agent its turn.
    """
    try:
        image = load_image(read_bytes(object_name))

        # Geometry first: the rules on the page bound everything else.
        grid = detect_gridlines(image)
        layout = detect_regions(image, grid)
        ticks = detect_depth_ticks(image, layout, grid)

        # Then the one language-model call, and deterministic parsing of it.
        text = read_sheet_text(image, layout, ticks)
        axes = parse_scales(text, layout)
        depth_units = parse_depth_unit(text.depth_header)
        depths = tuple(parse_depth_label(label) for label in text.depth_labels)

        # Then the calibration and its verdict.
        depth = fit_depth_axis(ticks, depths, depth_units, layout)
        findings = validate_calibration(depth, grid)
    except Exception as exc:
        logger.error(
            "inspect_scanned_log: FAILED on %s, %s: %s",
            object_name,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return {
            "ok": False,
            "source": gcs_uri(object_name),
            "error": f"{type(exc).__name__}: {exc}",
        }

    blocking = blocking_findings(findings)
    logger.info(
        "inspect_scanned_log: OK - %s has %d track(s), %d curve(s), %.0f-%.0f %s",
        object_name,
        len(layout.tracks),
        len(axes),
        depth.depth_min,
        depth.depth_max,
        depth.depth_units,
    )
    return {
        "ok": True,
        "source": gcs_uri(object_name),
        "digitisable": not blocking,
        "track_count": len(layout.tracks),
        "curve_count": len(axes),
        "depth_range": [round(depth.depth_min, 1), round(depth.depth_max, 1)],
        "depth_units": depth.depth_units,
        # One pixel is the finest the paper distinguishes; a LAS step below this
        # would be inventing detail.
        "depth_resolution": round(depth.depth_per_pixel, 3),
        "depth_fit_error": round(depth.fit_rmse, 3),
        "tracks": [_describe_track(track, axes) for track in layout.tracks],
        "findings": [
            {"severity": f.severity.value, "curve": f.mnemonic, "message": f.message}
            for f in findings
        ],
    }


def _describe_track(track, axes) -> dict:
    """Summarise one track and the curves plotted on it."""
    on_this_track = [a for a in axes if a.track_bounds.track_name == track.name]
    return {
        "name": track.name,
        "pixel_columns": [track.x_left, track.x_right],
        # A track has one printed grid, so its curves share a scale type. Taken
        # from the first curve rather than assumed; parse_scales has already
        # refused the sheet if they disagreed.
        "scale_type": (
            on_this_track[0].scale_type.value if on_this_track else "UNKNOWN"
        ),
        "curves": [
            {
                "mnemonic": axis.mnemonic,
                "unit": axis.unit,
                "scale": [axis.value_min, axis.value_max],
            }
            for axis in on_this_track
        ],
    }


def digitise_scanned_log(object_name: str, tool_context: ToolContext) -> dict:
    """Digitise a scanned well log into a LAS file in the agent's bucket.

    Traces every curve off the image, converts the pixels to values using the
    scales printed on the sheet, and writes a CWLS LAS 2.0 file. The LAS is
    validated before it is published, so a returned output_las_uri always
    points at a file that passed. A scanned PDF is accepted as readily as an
    image; the page is rendered to pixels first.

    This WRITES to the bucket. Confirm with the user before calling it.

    On success the digitised log is queued for display, so the chart appears
    alongside this reply. Nobody asks for a log to be digitised and then wants
    to ask a second time to see it.

    Args:
        object_name: the scan's key in the agent's bucket, for example
            'Scanned Well Logs/Well_log_schlum.jpg'. Use inspect_scanned_log
            first if it is not yet known whether the sheet can be read.
        tool_context: supplied by the ADK runtime.

    Returns:
        A dictionary giving where the LAS was written, what is in it, and how
        much of each curve was actually observed rather than inferred. On
        failure it returns {'ok': False, 'error': ...} rather than raising,
        because an ADK tool that raises loses the agent its turn.
    """
    try:
        result = digitise(object_name)
    except Exception as exc:
        logger.error(
            "digitise_scanned_log: FAILED on %s, %s: %s",
            object_name,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return {
            "ok": False,
            "source": gcs_uri(object_name),
            "error": f"{type(exc).__name__}: {exc}",
        }

    document = result.las_document

    # Queue the chart. Derived from the source key by the same naming rule the
    # writer used, rather than parsed back out of output_las_uri, so the two
    # cannot drift apart.
    tool_context.state[PENDING_CHART_KEY] = las_object_name(object_name)

    logger.info(
        "digitise_scanned_log: OK - %s -> %s, queued for display",
        object_name,
        result.output_las_uri,
    )
    return {
        "ok": True,
        "source": result.source_gcs_uri,
        "output_las_uri": result.output_las_uri,
        "well_name": result.well_name,
        "depth_range": [round(d, 1) for d in result.depth_range],
        "depth_units": document.depth_units,
        "depth_step": document.depth_step,
        "curve_count": len(document.curves),
        # Rounded to a percentage point: the underlying figure is an estimate
        # of an estimate, and more digits would imply a precision it lacks.
        "overall_confidence": round(result.overall_confidence, 2),
        "chart_attached": True,
        "curves": [
            {
                "mnemonic": curve.mnemonic,
                "unit": curve.unit,
                "description": curve.description,
                # The share of depths carrying a real reading. The remainder is
                # NULL in the file, never an interpolated value, so a low figure
                # here means gaps to look at rather than numbers to distrust.
                "observed_fraction": round(curve.coverage_fraction, 3),
                "mean_confidence": round(curve.mean_confidence, 2),
            }
            for curve in document.curves
        ],
        "findings": [
            {"severity": f.severity.value, "curve": f.mnemonic, "message": f.message}
            for f in result.qc_findings
        ],
    }


def render_well_log(object_name: str, tool_context: ToolContext) -> dict:
    """Display a digitised well log as an interactive multi-track chart.

    Draws the three conventional tracks — lithology, resistivity and porosity —
    from a LAS file the agent has already written. The chart appears in the
    chat surface automatically; there is nothing for the caller to attach.

    Args:
        object_name: the well's name, for example 'WELL_LOG_SCHLUM', or the
            full object key 'Digitised Well Logs/WELL_LOG_SCHLUM.las'. Either
            form works.
        tool_context: supplied by the ADK runtime.

    Returns:
        A dictionary describing what is being shown. On failure it returns
        {'ok': False, 'error': ...} rather than raising, because an ADK tool
        that raises loses the agent its turn.
    """
    try:
        # The plot is built here, rather than left entirely to the callback, so
        # that a bad well name fails while the model can still say so. The
        # callback reads the file a second time to draw it: a LogPlot is not
        # JSON-serialisable and so cannot be carried in session state, and a
        # second read of a 55 KiB object is far cheaper than storing the 70 KiB
        # rendered specification in the session.
        plot = pipeline_render.render(object_name)
    except Exception as exc:
        logger.error(
            "render_well_log: FAILED on %s, %s: %s",
            object_name,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return {
            "ok": False,
            "requested": object_name,
            "error": f"{type(exc).__name__}: {exc}",
        }

    # Hand the resolved key to the callback. The resolved form is stored, not
    # what the user typed, so the callback never repeats the name resolution
    # and cannot resolve it differently.
    resolved = pipeline_render.resolve_las_object(object_name)
    tool_context.state[PENDING_CHART_KEY] = resolved

    logger.info("render_well_log: OK - %s queued for display", resolved)
    return {
        "ok": True,
        "source": gcs_uri(resolved),
        "well_name": plot.well_name,
        "depth_range": [round(min(plot.depths), 1), round(max(plot.depths), 1)],
        "depth_units": plot.depth_units,
        "sample_count": len(plot.depths),
        "tracks": [
            {
                "track": track.number,
                "curves": [curve.mnemonic for curve in track.curves],
                # Stated because it is the one thing a reader cannot infer from
                # the mnemonics alone, and reading a logarithmic track as
                # linear misestimates resistivity by an order of magnitude.
                "scale": track.curves[0].scale_type.value,
            }
            for track in plot.tracks
        ],
    }
