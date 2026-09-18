"""Put the log chart on a Gemini Enterprise A2UI surface.

In : a LogPlot.
Out: the ADK Parts that make a chart appear in chat — one Part per message.
Rule: hand-rolled deliberately. The A2UI envelope is specific to Gemini
      Enterprise, so there is no library to call; but every component built
      here is validated against the real catalog schema by
      tests/unit/test_a2ui_catalog_validation.py before anything is deployed.

THE SPEC CANNOT BE EMBEDDED IN THE COMPONENT. This is the constraint that
shapes the file and it is not obvious from the component's description. The
catalog types VegaChart.spec as a DynamicValue, and DynamicValue permits a
string, a number, a boolean, an array, a data binding or a function call — it
does NOT permit a JSON object. Writing the spec inline where it visibly belongs
produces a payload the renderer rejects. The spec therefore travels in its own
updateDataModel message and the component points at it by JSON Pointer, which
is also the better arrangement: a 70 KiB specification stays out of the
component tree entirely.

Three messages go out, in this order and as three separate Parts:

    createSurface     opens the surface and names the catalog
    updateDataModel   carries the Vega-Lite specification
    updateComponents  the component tree, pointing at that specification

The data model is sent BEFORE the components that read it. A component bound to
a path that does not exist yet renders as an empty box and reports nothing.
"""

from __future__ import annotations

import json
from typing import Any

from google.genai import types

try:
    from app.contracts import LogPlot
    from app.render.a2ui_envelope import wrap_a2ui_part
    from app.render.a2ui_lifecycle import (
        build_create_surface,
        build_update_components,
        build_update_data_model,
    )
    from app.render.vega_spec import build_log_spec, chart_height
except ImportError:
    from contracts import LogPlot
    from render.a2ui_envelope import wrap_a2ui_part
    from render.a2ui_lifecycle import (
        build_create_surface,
        build_update_components,
        build_update_data_model,
    )
    from render.vega_spec import build_log_spec, chart_height

# The key the specification is stored under in the surface's data model, and
# the JSON Pointer the chart component uses to reach it. Declared as one pair
# so the two can never disagree — a pointer to a missing path draws an empty
# box and logs nothing.
_SPEC_KEY: str = "spec"
_SPEC_POINTER: str = f"/{_SPEC_KEY}"

# A2UI v0.9 requires a component with the id "root" and the SDK requires it to
# be first in the list. Without it the payload validates, the renderer accepts
# it, and nothing is drawn, because there is no entry point into the tree.
_ROOT_ID: str = "root"
_COLUMN_ID: str = "log-column"
_TITLE_ID: str = "log-title"
_CAPTION_ID: str = "log-caption"
_CHART_ID: str = "log-chart"

# The Gemini Enterprise gateway rejects any single A2UI message larger than
# kMaxA2uiPayloadBytes = 512 * 1024, enforced in
# cloud/ai/agentis/gateway/agentspace/converters/a2ui_converters.{h:21,cc:139}.
# We check the specification alone, but what is weighed is the whole message,
# so a margin is held back for the envelope, the surface id and JSON escaping.
# Because Safe Vega cannot fetch a URL, the curve data has to travel inside the
# specification, so the limit is a real constraint rather than a formality.
_GE_PAYLOAD_CAP_BYTES: int = 512 * 1024
_ENVELOPE_MARGIN_BYTES: int = 64 * 1024
_PAYLOAD_LIMIT_BYTES: int = _GE_PAYLOAD_CAP_BYTES - _ENVELOPE_MARGIN_BYTES


def build_log_surface(plot: LogPlot, surface_id: str) -> list[types.Part]:
    """Build the three Parts that render a well log in Gemini Enterprise chat.

    surface_id must be new for this response. Reusing one that has already been
    created does not raise and does not render; the update is simply dropped.
    """
    spec = build_log_spec(plot)
    _check_payload_size(plot, spec)

    messages = [
        build_create_surface(surface_id=surface_id),
        build_update_data_model(surface_id=surface_id, value={_SPEC_KEY: spec}),
        build_update_components(
            surface_id=surface_id,
            components=build_log_components(plot),
        ),
    ]

    # One message per Part. Bundling them into a single Part as a JSON array is
    # accepted by the transport and then silently ignored by the renderer.
    return [wrap_a2ui_part(message) for message in messages]


def build_log_components(plot: LogPlot) -> list[dict[str, Any]]:
    """The component tree: a card holding a heading, a caption and the chart."""
    depth_min = min(plot.depths)
    depth_max = max(plot.depths)
    mnemonics = [curve.mnemonic for track in plot.tracks for curve in track.curves]

    return [
        # "root" leads the list because A2UI v0.9 requires exactly that.
        {"id": _ROOT_ID, "component": "Card", "child": _COLUMN_ID},
        {
            "id": _COLUMN_ID,
            "component": "Column",
            "children": [_TITLE_ID, _CAPTION_ID, _CHART_ID],
        },
        {
            "id": _TITLE_ID,
            "component": "Text",
            "text": plot.well_name,
            "variant": "h3",
        },
        {
            "id": _CAPTION_ID,
            "component": "Text",
            # States what the reader is looking at and where the numbers came
            # from. A digitised log is an interpretation of a scan, not a
            # recording, and the caption is where that is admitted.
            "text": (
                f"{len(plot.tracks)} tracks  ·  {', '.join(mnemonics)}  ·  "
                f"{depth_min:,.1f}–{depth_max:,.1f} {plot.depth_units}  ·  "  # noqa: RUF001 - an en dash is the correct glyph for a depth range
                f"{len(plot.depths):,} samples digitised from a scanned log"
            ),
            "variant": "caption",
        },
        {
            "id": _CHART_ID,
            "component": "VegaChart",
            # A JSON Pointer into the data model, not the specification itself:
            # the catalog's DynamicValue type has no object variant, so an
            # inline spec here fails validation outright.
            "spec": {"path": _SPEC_POINTER},
            "height": chart_height(plot),
        },
    ]


def _check_payload_size(plot: LogPlot, spec: dict[str, Any]) -> None:
    """Refuse to emit a chart the gateway would reject.

    Over the cap the gateway fails the message rather than trimming it, so the
    reader loses the whole surface and the agent's text answer with it. Failing
    here instead keeps the text answer and puts the measured size, the sample
    count and the curve count in the logs, which together say whether to shorten
    the interval or drop a curve.
    """
    size = len(json.dumps(spec, separators=(",", ":")).encode("utf-8"))
    if size > _PAYLOAD_LIMIT_BYTES:
        raise ValueError(
            f"The chart for {plot.well_name!r} serialises to {size:,} bytes, over "
            f"the {_PAYLOAD_LIMIT_BYTES:,} byte inline A2UI limit. It carries "
            f"{len(plot.depths):,} depth samples across "
            f"{sum(len(t.curves) for t in plot.tracks)} curves; render a shorter "
            f"depth interval or fewer curves."
        )
