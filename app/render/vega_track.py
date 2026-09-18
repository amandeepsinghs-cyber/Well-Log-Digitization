"""Turn one log track into one layered Vega-Lite view.

In : a TrackPlot, the depth range to show, and how wide the track should be.
Out: a Vega-Lite layered view specification as a plain dict.
Rule: hand-rolled deliberately. Our three-track log layout is not a chart type
      any plotting library ships, so there is nothing to call here — but the
      output is validated by the real Vega-Lite compiler in scripts/compile_check.py
      rather than by eye.

Two Vega-Lite rules shape everything below, both learned the hard way because
both fail loudly only at compile time or, worse, silently at render time:

  1. EXACTLY ONE LAYER MAY DESCRIBE AN AXIS. If two layers in the same view each
     declare a depth axis, the spec does not parse at all. Every layer still
     needs the depth SCALE — that is what keeps the curves aligned — so the
     non-owning layers set "axis": null and keep their scale.

  2. A SELECTION PARAMETER BELONGS TO EXACTLY ONE LAYER. Declared at view or
     top level it propagates into every child and Vega rejects the duplicated
     signal name. One layer in the whole chart carries the zoom; the tracks
     follow it because they share the depth scale, not because they share the
     parameter.
"""

from __future__ import annotations

from typing import Any

try:
    from app.contracts import CurvePlot, ScaleType, TrackPlot
except ImportError:
    from contracts import CurvePlot, ScaleType, TrackPlot

# The name of the inline dataset every layer reads from. One shared table of
# depth-indexed rows, defined once at the top of the spec: a well log has a
# single depth index, so repeating it per curve would multiply the payload by
# the number of curves for no gain. Payload size is not cosmetic here — the
# A2UI inline data model truncates near 100 KB.
DATA_NAME: str = "log"

# The column holding the depth index in that dataset.
DEPTH_FIELD: str = "DEPTH"

# Vertical spacing between the stacked scale headers above a track, in pixels.
# A printed log puts each curve's scale on its own line above the track; this
# reproduces that, and 34 px is the smallest gap at which a two-line axis
# (title plus tick labels) does not collide with the one above it.
_AXIS_ROW_HEIGHT: int = 34

# Curves are drawn slightly heavier than a hairline so that the line itself is
# a large enough target to hover for a tooltip.
_STROKE_WIDTH: float = 1.5


def build_track_view(
    track: TrackPlot,
    depth_min: float,
    depth_max: float,
    depth_units: str,
    width: int,
    height: int,
    show_depth_axis: bool,
    zoom_param: str | None,
) -> dict[str, Any]:
    """Build the layered view for a single track.

    show_depth_axis is true for the leftmost track only. A printed log carries
    one depth column, not one per track, and repeating the numbers three times
    would cost horizontal space that the curves need.

    zoom_param names the selection this track's depth axis should be bound to,
    or None for every track that is not carrying it. Only one track in the
    whole chart may pass a name.
    """
    if not track.curves:
        raise ValueError(f"Track {track.number} has no curves to draw.")

    # Which layer owns the depth axis: the first one, when this track shows it
    # at all. Chosen by position rather than by curve because any layer can
    # draw it — they all share the same depth scale.
    depth_axis_owner = 0 if show_depth_axis else None

    # A track may carry curves on different scales — gamma ray 0-150 beside
    # spontaneous potential -80 to 20 — and each needs its own header. Curves
    # that share a scale exactly, as the three resistivity curves do, share one
    # header: drawing three identical logarithmic axes would just be clutter.
    headers = _assign_header_rows(track.curves)

    layers = [
        _curve_layer(
            curve=curve,
            depth_min=depth_min,
            depth_max=depth_max,
            depth_units=depth_units,
            header=headers[index],
            owns_depth_axis=index == depth_axis_owner,
            zoom_param=zoom_param if index == 0 else None,
        )
        for index, curve in enumerate(track.curves)
    ]

    return {
        "width": width,
        "height": height,
        "layer": layers,
        # Each curve keeps the scale printed on the original sheet, so the x
        # scales must NOT be unified across the layers of a track. The depth
        # SCALE is left shared — that is what keeps the curves on a track
        # reading at the same depth.
        #
        # Both AXES are resolved independently, and the depth axis has to be,
        # even though its scale is shared. Vega-Lite merges shared axis
        # definitions across layers, and merging a real axis with the null the
        # other layers declare yields null: the depth column silently vanishes
        # from the finished chart with no warning from the compiler. Resolving
        # independently lets each layer's own declaration stand, and since only
        # one layer declares a depth axis, exactly one is drawn.
        "resolve": {
            "scale": {"x": "independent"},
            "axis": {"x": "independent", "y": "independent"},
        },
    }


