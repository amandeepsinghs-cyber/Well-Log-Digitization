"""Transcribe the printed text on a log sheet, using one language-model call.

In : the RGB sheet, its PageLayout, and its DepthTicks.
Out: a SheetText holding the curve headers, the depth column's header and the
     depth labels, all verbatim.
Rule: this is the ONLY language-model call in the digitisation pipeline, and it
      reads characters. It does no arithmetic, no unit conversion and no
      interpretation — "0.2 ohm.m 20" comes back as that string, and
      header/parse_header.py turns it into numbers in deterministic code. The
      split exists so that a model can never quietly invent a scale, which would
      put wrong values into a LAS file that looks entirely correct.
"""

from __future__ import annotations

import json
import logging

import cv2
import numpy as np
from google import genai
from google.genai import types

try:
    from app.contracts import (
        CurveHeaderText,
        DepthTick,
        PageLayout,
        RasterImage,
        SheetText,
    )
    from app.ingest.load_image import to_array
except ImportError:
    from contracts import (
        CurveHeaderText,
        DepthTick,
        PageLayout,
        RasterImage,
        SheetText,
    )
    from ingest.load_image import to_array

logger = logging.getLogger(__name__)

# Transcription is a short, well-conditioned task on clean printed text, so the
# fast model is the right one. It is the same model the agent itself runs on.
_MODEL = "gemini-2.5-flash"

# Crops are enlarged so that the smallest printed characters are comfortably
# above the model's image tile resolution. A depth label is only 14 px tall on
# the reference sheet; at that size the difference between "7,100" and "7,700"
# comes down to a handful of pixels.
_MIN_CROP_HEIGHT_PX = 120

# A few pixels of white are left around each depth label. Characters with
# descenders or a comma sit right on the edge of the detected ink rows, and
# cropping flush against them clips the stroke that distinguishes them.
_LABEL_MARGIN_PX = 3

_INSTRUCTION = """You are transcribing the printed text on a wireline well log.

You will be shown, in order:
  - one image per curve track, each the header block above that track;
  - one image of the depth column's header;
  - one image per printed depth label.

Transcribe EXACTLY what is printed. Specifically:
  - Copy digits, commas, decimal points, minus signs and units character for
    character. Do not tidy, round, convert or reorder anything.
  - A track header holds one or more curves stacked vertically. Each has a name
    line (for example "Gamma Ray") and, below it, a scale line with a value at
    the left, a unit in the middle, and a value at the right (for example
    "0 gAPI 150"). Return the scale line with single spaces between those three.
  - Report every curve you can read, in top-to-bottom order within each track,
    and use the track name given in the text before that image.
  - A scale may run high-to-low, for example "45 % -15". Keep that order.
  - If a line is blank or unreadable, return an empty string for it rather than
    a guess.
"""

# The model is pinned to this shape so the reply can be parsed without a second
# round of interpretation.
_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "curves": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "track_name": {"type": "STRING"},
                    "title": {"type": "STRING"},
                    "scale": {"type": "STRING"},
                },
                "required": ["track_name", "title", "scale"],
            },
        },
        "depth_header": {"type": "STRING"},
        "depth_labels": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["curves", "depth_header", "depth_labels"],
}


def read_sheet_text(
    image: RasterImage, layout: PageLayout, ticks: tuple[DepthTick, ...]
) -> SheetText:
    """Read the sheet's headers and depth labels.

    Args:
        image: the full RGB sheet.
        layout: the page decomposition, giving the crop rectangles.
        ticks: the depth labels to read, in depth order.

    Returns:
        SheetText, with one depth label per tick in the same order.

    Raises:
        ValueError: if the model returns no curve headers, a track name that is
            not on the sheet, or a number of depth labels that does not match
            the number of ticks. The last is the dangerous one: labels are
            paired with pixel rows by position, so a length mismatch would
            silently attach every depth to the wrong line.
    """
    array = to_array(image)
    parts = _build_parts(array, layout, ticks)
    reply = _call_model(parts)

    return _to_sheet_text(reply, layout, ticks)


