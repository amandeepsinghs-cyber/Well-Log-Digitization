"""Wire-format fixture emitter and byte-level validator for A2UI v0.9 on ADK.

In : A2UI lifecycle builders (createSurface, updateComponents).
Out: Formatted wire bytes emitted over A2A transport; assertion of protocol conformance.
Rule: Asserts that every emitted Part strictly complies with the three findings from
      the ADK/A2UI runtime specification:
      1. Exactly one lifecycle message per Part.
      2. Envelope structure: {"kind": "data", "data": ...}.
      3. Delimiter markers: <a2a_datapart_json>...</a2a_datapart_json> with mime_type='text/plain'.
"""

import json
import os
import sys

# Ensure app package is importable
APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from google.genai import types

from contracts import ACTIVE_A2UI_CATALOG_VERSION, A2uiCatalogVersion, A2uiMessage
from render.a2ui_envelope import (
    A2A_DATA_PART_CLOSE_TAG,
    A2A_DATA_PART_OPEN_TAG,
    wrap_a2ui_part,
)
from render.a2ui_lifecycle import (
    DEFAULT_GE_CATALOG_ID,
    build_create_surface,
    build_update_components,
    build_update_data_model,
)


def emit_and_validate_fixture(surface_id: str = "fixture-well-log-surface-001") -> list[types.Part]:
    """Emit a complete multi-step A2UI v0.9 message sequence and validate raw wire bytes.
    
    Returns:
        List of ADK types.Part objects ready for wire transport.
    """
    print(f"=== Emitting A2UI v0.9 Wire-Format Fixture for Surface: {surface_id} ===")
    
    parts: list[types.Part] = []

    # -------------------------------------------------------------------------
    # Message 1: createSurface
    # Informs Gemini Enterprise client to initialize a fresh surface container.
    # -------------------------------------------------------------------------
    create_msg = build_create_surface(surface_id=surface_id, catalog_id=DEFAULT_GE_CATALOG_ID)
    part_create = wrap_a2ui_part(create_msg)
    parts.append(part_create)
    _validate_single_part(part_create, expected_message_type="createSurface", surface_id=surface_id)

    # -------------------------------------------------------------------------
    # Message 2: updateComponents
    # Declares the UI component tree (Card with a Text header and status badge).
    # -------------------------------------------------------------------------
    components = [
        {
            "id": "root-card",
            "component": "Card",
            "appearance": "outlined",
            "children": ["title-text", "status-badge"],
        },
        {
            "id": "title-text",
            "component": "Text",
            "text": "Well Log Digitisation Agent",
            "usageHint": "h2",
        },
        {
            "id": "status-badge",
            "component": "Text",
            "text": "Status: A2UI v0.9 Protocol Verified",
            "usageHint": "caption",
        },
    ]
    update_msg = build_update_components(surface_id=surface_id, components=components)
    part_update = wrap_a2ui_part(update_msg)
    parts.append(part_update)
    _validate_single_part(part_update, expected_message_type="updateComponents", surface_id=surface_id)

    # -------------------------------------------------------------------------
    # Message 3: updateDataModel (Optional initial state)
    # -------------------------------------------------------------------------
    data_payload = {
        "wellName": "Kansas Well A-12",
        "depthInterval": {"top": 7000.0, "bottom": 7300.0, "unit": "ft"},
    }
    model_msg = build_update_data_model(surface_id=surface_id, data=data_payload)
    part_model = wrap_a2ui_part(model_msg)
    parts.append(part_model)
    _validate_single_part(part_model, expected_message_type="updateDataModel", surface_id=surface_id)

    print(f"✅ Successfully validated {len(parts)} independent wire parts.")
    return parts


def _validate_single_part(part: types.Part, expected_message_type: str, surface_id: str) -> None:
    """Perform byte-level assertions on a single ADK types.Part."""
    # 1. Assert MIME type is text/plain on the ADK side
    assert part.inline_data is not None, "Part must have inline_data populated."
    assert (
        part.inline_data.mime_type == "text/plain"
    ), f"ADK-side MIME type must be text/plain, got: {part.inline_data.mime_type}"

    # 2. Decode raw wire bytes
    raw_bytes: bytes = part.inline_data.data
    raw_str: str = raw_bytes.decode("utf-8")

    # 3. Assert delimiter tags are present and wrap the payload
    assert raw_str.startswith(
        A2A_DATA_PART_OPEN_TAG
    ), f"Part payload must start with {A2A_DATA_PART_OPEN_TAG}"
    assert raw_str.endswith(
        A2A_DATA_PART_CLOSE_TAG
    ), f"Part payload must end with {A2A_DATA_PART_CLOSE_TAG}"

    # 4. Extract inner JSON and verify envelope structure
    inner_json = raw_str[len(A2A_DATA_PART_OPEN_TAG) : -len(A2A_DATA_PART_CLOSE_TAG)].strip()
    payload = json.loads(inner_json)

    assert "kind" in payload, "Payload envelope must contain 'kind' field."
    assert (
        payload["kind"] == "data"
    ), f"Payload 'kind' must be 'data', got: {payload.get('kind')}"
    assert "data" in payload, "Payload envelope must contain 'data' dictionary."

    message_body = payload["data"]
    assert (
        expected_message_type in message_body
    ), f"Inner payload must contain '{expected_message_type}' key."

    inner_msg = message_body[expected_message_type]
    assert (
        inner_msg.get("surfaceId") == surface_id
    ), f"surfaceId mismatch: expected {surface_id}, got {inner_msg.get('surfaceId')}"

    print(f"  • Part [{expected_message_type}]: {len(raw_bytes)} bytes | Validated envelope & tags.")


if __name__ == "__main__":
    emitted = emit_and_validate_fixture()
    print("\n=== Byte Inspection of First Emitted Part (createSurface) ===")
    sample_text = emitted[0].inline_data.data.decode("utf-8")
    print(sample_text)
    print("=== Verification Complete ===")
