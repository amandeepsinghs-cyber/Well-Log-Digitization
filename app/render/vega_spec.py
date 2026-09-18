"""Assemble the track views into one multi-track Vega-Lite log display.

In : a LogPlot from track_layout.
Out: a complete Vega-Lite v5 specification as a plain dict, data included.
Rule: hand-rolled deliberately, and validated by the real Vega-Lite compiler in
      scripts/compile_check.py — never by reading it and assuming it parses.

Four constraints decide the shape of this file. Each of them was established
against the actual Vega-Lite engine and the actual Gemini Enterprise renderer,
and three of the four fail silently rather than loudly:

  1. hconcat IS THE ONLY OPTION. The obvious way to repeat a panel is `facet`,
     but facet forces one x-scale TYPE across every panel. Track 2 is
     logarithmic and tracks 1 and 3 are linear, so facet cannot express a well
     log at all. hconcat keeps each track's scales its own.

  2. EVERY CONCAT CHILD NEEDS AN EXPLICIT NUMERIC WIDTH. Gemini Enterprise
     rewrites the top-level width and height to 'container' before handing the
     spec to Vega-Lite, and Vega-Lite rejects 'container' sizing on a concat
     view. Fixed child widths mean the rewrite has nothing to act on.

  3. DATA SHIPS INLINE. Safe Vega disables the URL loader and the page's
     content security policy blocks the fetch, so `data.url` is unavailable.
     Everything travels in the spec, which puts payload size on the critical
     path: the inline data model truncates near 100 KB.

  4. THE TRACKS MUST SCROLL TOGETHER. resolve.scale.y = 'shared' gives all
     three tracks one depth scale, so the zoom parameter attached to a single
     layer in a single track moves all of them.
"""

from __future__ import annotations

from typing import Any

try:
    from app.contracts import CurvePlot, LogPlot
    from app.render.vega_track import DATA_NAME, DEPTH_FIELD, build_track_view
except ImportError:
    from contracts import CurvePlot, LogPlot
    from render.vega_track import DATA_NAME, DEPTH_FIELD, build_track_view

VEGA_LITE_SCHEMA: str = "https://vega.github.io/schema/vega-lite/v5.json"

# The single zoom selection for the whole chart. It lives on one layer of the
# first track; the other tracks follow it through the shared depth scale.
ZOOM_PARAM: str = "depthZoom"

# Track geometry in pixels. A well log is read as a tall narrow strip, and
# these are sized for the Gemini Enterprise chat panel rather than a full
# screen: three 200 px tracks plus the depth axis fit without horizontal
# scrolling at the default chat width.
TRACK_WIDTH: int = 200
TRACK_HEIGHT: int = 560

# Tracks butt up against each other on a printed log. A few pixels of gap keeps
# the axis rules from merging into one thick line while still reading as one
# continuous depth strip.
_TRACK_SPACING: int = 6

# Room above the tracks for the stacked scale headers. Vega-Lite does not
# reserve space for an axis it has been told to offset, so the tallest stack of
# headers on any track decides the padding the whole chart needs.
_HEADER_ROW_PADDING: int = 34
_BASE_TOP_PADDING: int = 12
_BOTTOM_PADDING: int = 8

# The chart's own title block — well name over a line of provenance — and the
# depth tick label that sits below the deepest sample. Both are measured from
# the rendered PNG that scripts/compile_check.py produces, and both have to be
# counted because the host reserves a fixed pixel height for the whole chart
# and silently crops anything past it.
_TITLE_HEIGHT: int = 44
_DEPTH_LABEL_HEIGHT: int = 16

# Readings are carried to four decimal places, which is what the LAS itself
# holds: neutron porosity is recorded in volume fraction, where one pixel on
# the source scan is about 0.001. Rounding further would discard real
# precision; carrying more would only pad the payload with float noise.
_VALUE_DECIMALS: int = 4

# Depth is written to the same precision for the same reason: a half-foot
# sampling interval needs one decimal, and the fourth guards against a step
# that is not a round number.
_DEPTH_DECIMALS: int = 4