def _build_parts(
    array: np.ndarray, layout: PageLayout, ticks: tuple[DepthTick, ...]
) -> list[types.Part]:
    """Assemble the one prompt: a labelled image for each region to be read.

    Each image is preceded by a text part naming it. That is what lets the model
    attribute a header to "Track 2" without being able to see where the track
    sits on the page — it never receives the whole sheet, only the crops.
    """
    header_top, header_bottom = layout.header_rows
    parts: list[types.Part] = [types.Part(text=_INSTRUCTION)]

    for track in layout.tracks:
        parts.append(types.Part(text=f"Header block above {track.name}:"))
        parts.append(
            _image_part(array, header_top, header_bottom, track.x_left, track.x_right)
        )

    parts.append(types.Part(text="Header of the depth column:"))
    parts.append(
        _image_part(
            array,
            header_top,
            header_bottom,
            layout.depth_column.x_left,
            layout.depth_column.x_right,
        )
    )

    for index, tick in enumerate(ticks, start=1):
        parts.append(types.Part(text=f"Depth label {index} of {len(ticks)}:"))
        parts.append(
            _image_part(
                array,
                tick.label_top - _LABEL_MARGIN_PX,
                tick.label_bottom + _LABEL_MARGIN_PX,
                layout.depth_column.x_left,
                layout.depth_column.x_right,
            )
        )

    return parts


def _image_part(
    array: np.ndarray, top: int, bottom: int, left: int, right: int
) -> types.Part:
    """Crop a region, enlarge it if it is small, and encode it as PNG.

    PNG rather than JPEG because a second lossy pass over text that is already a
    JPEG artefact blurs exactly the thin strokes the model has to read.
    """
    # Clamp to the sheet: the label margin can run past the top or bottom edge.
    top = max(0, top)
    left = max(0, left)
    crop = array[top : bottom + 1, left : right + 1]
    if crop.size == 0:
        raise ValueError(
            f"Crop rows {top}-{bottom}, columns {left}-{right} is empty for an "
            f"image of shape {array.shape}."
        )

    if crop.shape[0] < _MIN_CROP_HEIGHT_PX:
        scale = _MIN_CROP_HEIGHT_PX / crop.shape[0]
        # Cubic interpolation keeps character strokes smooth when upscaling;
        # nearest-neighbour would turn them into staircases.
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # cv2 writes BGR, and the pipeline works in RGB, so the channels are swapped
    # back for encoding. Without this the model sees a colour-inverted crop —
    # legible, but the colour cues in a header line would be wrong.
    encoded = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR) if crop.ndim == 3 else crop
    ok, buffer = cv2.imencode(".png", encoded)
    if not ok:
        raise ValueError(f"Could not PNG-encode a crop of shape {crop.shape}.")

    return types.Part.from_bytes(data=buffer.tobytes(), mime_type="image/png")


def _call_model(parts: list[types.Part]) -> dict:
    """Send the prompt and return the parsed JSON reply."""
    client = genai.Client()
    response = client.models.generate_content(
        model=_MODEL,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            # Transcription has one right answer, so there is nothing to sample.
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
        ),
    )
    if not response.text:
        raise ValueError(
            "The transcription model returned no text. Finish reason: "
            f"{response.candidates[0].finish_reason if response.candidates else 'none'}."
        )
    return json.loads(response.text)


def _to_sheet_text(
    reply: dict, layout: PageLayout, ticks: tuple[DepthTick, ...]
) -> SheetText:
    """Check the reply against what was asked for, and convert it.

    Everything checked here is something the model could plausibly get wrong in
    a way that no later stage would notice.
    """
    raw_curves = reply.get("curves") or []
    if not raw_curves:
        raise ValueError(
            "The transcription model read no curve headers from any of the "
            f"{len(layout.tracks)} track header images. Without a scale, no "
            "pixel can be converted to a value."
        )

    known_tracks = {track.name for track in layout.tracks}
    curves: list[CurveHeaderText] = []
    for entry in raw_curves:
        track_name = entry["track_name"]
        if track_name not in known_tracks:
            raise ValueError(
                f"The transcription model attributed a header to {track_name!r}, "
                f"which is not a track on this sheet ({sorted(known_tracks)}). "
                "The header cannot be matched to any pixels."
            )
        curves.append(
            CurveHeaderText(
                track_name=track_name,
                title=entry["title"].strip(),
                scale=entry["scale"].strip(),
            )
        )

    depth_labels = tuple(label.strip() for label in reply.get("depth_labels") or ())
    if len(depth_labels) != len(ticks):
        raise ValueError(
            f"{len(ticks)} depth label image(s) were sent but "
            f"{len(depth_labels)} were transcribed: {list(depth_labels)}. Labels "
            "are paired with pixel rows by position, so an unequal count would "
            "put every depth on the wrong line."
        )

    logger.info(
        "read_sheet_text: OK - %d curve header(s) across %d track(s), depth "
        "header %r, labels %s",
        len(curves),
        len(known_tracks),
        reply.get("depth_header", ""),
        list(depth_labels),
    )
    return SheetText(
        curves=tuple(curves),
        depth_header=(reply.get("depth_header") or "").strip(),
        depth_labels=depth_labels,
    )
