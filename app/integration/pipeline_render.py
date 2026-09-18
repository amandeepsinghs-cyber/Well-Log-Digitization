"""Compose the render path: a LAS file in Cloud Storage to a drawable log.

In : an object key, or the bare name of a well the agent has already digitised.
Out: a LogPlot — the curves grouped onto tracks, ready to be turned into a spec.
Rule: wiring only. Every decision belongs to a module in app/las or app/render;
      this file reads the bytes and calls them in order.
"""

from __future__ import annotations

import logging

try:
    from app.contracts import LogPlot
    from app.gcs.paths import LAS_EXTENSION, LAS_OUTPUT_PREFIX
    from app.gcs.read_bytes import read_bytes
    from app.las.parse_las import parse_las
    from app.render.track_layout import build_log_plot
except ImportError:
    from contracts import LogPlot
    from gcs.paths import LAS_EXTENSION, LAS_OUTPUT_PREFIX
    from gcs.read_bytes import read_bytes
    from las.parse_las import parse_las
    from render.track_layout import build_log_plot

logger = logging.getLogger(__name__)


def render(object_name: str) -> LogPlot:
    """Read a digitised LAS out of the bucket and lay its curves onto tracks."""
    resolved = resolve_las_object(object_name)

    raw = read_bytes(resolved)
    document = parse_las(raw)
    plot = build_log_plot(document)

    logger.info(
        "render: object=%s well=%s tracks=%d curves=%d samples=%d",
        resolved,
        plot.well_name,
        len(plot.tracks),
        sum(len(track.curves) for track in plot.tracks),
        len(plot.depths),
    )
    return plot


def resolve_las_object(object_name: str) -> str:
    """Accept either a full object key or a bare well name.

    A user asks to see "WELL_LOG_SCHLUM", not
    "Digitised Well Logs/WELL_LOG_SCHLUM.las", and the model relaying that
    request will pass on whichever form it was given. Both resolve to the same
    object here rather than failing with a not-found the user cannot act on.
    """
    if not object_name or not object_name.strip():
        raise ValueError("No well or object name was given to render.")

    name = object_name.strip()
    if name.lower().endswith(LAS_EXTENSION):
        # Already an object key, unless the prefix is missing.
        return name if "/" in name else f"{LAS_OUTPUT_PREFIX}{name}"

    return f"{LAS_OUTPUT_PREFIX}{name}{LAS_EXTENSION}"
