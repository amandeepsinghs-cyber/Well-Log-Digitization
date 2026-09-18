"""Put the scanned sheet itself on a Gemini Enterprise A2UI surface.

In : a RasterImage — the scan as the pipeline sees it, whatever it arrived as.
Out: the ADK Parts that make the picture appear in chat.
Rule: hand-rolled for the same reason as a2ui_emit: the A2UI envelope is
      specific to Gemini Enterprise and there is no library to call. Every
      component built here is validated against the real catalog schema by
      tests/unit/test_a2ui_catalog_validation.py before anything is deployed.

THE PIXELS TRAVEL INSIDE THE MESSAGE, as a base64 data: URI. The alternative is
a signed URL, and the difference is not which one the agent can read — the
service account reads the bucket either way — but whether the READER'S BROWSER
also needs access to Cloud Storage. With a data URI it does not, and the agent
needs no token-signing permission on itself. The cost is size, and size is
budgeted below.

Whatever the scan was filed as, it is re-encoded here as JPEG from the decoded
raster. That is not wasted work: it means a scanned PDF displays as readily as a
JPEG without the browser being asked to render a PDF inside an Image component,
and it puts the size of the result under our control rather than the uploader's.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import cv2
import numpy as np
from google.genai import types

try:
    from app.contracts import RasterImage
    from app.ingest.load_image import to_array
    from app.render.a2ui_envelope import wrap_a2ui_part
    from app.render.a2ui_lifecycle import (
        build_create_surface,
        build_update_components,
    )
except ImportError:
    from contracts import RasterImage
    from ingest.load_image import to_array
    from render.a2ui_envelope import wrap_a2ui_part
    from render.a2ui_lifecycle import (
        build_create_surface,
        build_update_components,
    )

logger = logging.getLogger(__name__)

# A2UI v0.9 requires a component with the id "root" and the SDK requires it to
# be first in the list. Without it the payload validates, the renderer accepts
# it, and nothing is drawn, because there is no entry point into the tree.
_ROOT_ID: str = "root"
_COLUMN_ID: str = "scan-column"
_TITLE_ID: str = "scan-title"
_CAPTION_ID: str = "scan-caption"
_IMAGE_ID: str = "scan-image"

# The widest we send. A log sheet is scanned for reading, not for measuring, and
# the chat pane is never more than about a thousand pixels across; sending more
# costs payload and buys the reader nothing. Set above the pane width so a
# reader who opens the image full-screen still has detail to zoom into.
MAX_DISPLAY_WIDTH_PX: int = 1600

# JPEG quality for the re-encode. A log sheet is line art on paper, where the
# ringing that JPEG introduces around hard edges is most visible, so this sits
# well above the usual photographic default of 75.
_JPEG_QUALITY: int = 88

# The gateway rejects any single A2UI message over
# kMaxA2uiPayloadBytes = 512 * 1024
# (cloud/ai/agentis/gateway/agentspace/converters/a2ui_converters.h:21).
# Half of that is the working budget for the encoded image: the remainder
# covers the base64 expansion already counted, the component tree, the envelope
# and JSON escaping, with room to spare. If a scan will not fit, it is
# downscaled further rather than refused — an approximate picture of the sheet
# is worth far more to the reader than an error message.
_MAX_ENCODED_BYTES: int = 256 * 1024

# The floor on that downscaling. Below roughly this width a log sheet stops
# being readable at all, and returning an unreadable image would be worse than
# saying plainly that it cannot be shown.
_MIN_DISPLAY_WIDTH_PX: int = 400


def build_scan_surface(
    image: RasterImage,
    surface_id: str,
    title: str,
    caption: str,
) -> list[types.Part]:
    """Build the Parts that show a scanned log sheet in Gemini Enterprise chat.

    surface_id must be new for this response. Reusing one that has already been
    created does not raise and does not render; the update is simply dropped.
    """
    data_uri = encode_data_uri(image)

    messages = [
        build_create_surface(surface_id=surface_id),
        build_update_components(
            surface_id=surface_id,
            components=build_scan_components(title, caption, data_uri),
        ),
    ]

    # One message per Part. Bundling them into a single Part as a JSON array is
    # accepted by the transport and then silently ignored by the renderer.
    return [wrap_a2ui_part(message) for message in messages]


def build_scan_components(
    title: str,
    caption: str,
    data_uri: str,
) -> list[dict[str, Any]]:
    """The component tree: a card holding a heading, a caption and the picture."""
    return [
        # "root" leads the list because A2UI v0.9 requires exactly that.
        {"id": _ROOT_ID, "component": "Card", "child": _COLUMN_ID},
        {
            "id": _COLUMN_ID,
            "component": "Column",
            "children": [_TITLE_ID, _CAPTION_ID, _IMAGE_ID],
        },
        {"id": _TITLE_ID, "component": "Text", "text": title, "variant": "h3"},
        {
            "id": _CAPTION_ID,
            "component": "Text",
            "text": caption,
            "variant": "caption",
        },
        {
            "id": _IMAGE_ID,
            "component": "Image",
            # The catalog types url as a DynamicString, which admits a plain
            # string, so the data URI goes inline rather than through the data
            # model. One fewer indirection to get wrong, and the reason for the
            # data model — keeping a large blob out of the component tree — does
            # not apply when the blob IS the component's only content.
            "url": data_uri,
            "description": title,
            # "contain" so the whole sheet is visible. The default, "fill",
            # stretches a log to the container's aspect ratio, which distorts
            # the very curve shapes the reader is looking at.
            "fit": "contain",
            "variant": "largeFeature",
        },
    ]


def encode_data_uri(image: RasterImage) -> str:
    """Encode a raster as a base64 JPEG data: URI, within the payload budget.

    Downscales until the encoded image fits, halving the width each time. Each
    halving cuts the pixel count fourfold, so a scan far over budget converges
    in two or three attempts rather than creeping down.

    Raises:
        ValueError: if the sheet will not fit even at the minimum readable
            width. Raised rather than returning a thumbnail nobody can read.
    """
    rgb = to_array(image)
    width = min(image.width, MAX_DISPLAY_WIDTH_PX)

    while True:
        encoded = _encode_jpeg(rgb, width)
        if len(encoded) <= _MAX_ENCODED_BYTES:
            break
        if width // 2 < _MIN_DISPLAY_WIDTH_PX:
            raise ValueError(
                f"The {image.width}x{image.height} scan still encodes to "
                f"{len(encoded):,} bytes at {width} px wide, over the "
                f"{_MAX_ENCODED_BYTES:,} byte budget, and halving it again "
                f"would drop below the {_MIN_DISPLAY_WIDTH_PX} px at which a "
                "log sheet stops being readable."
            )
        width //= 2

    payload = base64.b64encode(encoded).decode("ascii")
    logger.info(
        "encode_data_uri: OK - %dx%d -> %d px wide, %d bytes JPEG, %d bytes base64",
        image.width,
        image.height,
        width,
        len(encoded),
        len(payload),
    )
    return f"data:image/jpeg;base64,{payload}"


def _encode_jpeg(rgb: np.ndarray, width: int) -> bytes:
    """Resize to the given width, preserving aspect, and encode as JPEG."""
    if width < rgb.shape[1]:
        height = max(1, round(rgb.shape[0] * width / rgb.shape[1]))
        # INTER_AREA is the correct choice for shrinking: it averages the pixels
        # it discards, where the interpolating filters sample them and turn a
        # thin curve into a dotted one.
        resized = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
    else:
        resized = rgb

    # imencode expects BGR; the raster is stored RGB. Skipping this swap would
    # not fail, it would just print the log in the wrong colours.
    bgr = cv2.cvtColor(resized, cv2.COLOR_RGB2BGR)
    ok, buffer = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY])
    if not ok:
        raise ValueError(
            f"OpenCV could not JPEG-encode a {resized.shape[1]}x{resized.shape[0]} image."
        )
    return bytes(buffer)