def build_log_spec(plot: LogPlot) -> dict[str, Any]:
    """Build the complete Vega-Lite specification for a multi-track log."""
    if not plot.depths:
        raise ValueError(f"{plot.well_name!r} has an empty depth index.")

    depth_min = min(plot.depths)
    depth_max = max(plot.depths)

    tracks = [
        build_track_view(
            track=track,
            depth_min=depth_min,
            depth_max=depth_max,
            depth_units=plot.depth_units,
            width=TRACK_WIDTH,
            height=TRACK_HEIGHT,
            # Only the leftmost track prints the depth column, exactly as a
            # paper log does.
            show_depth_axis=index == 0,
            # The zoom parameter exists once in the whole specification.
            # Repeating it per track would duplicate the Vega signal name and
            # the spec would not parse.
            zoom_param=ZOOM_PARAM if index == 0 else None,
        )
        for index, track in enumerate(plot.tracks)
    ]

    return {
        "$schema": VEGA_LITE_SCHEMA,
        "title": {
            "text": plot.well_name,
            "subtitle": (
                f"{depth_min:,.1f} – {depth_max:,.1f} {plot.depth_units}  ·  "  # noqa: RUF001 - an en dash is the correct glyph for a depth range in a chart title
                f"{len(plot.depths):,} samples  ·  digitised from a scanned log"
            ),
            "fontSize": 13,
            "subtitleFontSize": 10,
            "anchor": "start",
        },
        "datasets": {DATA_NAME: _rows(plot)},
        "hconcat": tracks,
        # One depth scale across all tracks. This is what makes the single zoom
        # parameter move every track together, and it is also what guarantees a
        # horizontal line across the chart is one depth.
        "resolve": {"scale": {"y": "shared"}},
        "spacing": _TRACK_SPACING,
        "padding": {
            "top": _top_padding(plot),
            "left": 8,
            "right": 8,
            "bottom": _BOTTOM_PADDING,
        },
        "config": {
            "view": {"stroke": "#B0B0B0"},
            "axis": {"labelOverlap": "greedy"},
        },
    }


def chart_height(plot: LogPlot) -> int:
    """Total pixel height the finished chart occupies, headers and title included.

    The A2UI chart component defaults to 290 pixels and crops whatever does not
    fit, with no scrollbar and no warning — a 560 pixel track would lose its
    bottom two thirds. The host has to be told the real height, and only this
    module knows it, because only this module knows how many scale headers are
    stacked above the tracks.
    """
    return (
        _top_padding(plot)
        + _TITLE_HEIGHT
        + TRACK_HEIGHT
        + _DEPTH_LABEL_HEIGHT
        + _BOTTOM_PADDING
    )


def _top_padding(plot: LogPlot) -> int:
    """Room above the tracks for the tallest stack of scale headers."""
    return _BASE_TOP_PADDING + _header_rows(plot) * _HEADER_ROW_PADDING


def _rows(plot: LogPlot) -> list[dict[str, float | None]]:
    """The inline dataset: one record per depth, one column per curve.

    A record-per-depth table is the compact form. The alternative, one table
    per curve, would repeat the depth index once for every curve in the file —
    seven times over on this log — and the inline data model has a hard size
    limit that the curves themselves are already a fair fraction of.
    """
    rows: list[dict[str, float | None]] = [
        {DEPTH_FIELD: round(depth, _DEPTH_DECIMALS)} for depth in plot.depths
    ]

    for track in plot.tracks:
        for curve in track.curves:
            # strict: a curve shorter than the depth index would otherwise be
            # truncated here without complaint, and the chart would simply stop
            # drawing that curve partway down the well.
            for row, value in zip(rows, curve.values, strict=True):
                # None stays None so it serialises as JSON null, which breaks
                # the line. Rounding None would raise.
                row[curve.mnemonic] = (
                    None if value is None else round(value, _VALUE_DECIMALS)
                )

    return rows


def _header_rows(plot: LogPlot) -> int:
    """How many stacked scale headers the busiest track carries.

    The chart's top padding has to clear the tallest stack, not the average
    one, or the topmost scale on the busiest track is cropped off.
    """
    return max(_distinct_scales(track.curves) for track in plot.tracks)


def _distinct_scales(curves: tuple[CurvePlot, ...]) -> int:
    """Count the separate scale headers a track needs.

    Mirrors the grouping in vega_track._assign_header_rows: curves sharing an
    identical scale, as the three resistivity curves do, share one header.
    """
    return len(
        {(curve.scale_type.value, curve.display_min, curve.display_max) for curve in curves}
    )
