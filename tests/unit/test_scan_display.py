"""Unit tests for showing the scanned sheet itself in Gemini Enterprise.

Covers three things the GE run of 2026-09-18 got wrong or could not do:
the scan was described instead of displayed, an ambiguous name would have been
guessed at, and the model fabricated A2UI payloads into its own prose.
"""

from __future__ import annotations

import base64
import json
from unittest import mock

import cv2
import numpy as np
import pytest
from google.genai import types

from app.ingest.load_image import FORMAT_RGB, from_array
from app.integration import agent as agent_module
from app.integration import tools as tools_module
from app.render.a2ui_envelope import (
    A2A_DATA_PART_CLOSE_TAG,
    A2A_DATA_PART_OPEN_TAG,
)
from app.render.scan_image import (
    MAX_DISPLAY_WIDTH_PX,
    build_scan_components,
    build_scan_surface,
    encode_data_uri,
)


class FakeContext:
    """The only part of ToolContext these tools touch."""

    def __init__(self) -> None:
        self.state: dict[str, str] = {}


def _raster(width: int, height: int, noisy: bool = False):
    """A RasterImage of the given size.

    Noise is offered because a flat colour compresses to almost nothing, which
    would make a size-budget test pass for the wrong reason.
    """
    if noisy:
        rng = np.random.default_rng(seed=0)
        pixels = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
    else:
        pixels = np.full((height, width, 3), 200, dtype=np.uint8)
    return from_array(pixels, FORMAT_RGB)


# ---------------------------------------------------------------- data URI


