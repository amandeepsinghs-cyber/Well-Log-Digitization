"""Unit tests for A2UI v0.9 wire protocol serialization and envelope framing.

Covers the ADK -> A2A transport contract. Component-level JSON Schema validation
lives in test_a2ui_catalog_validation.py.
"""

import json
from unittest import mock

from google.adk.a2a.converters import part_converter

from app.contracts import BucketInventory
from app.integration import agent as agent_module
from app.render.a2ui_envelope import (
    A2A_DATA_PART_CLOSE_TAG,
    A2A_DATA_PART_OPEN_TAG,
    A2UI_MIME,
    wrap_a2ui_part,
)
from app.render.a2ui_lifecycle import DEFAULT_GE_CATALOG_ID, build_create_surface

_FAKE_INVENTORY = BucketInventory(
    bucket="og-agentic-petrophysics-data-asia-south1",
    region="asia-south1",
)


def test_wrap_a2ui_part_frames_correctly() -> None:
    """wrap_a2ui_part builds a text/plain envelope tagged with <a2a_datapart_json>."""
    msg = build_create_surface(surface_id="test-surf-123")
    part = wrap_a2ui_part(msg)

    assert part.inline_data is not None
    assert part.inline_data.mime_type == "text/plain"
    assert part.part_metadata == {"mimeType": A2UI_MIME}

    raw_text = part.inline_data.data.decode("utf-8")
    assert raw_text.startswith(A2A_DATA_PART_OPEN_TAG)
    assert raw_text.endswith(A2A_DATA_PART_CLOSE_TAG)

    inner_json = raw_text[len(A2A_DATA_PART_OPEN_TAG) : -len(A2A_DATA_PART_CLOSE_TAG)]
    payload = json.loads(inner_json)

    assert payload["kind"] == "data"
    assert payload["metadata"] == {"mimeType": A2UI_MIME}
    assert payload["data"]["version"] == "v0.9"
    assert payload["data"]["createSurface"]["surfaceId"] == "test-surf-123"
    assert payload["data"]["createSurface"]["catalogId"] == DEFAULT_GE_CATALOG_ID


def test_emitted_parts_convert_to_a2a_dataparts() -> None:
    """ADK must unbox our parts into native A2A DataParts, not file attachments.

    Regression guard for the 'application/json+a2ui: Unsupported attachment'
    failure, where ADK wrapped the payload as a downloadable FilePart because the
    blob was not framed with the <a2a_datapart_json> tags.
    """
    with mock.patch.object(
        agent_module, "scan_bucket_inventory", return_value=_FAKE_INVENTORY
    ):
        content = agent_module.emit_a2ui_surface()

    assert content is not None
    assert len(content.parts) == 2

    for p in content.parts:
        assert p.inline_data.mime_type == "text/plain"
        a2a_part = part_converter.convert_genai_part_to_a2a_part(p)
        assert a2a_part is not None
        # Native structured data, NOT a file attachment (raw bytes / url).
        assert a2a_part.HasField("data")
        assert not a2a_part.HasField("raw")
        assert not a2a_part.HasField("url")
        assert a2a_part.metadata["mimeType"] == A2UI_MIME