def _assign_header_rows(
    curves: tuple[CurvePlot, ...],
) -> list[tuple[int, str] | None]:
    """Work out which curve draws which scale header, and what it should say.

    The first curve on a given scale draws the header; later curves on the
    identical scale reuse it and draw nothing. Returns one entry per curve, in
    the same order: either (row, title) or None for "somebody else drew it".

    The title names EVERY curve sharing the scale. Track 2 carries deep, medium
    and shallow resistivity on one logarithmic 0.2-20 decade pair, and a header
    reading only "RXO" would credit all three traces to the shallow tool. A
    printed log labels that header for the measurement, not the first curve on
    it, and so does this.
    """
    # Group the curves by their exact scale, keeping first-seen order so the
    # headers stack in the order the curves are listed.
    groups: dict[tuple[str, float, float], list[CurvePlot]] = {}
    for curve in curves:
        scale = (curve.scale_type.value, curve.display_min, curve.display_max)
        groups.setdefault(scale, []).append(curve)

    rows = {scale: row for row, scale in enumerate(groups)}

    headers: list[tuple[int, str] | None] = []
    for curve in curves:
        scale = (curve.scale_type.value, curve.display_min, curve.display_max)
        members = groups[scale]
        if curve is not members[0]:
            headers.append(None)
            continue
        names = "  ·  ".join(member.mnemonic for member in members)
        headers.append((rows[scale], f"{names}  ({curve.unit})"))

    return headers


def _curve_layer(
    curve: CurvePlot,
    depth_min: float,
    depth_max: float,
    depth_units: str,
    header: tuple[int, str] | None,
    owns_depth_axis: bool,
    zoom_param: str | None,
) -> dict[str, Any]:
    """One curve: a clipped line, its own value scale, and a depth encoding."""
    layer: dict[str, Any] = {
        "data": {"name": DATA_NAME},
        "mark": {
            "type": "line",
            "stroke": curve.colour,
            "strokeWidth": _STROKE_WIDTH,
            # Without clipping, a curve keeps drawing outside the plotting area
            # once the reader zooms, overrunning the axes of the track beside it.
            "clip": True,
            # A log curve is sampled every half foot, far finer than the pixel
            # spacing on screen. Vega's default curve interpolation would round
            # off genuine sharp kicks, which in a gamma ray trace are bed
            # boundaries — the whole point of the measurement.
            "interpolate": "linear",
            **({"strokeDash": list(curve.dash)} if curve.dash else {}),
        },
        "encoding": {
            "x": _value_encoding(curve, header),
            "y": _depth_encoding(depth_min, depth_max, depth_units, owns_depth_axis),
            # A well log is a line plotted against the VERTICAL axis, which is
            # the reverse of almost every chart Vega-Lite is asked to draw. Left
            # alone it sorts the points of a line by the x channel, so the curve
            # is drawn in order of increasing reading rather than increasing
            # depth and comes out as a dense zigzag between the extremes of the
            # track. It compiles, it renders, and it is completely wrong. The
            # order channel names depth as the sequence the line follows.
            "order": {"field": DEPTH_FIELD, "type": "quantitative"},
            "tooltip": _tooltip_encoding(curve, depth_units),
        },
    }

    if zoom_param:
        # bind: "scales" turns the interval selection into pan and zoom on the
        # scales it covers. Restricted to the depth encoding so that dragging
        # never rescales a curve's calibrated value axis — a resistivity track
        # whose decades silently changed would be actively misleading.
        layer["params"] = [
            {
                "name": zoom_param,
                "select": {"type": "interval", "encodings": ["y"]},
                "bind": "scales",
            }
        ]

    return layer