def test_a_scan_encodes_to_a_jpeg_data_uri() -> None:
    uri = encode_data_uri(_raster(300, 200))

    assert uri.startswith("data:image/jpeg;base64,")
    decoded = base64.b64decode(uri.split(",", 1)[1])
    # Round-trips through OpenCV, so what the browser gets is a real JPEG and
    # not merely a string that starts with the right words.
    image = cv2.imdecode(np.frombuffer(decoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (200, 300, 3)


def test_a_small_scan_is_never_upscaled() -> None:
    """Enlarging a scan invents detail and costs payload for nothing."""
    uri = encode_data_uri(_raster(320, 240))
    decoded = base64.b64decode(uri.split(",", 1)[1])
    image = cv2.imdecode(np.frombuffer(decoded, dtype=np.uint8), cv2.IMREAD_COLOR)

    assert image.shape[1] == 320


def test_an_oversized_scan_is_capped_at_the_display_width() -> None:
    uri = encode_data_uri(_raster(MAX_DISPLAY_WIDTH_PX * 2, 400))
    decoded = base64.b64decode(uri.split(",", 1)[1])
    image = cv2.imdecode(np.frombuffer(decoded, dtype=np.uint8), cv2.IMREAD_COLOR)

    assert image.shape[1] == MAX_DISPLAY_WIDTH_PX
    # Aspect ratio held: 400 * 1600/3200 = 200.
    assert image.shape[0] == 200


def test_the_colour_channels_survive_the_round_trip() -> None:
    """A red sheet must come back red. RGB/BGR is swapped twice and must cancel."""
    pixels = np.zeros((40, 60, 3), dtype=np.uint8)
    pixels[:, :, 0] = 255  # red, in RGB order
    uri = encode_data_uri(from_array(pixels, FORMAT_RGB))

    decoded = base64.b64decode(uri.split(",", 1)[1])
    bgr = cv2.imdecode(np.frombuffer(decoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    blue, green, red = bgr[20, 30]

    assert red > 200 and blue < 60 and green < 60


def test_an_unshrinkable_scan_fails_rather_than_returning_a_thumbnail() -> None:
    """Better to say it cannot be shown than to show something unreadable."""
    with mock.patch("app.render.scan_image._MAX_ENCODED_BYTES", 64):
        with pytest.raises(ValueError, match="stops being readable"):
            encode_data_uri(_raster(1600, 1200, noisy=True))


def test_a_large_noisy_scan_is_downscaled_until_it_fits() -> None:
    """The budget is met by halving, not by refusing.

    60 KB is chosen against measurement: this noise field encodes to roughly
    200 KB at 800 px and 52 KB at 400 px, so the loop must halve twice and then
    stop, which is the behaviour under test. A tighter budget would drive it
    into the refusal path covered by the test above.
    """
    with mock.patch("app.render.scan_image._MAX_ENCODED_BYTES", 60_000):
        uri = encode_data_uri(_raster(1600, 1200, noisy=True))

    decoded = base64.b64decode(uri.split(",", 1)[1])
    assert len(decoded) <= 60_000
    image = cv2.imdecode(np.frombuffer(decoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image.shape[1] < 1600


# ------------------------------------------------------------- components


def test_the_component_tree_leads_with_root() -> None:
    """A2UI draws nothing at all if 'root' is missing or not first."""
    components = build_scan_components("a.jpg", "caption", "data:image/jpeg;base64,AA")

    assert components[0]["id"] == "root"
    assert components[0]["component"] == "Card"


def test_the_image_component_carries_the_data_uri_inline() -> None:
    uri = "data:image/jpeg;base64,AAAA"
    image = next(
        c for c in build_scan_components("a.jpg", "cap", uri) if c["component"] == "Image"
    )

    assert image["url"] == uri
    # "fill" would stretch the sheet to the pane's aspect ratio and distort the
    # curve shapes the reader is there to look at.
    assert image["fit"] == "contain"


def test_the_surface_is_two_parts_create_then_components() -> None:
    parts = build_scan_surface(
        _raster(100, 80), surface_id="surface-1", title="a.jpg", caption="cap"
    )

    assert len(parts) == 2
    kinds = []
    for part in parts:
        raw = part.inline_data.data.decode("utf-8")
        inner = raw[len(A2A_DATA_PART_OPEN_TAG) : -len(A2A_DATA_PART_CLOSE_TAG)]
        kinds.append(next(k for k in json.loads(inner)["data"] if k != "version"))

    assert kinds == ["createSurface", "updateComponents"]


# -------------------------------------------------------- name resolution


@pytest.mark.parametrize(
    "requested",
    [
        "Scanned Well Logs/Well_log_schlum.jpg",
        "Well_log_schlum.jpg",
        "well_log_schlum.jpg",
        "Well_log_schlum",
    ],
)
def test_a_scan_is_found_by_key_basename_or_stem(requested: str) -> None:
    available = [
        "Scanned Well Logs/Well_log_schlum.jpg",
        "Scanned Well Logs/Other.pdf",
    ]
    assert tools_module._match_scans(requested, available) == [
        "Scanned Well Logs/Well_log_schlum.jpg"
    ]


def test_a_stem_shared_by_two_files_returns_both() -> None:
    """The JPEG and the PDF of one well share a stem; the user must choose."""
    available = [
        "Scanned Well Logs/Well_log_schlum.jpg",
        "Scanned Well Logs/Well_log_schlum.pdf",
    ]
    assert len(tools_module._match_scans("Well_log_schlum", available)) == 2


def test_an_exact_key_wins_over_a_stem_collision() -> None:
    """If the user gave the real key there is nothing left to interpret."""
    available = [
        "Scanned Well Logs/Well_log_schlum.jpg",
        "Scanned Well Logs/Well_log_schlum.pdf",
    ]
    assert tools_module._match_scans(available[1], available) == [available[1]]


# ------------------------------------------------------------------ tool


def _inventory(names: list[str]):
    scans = []
    for name in names:
        scan = mock.Mock()
        scan.name = name
        scans.append(scan)
    return mock.Mock(ok=True, error=None, scans=scans)


def test_show_scanned_log_queues_the_scan_for_display() -> None:
    context = FakeContext()
    with (
        mock.patch.object(
            tools_module,
            "scan_bucket_inventory",
            return_value=_inventory(["Scanned Well Logs/a.jpg"]),
        ),
        mock.patch.object(tools_module, "read_bytes", return_value=b"x"),
        mock.patch.object(tools_module, "load_image", return_value=_raster(919, 778)),
    ):
        result = tools_module.show_scanned_log("a.jpg", context)

    assert result["ok"] is True
    assert result["pixel_size"] == [919, 778]
    assert context.state[tools_module.PENDING_SCAN_KEY] == "Scanned Well Logs/a.jpg"


def test_show_scanned_log_refuses_to_guess_between_two_scans() -> None:
    """Two scans of one well are usually different runs. Ask, do not pick."""
    context = FakeContext()
    with mock.patch.object(
        tools_module,
        "scan_bucket_inventory",
        return_value=_inventory(
            ["Scanned Well Logs/a.jpg", "Scanned Well Logs/a.pdf"]
        ),
    ):
        result = tools_module.show_scanned_log("a", context)

    assert result["ok"] is False
    assert len(result["candidates"]) == 2
    assert tools_module.PENDING_SCAN_KEY not in context.state


def test_show_scanned_log_lists_what_is_there_when_nothing_matches() -> None:
    context = FakeContext()
    with mock.patch.object(
        tools_module,
        "scan_bucket_inventory",
        return_value=_inventory(["Scanned Well Logs/a.jpg"]),
    ):
        result = tools_module.show_scanned_log("nonexistent.jpg", context)

    assert result["ok"] is False
    assert result["candidates"] == ["Scanned Well Logs/a.jpg"]


def test_an_unreadable_scan_does_not_queue_a_surface() -> None:
    """A failed decode must not leave a surface pending for the next reply."""
    context = FakeContext()
    with (
        mock.patch.object(
            tools_module,
            "scan_bucket_inventory",
            return_value=_inventory(["Scanned Well Logs/a.jpg"]),
        ),
        mock.patch.object(tools_module, "read_bytes", return_value=b"x"),
        mock.patch.object(
            tools_module, "load_image", side_effect=ValueError("not an image")
        ),
    ):
        result = tools_module.show_scanned_log("a.jpg", context)

    assert result["ok"] is False
    assert tools_module.PENDING_SCAN_KEY not in context.state


# ------------------------------------------------------------- dispatcher


def test_a_pending_chart_outranks_a_pending_scan() -> None:
    """Digitising queues a chart; the reader wants the result, not the input."""
    context = FakeContext()
    context.state[tools_module.PENDING_CHART_KEY] = "Digitised Well Logs/W.las"
    context.state[tools_module.PENDING_SCAN_KEY] = "Scanned Well Logs/a.jpg"

    with (
        mock.patch.object(
            agent_module, "_emit_log_chart", return_value="CHART"
        ) as chart,
        mock.patch.object(agent_module, "_emit_scan_image") as scan,
    ):
        assert agent_module.emit_a2ui_surface(context) == "CHART"

    chart.assert_called_once()
    scan.assert_not_called()


def test_the_losing_surface_is_cleared_so_it_cannot_reattach_later() -> None:
    """Left in state, the scan would reappear against an unrelated later reply."""
    context = FakeContext()
    context.state[tools_module.PENDING_CHART_KEY] = "Digitised Well Logs/W.las"
    context.state[tools_module.PENDING_SCAN_KEY] = "Scanned Well Logs/a.jpg"

    with mock.patch.object(agent_module, "_emit_log_chart", return_value="CHART"):
        agent_module.emit_a2ui_surface(context)

    assert not context.state[tools_module.PENDING_SCAN_KEY]
    assert not context.state[tools_module.PENDING_CHART_KEY]


def test_a_pending_scan_alone_draws_the_scan() -> None:
    context = FakeContext()
    context.state[tools_module.PENDING_SCAN_KEY] = "Scanned Well Logs/a.jpg"

    with mock.patch.object(
        agent_module, "_emit_scan_image", return_value="SCAN"
    ) as scan:
        assert agent_module.emit_a2ui_surface(context) == "SCAN"

    assert scan.call_args.args[0] == "Scanned Well Logs/a.jpg"


# --------------------------------------------------------------- scrubber


def _response(*texts: str) -> types.Content:
    return mock.Mock(
        content=types.Content(role="model", parts=[types.Part(text=t) for t in texts])
    )


def test_a_fabricated_payload_is_cut_out_of_the_model_text() -> None:
    blob = f'{A2A_DATA_PART_OPEN_TAG}{{"version":"v0.9"}}{A2A_DATA_PART_CLOSE_TAG}'
    response = _response(f"Here is the log.{blob} Enjoy.")

    result = agent_module.strip_fabricated_a2ui(response)

    assert result is not None
    assert result.content.parts[0].text == "Here is the log. Enjoy."


def test_several_payloads_in_one_reply_all_go() -> None:
    """The live failure emitted three, two of them copied from the turn before."""
    blob = f"{A2A_DATA_PART_OPEN_TAG}x{A2A_DATA_PART_CLOSE_TAG}"
    response = _response(f"a{blob}b{blob}c{blob}")

    result = agent_module.strip_fabricated_a2ui(response)

    assert result.content.parts[0].text == "abc"


def test_an_unclosed_payload_truncates_the_rest() -> None:
    """A half-written payload is not prose; keeping it would print raw JSON."""
    response = _response(f"Answer.{A2A_DATA_PART_OPEN_TAG}" + '{"version":')

    result = agent_module.strip_fabricated_a2ui(response)

    assert result.content.parts[0].text == "Answer."


def test_a_part_that_was_only_a_payload_is_dropped() -> None:
    blob = f"{A2A_DATA_PART_OPEN_TAG}x{A2A_DATA_PART_CLOSE_TAG}"
    response = _response("Real answer.", blob)

    result = agent_module.strip_fabricated_a2ui(response)

    assert [p.text for p in result.content.parts] == ["Real answer."]


def test_a_reply_that_was_entirely_payload_still_has_a_part() -> None:
    """An empty parts list reads downstream as a malformed turn."""
    blob = f"{A2A_DATA_PART_OPEN_TAG}x{A2A_DATA_PART_CLOSE_TAG}"
    result = agent_module.strip_fabricated_a2ui(_response(blob))

    assert len(result.content.parts) == 1
    assert result.content.parts[0].text == ""


def test_clean_text_is_returned_untouched() -> None:
    """None tells ADK to keep the original rather than swap in a copy."""
    assert agent_module.strip_fabricated_a2ui(_response("Just prose.")) is None


def test_a_response_with_no_content_is_left_alone() -> None:
    assert agent_module.strip_fabricated_a2ui(mock.Mock(content=None)) is None
    assert agent_module.strip_fabricated_a2ui(None) is None


# --------------------------------------------------- history sanitizer tests


def test_sanitize_llm_request_history_strips_a2ui_from_past_turns() -> None:
    blob = f'{A2A_DATA_PART_OPEN_TAG}{{"version":"v0.9"}}{A2A_DATA_PART_CLOSE_TAG}'
    request = mock.Mock(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part(text="Can I see the scan?")],
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part(text=f"Here is the log: {blob}"),
                ],
            ),
        ],
        config=None,
    )

    agent_module.sanitize_llm_request_history(llm_request=request)

    assert request.contents[0].parts[0].text == "Can I see the scan?"
    assert request.contents[1].parts[0].text == "Here is the log: "
    assert request.config.max_output_tokens == 1024


def test_sanitize_llm_request_history_drops_parts_that_are_pure_payload() -> None:
    blob = f'{A2A_DATA_PART_OPEN_TAG}{{"version":"v0.9"}}{A2A_DATA_PART_CLOSE_TAG}'
    request = mock.Mock(
        contents=[
            types.Content(
                role="model",
                parts=[
                    types.Part(text="Valid answer"),
                    types.Part(text=blob),
                ],
            ),
            types.Content(
                role="model",
                parts=[
                    types.Part(text=blob),
                ],
            ),
        ],
        config=types.GenerateContentConfig(max_output_tokens=8192),
    )

    agent_module.sanitize_llm_request_history(llm_request=request)

    assert [p.text for p in request.contents[0].parts] == ["Valid answer"]
    assert [p.text for p in request.contents[1].parts] == [""]
    assert request.config.max_output_tokens == 1024


def test_sanitize_llm_request_history_leaves_clean_contents_alone() -> None:
    request = mock.Mock(
        contents=[
            types.Content(
                role="user",
                parts=[types.Part(text="Hello")],
            ),
        ],
        config=types.GenerateContentConfig(max_output_tokens=500),
    )

    agent_module.sanitize_llm_request_history(llm_request=request)

    assert request.contents[0].parts[0].text == "Hello"
    assert request.config.max_output_tokens == 500