def _value_encoding(
    curve: CurvePlot, header: tuple[int, str] | None
) -> dict[str, Any]:
    """The curve's own calibrated horizontal scale and, if it owns one, its header."""
    logarithmic = curve.scale_type is ScaleType.LOGARITHMIC

    encoding: dict[str, Any] = {
        "field": curve.mnemonic,
        "type": "quantitative",
        "scale": {
            "type": "log" if logarithmic else "linear",
            # The domain is the scale printed on the sheet, used verbatim. When
            # display_min exceeds display_max — as it does for neutron porosity
            # — that reverses the axis, which is the intended convention.
            "domain": [curve.display_min, curve.display_max],
            # Let the printed scale stand: "nice" would round the ends outward
            # and the plot would no longer match the paper it came from.
            "nice": False,
            "clamp": True,
        },
    }

    if header is None:
        # Another curve on this track already drew this exact scale.
        encoding["axis"] = None
        return encoding

    header_row, title = header
    encoding["axis"] = {
        # Scale headers sit above the track on a printed log, stacked upward in
        # the order the curves are listed.
        "orient": "top",
        "offset": header_row * _AXIS_ROW_HEIGHT,
        "title": title,
        "titleColor": curve.colour,
        "labelColor": curve.colour,
        "tickColor": curve.colour,
        "domainColor": curve.colour,
        "titleFontSize": 10,
        "labelFontSize": 9,
        # Two ticks on a two-decade resistivity scale would read 0.2 and 20 and
        # tell the reader nothing about the decade between them, so the log
        # axis is left to place its own.
        **({} if curve.scale_type is ScaleType.LOGARITHMIC else {"tickCount": 4}),
        "grid": True,
        "gridOpacity": 0.25,
    }
    return encoding


def _depth_encoding(
    depth_min: float,
    depth_max: float,
    depth_units: str,
    owns_axis: bool,
) -> dict[str, Any]:
    """The depth scale, identical on every layer of every track.

    Every layer carries the scale; at most one carries the axis. Dropping the
    scale from the non-owning layers would let them auto-fit to their own data
    and the curves would no longer line up at the same depth.
    """
    encoding: dict[str, Any] = {
        "field": DEPTH_FIELD,
        "type": "quantitative",
        "scale": {
            "domain": [depth_min, depth_max],
            # Depth increases downward on every well log ever printed.
            "reverse": True,
            "nice": False,
        },
    }

    if not owns_axis:
        encoding["axis"] = None
        return encoding

    encoding["axis"] = {
        "title": f"Depth ({depth_units})",
        "titleFontSize": 10,
        "labelFontSize": 9,
        "format": "d",
        "grid": True,
        "gridOpacity": 0.25,
    }
    return encoding


def _tooltip_encoding(curve: CurvePlot, depth_units: str) -> list[dict[str, Any]]:
    """Depth and reading, shown when the pointer is over this curve."""
    return [
        {
            "field": DEPTH_FIELD,
            "type": "quantitative",
            "title": f"Depth ({depth_units})",
            # Half a foot is the sampling interval, so one decimal is the
            # finest figure that carries information.
            "format": ".1f",
        },
        {
            "field": curve.mnemonic,
            "type": "quantitative",
            "title": f"{curve.description} ({curve.unit})",
            # Resistivity spans two decades, where a fixed number of decimal
            # places would print either 0.2 as 0 or 20 as 20.0000. Significant
            # figures read correctly at both ends.
            "format": ".3~g" if curve.scale_type is ScaleType.LOGARITHMIC else ".3f",
        },
    ]
