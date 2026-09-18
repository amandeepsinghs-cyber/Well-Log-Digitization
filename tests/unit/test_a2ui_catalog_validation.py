"""Pre-deploy gate: validate every emitted A2UI component against the REAL catalog.

This reproduces, locally and in ~2 seconds, the exact JSON Schema validation the
Gemini Enterprise Angular renderer performs in the browser. Without it, schema
mistakes are only discovered by deploying (~4 minutes) and reading a red error
box in chat, one error at a time.

The catalog in tests/fixtures/a2ui/ is a pinned copy of:
  https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json
  https://a2ui.org/specification/v0_9/common_types.json

Refresh it with tests/fixtures/a2ui/REFRESH.md when Gemini Enterprise ships a new
catalog version.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.contracts import BucketInventory, GcsObjectInfo
from app.render.a2ui_envelope import (
    A2A_DATA_PART_CLOSE_TAG,
    A2A_DATA_PART_OPEN_TAG,
)
from app.render.a2ui_lifecycle import DEFAULT_GE_CATALOG_ID
from app.render.inventory_card import build_inventory_components

FIXTURES = Path(__file__).parent.parent / "fixtures" / "a2ui"
COMMON_TYPES_URL = "https://a2ui.org/specification/v0_9/common_types.json"


@pytest.fixture(scope="module")
def catalog() -> dict[str, Any]:
    """The pinned Gemini Enterprise composite catalog."""
    return json.loads((FIXTURES / "ge_composite_catalog.json").read_text())


@pytest.fixture(scope="module")
def registry(catalog: dict[str, Any]) -> Registry:
    """Schema registry resolving the catalog's absolute $refs to common_types."""
    common = json.loads((FIXTURES / "common_types.json").read_text())
    return Registry().with_resources(
        [
            (COMMON_TYPES_URL, Resource.from_contents(common)),
            (catalog["$id"], Resource.from_contents(catalog)),
        ]
    )


def _validate(
    components: list[dict[str, Any]],
    catalog: dict[str, Any],
    registry: Registry,
) -> list[str]:
    """Validate components against the catalog. Returns human-readable errors."""
    errors: list[str] = []
    catalog_id = catalog["$id"]
    for comp in components:
        name = comp.get("component")
        comp_id = comp.get("id", "<no-id>")
        if name not in catalog["components"]:
            errors.append(f"[{comp_id}] unknown component '{name}'")
            continue
        # Reference INTO the registered catalog document so that relative refs
        # such as '#/$defs/CatalogComponentCommon' resolve correctly.
        validator = Draft202012Validator(
            {"$ref": f"{catalog_id}#/components/{name}"}, registry=registry
        )
        for err in sorted(validator.iter_errors(comp), key=lambda e: e.json_path):
            errors.append(f"[{comp_id}] {name}: {err.message}")
    return errors


def _inventory(objects: int = 1) -> BucketInventory:
    """Build a representative inventory without touching the network."""
    scans = [
        GcsObjectInfo(
            name=f"Scanned Well Logs/well_{i}.jpg",
            size_bytes=52597,
            content_type="image/jpeg",
            updated="2026-09-17T10:18:39+00:00",
        )
        for i in range(objects)
    ]
    return BucketInventory(
        bucket="og-agentic-petrophysics-data-asia-south1",
        region="asia-south1",
        scans=scans,
    )


# --------------------------------------------------------------------------
# Catalog contract regression guards
# --------------------------------------------------------------------------


def test_catalog_id_matches_create_surface(catalog: dict[str, Any]) -> None:
    """createSurface must advertise the catalog id the renderer actually serves."""
    assert DEFAULT_GE_CATALOG_ID == catalog["catalogId"]


def test_card_requires_single_child_not_children(catalog: dict[str, Any]) -> None:
    """Pin the constraint that caused the production 'Validation failed' error."""
    card = catalog["components"]["Card"]["allOf"][-1]
    assert "child" in card["required"], "Card must require a single 'child'"
    assert "children" not in card["properties"], "Card must NOT accept 'children'"
    assert "appearance" not in card["properties"], "Card has no 'appearance'"


def test_text_uses_variant_not_usage_hint(catalog: dict[str, Any]) -> None:
    """Text styling is 'variant'; 'usageHint' is rejected by the renderer."""
    text = catalog["components"]["Text"]["allOf"][-1]
    assert "variant" in text["properties"]
    assert "usageHint" not in text["properties"]


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "inventory",
    [
        pytest.param(_inventory(objects=1), id="single-scan"),
        pytest.param(_inventory(objects=3), id="multiple-scans"),
        pytest.param(
            BucketInventory(bucket="b", region="asia-south1"), id="empty-bucket"
        ),
        pytest.param(
            BucketInventory(
                bucket="b",
                region="asia-south1",
                ok=False,
                error="Forbidden: 403 does not have storage.objects.list access",
            ),
            id="scan-failed",
        ),
    ],
)
def test_inventory_components_validate(
    inventory: BucketInventory, catalog: dict[str, Any], registry: Registry
) -> None:
    """Every component the card can emit must satisfy the live GE schema."""
    components = build_inventory_components(inventory)
    errors = _validate(components, catalog, registry)
    assert not errors, "A2UI schema violations:\n  " + "\n  ".join(errors)


def test_component_ids_are_unique_and_children_resolve(
    catalog: dict[str, Any],
) -> None:
    """Every referenced child id must exist; duplicate ids would clobber nodes."""
    components = build_inventory_components(_inventory(objects=3))
    ids = [c["id"] for c in components]
    assert len(ids) == len(set(ids)), f"duplicate component ids: {ids}"

    known = set(ids)
    for comp in components:
        if "child" in comp:
            assert comp["child"] in known, f"dangling child {comp['child']}"
        for child in comp.get("children", []):
            assert child in known, f"dangling child {child} in {comp['id']}"


@pytest.mark.parametrize(
    "inventory",
    [
        pytest.param(_inventory(objects=1), id="single-scan"),
        pytest.param(_inventory(objects=3), id="multiple-scans"),
        pytest.param(
            BucketInventory(bucket="b", region="asia-south1"), id="empty-bucket"
        ),
        pytest.param(
            BucketInventory(
                bucket="b", region="asia-south1", ok=False, error="403"
            ),
            id="scan-failed",
        ),
    ],
)
def test_component_tree_has_root_anchor(inventory: BucketInventory) -> None:
    """A2UI v0.9 requires a component with id 'root', and it must come first.

    This invariant is stated in prose in the protocol spec and is NOT encoded in
    the JSON Schema, so schema validation passes without it. The renderer then
    accepts the payload and silently draws nothing, because it has no entry
    point into the component tree - no error, no card, no log line.

    Sources:
      third_party/a2ui/specification/v0_9/docs/a2ui_protocol.md:182
        "One of the components in one of the component lists MUST have an 'id'
         of 'root' to serve as the root of the component tree."
      third_party/a2ui/agent_sdks/python/a2ui_agent/src/a2ui/schema/constants.py:105
        "The 'root' component MUST be the FIRST element."
    """
    components = build_inventory_components(inventory)

    ids = [c["id"] for c in components]
    assert "root" in ids, (
        "no component has id 'root' - the renderer will draw nothing at all. "
        f"got ids: {ids}"
    )
    assert components[0]["id"] == "root", (
        f"'root' must be the first component, found {components[0]['id']!r} first"
    )


def test_emitted_parts_validate_end_to_end(
    catalog: dict[str, Any], registry: Registry
) -> None:
    """Validate what the agent ACTUALLY puts on the wire, not a reconstruction.

    Parses the real <a2a_datapart_json> envelopes produced by the callback and
    validates the components carried inside them.
    """
    from app.integration import agent as agent_module

    with mock.patch.object(
        agent_module, "scan_bucket_inventory", return_value=_inventory(objects=2)
    ):
        content = agent_module.emit_a2ui_surface()

    assert content is not None, "callback must emit an A2UI surface"
    assert len(content.parts) == 2, "expected createSurface + updateComponents"

    saw_update = False
    for part in content.parts:
        raw = part.inline_data.data.decode("utf-8")
        assert raw.startswith(A2A_DATA_PART_OPEN_TAG)
        assert raw.endswith(A2A_DATA_PART_CLOSE_TAG)
        payload = json.loads(
            raw[len(A2A_DATA_PART_OPEN_TAG) : -len(A2A_DATA_PART_CLOSE_TAG)]
        )
        msg = payload["data"]
        assert msg["version"] == "v0.9"

        if "createSurface" in msg:
            assert msg["createSurface"]["catalogId"] == catalog["catalogId"]
        if "updateComponents" in msg:
            saw_update = True
            wire_components = msg["updateComponents"]["components"]

            errors = _validate(wire_components, catalog, registry)
            assert not errors, "A2UI schema violations on the wire:\n  " + "\n  ".join(
                errors
            )

            # Schema-valid but rootless payloads render as nothing at all, so
            # assert the protocol anchor on the real bytes too.
            assert wire_components[0]["id"] == "root", (
                "first component on the wire must be id 'root', got "
                f"{wire_components[0]['id']!r}"
            )

    assert saw_update, "no updateComponents message was emitted"


def test_callback_survives_gcs_failure() -> None:
    """A GCS outage must degrade to an error card, never break the text reply."""
    from app.integration import agent as agent_module

    failed = BucketInventory(
        bucket="b", region="asia-south1", ok=False, error="boom"
    )
    with mock.patch.object(
        agent_module, "scan_bucket_inventory", return_value=failed
    ):
        content = agent_module.emit_a2ui_surface()

    assert content is not None
    blob = b"".join(p.inline_data.data for p in content.parts).decode("utf-8")
    assert "Inventory scan failed" in blob
    assert "boom" in blob


# --------------------------------------------------------------------------
# The well log chart
# --------------------------------------------------------------------------


def _log_components() -> list[dict[str, Any]]:
    """The component tree for a representative three-track log."""
    from app.render.a2ui_emit import build_log_components
    from app.render.track_layout import build_log_plot
    from tests.unit.test_render_log_chart import _three_track_document

    return build_log_components(build_log_plot(_three_track_document()))


def test_vega_chart_spec_cannot_hold_an_inline_object(
    catalog: dict[str, Any],
) -> None:
    """Pin the constraint that decides where the Vega specification lives.

    VegaChart.spec is typed as DynamicValue, whose oneOf admits a string, a
    number, a boolean, an array, a data binding or a function call — and NOT an
    object. Putting the specification inline, which is the obvious thing to do
    and is what every Vega-Lite example shows, produces a payload the renderer
    rejects. That is why a2ui_emit sends it in updateDataModel instead.

    If a future catalog adds an object variant this test fails, and the
    indirection can be reconsidered rather than carried forever as folklore.
    """
    common = json.loads((FIXTURES / "common_types.json").read_text())
    variants = common["$defs"]["DynamicValue"]["oneOf"]

    assert {"type": "object"} not in variants
    assert {"$ref": "#/$defs/DataBinding"} in variants

    spec_property = catalog["components"]["VegaChart"]["allOf"][-1]["properties"]["spec"]
    assert spec_property["$ref"].endswith("#/$defs/DynamicValue")


def test_log_chart_components_validate(
    catalog: dict[str, Any], registry: Registry
) -> None:
    """Every component the chart emits must satisfy the live GE schema."""
    errors = _validate(_log_components(), catalog, registry)
    assert not errors, "A2UI schema violations:\n  " + "\n  ".join(errors)


def test_log_chart_tree_has_root_anchor_and_resolves() -> None:
    """Rootless or dangling trees validate and then draw nothing at all."""
    components = _log_components()
    ids = [c["id"] for c in components]

    assert components[0]["id"] == "root"
    assert len(ids) == len(set(ids)), f"duplicate component ids: {ids}"

    for comp in components:
        if "child" in comp:
            assert comp["child"] in ids, f"dangling child {comp['child']}"
        for child in comp.get("children", []):
            assert child in ids, f"dangling child {child} in {comp['id']}"


def test_log_chart_binds_to_a_path_the_data_model_actually_sets() -> None:
    """A pointer into an empty slot renders an empty box and logs nothing."""
    from app.render.a2ui_emit import build_log_surface
    from app.render.track_layout import build_log_plot
    from tests.unit.test_render_log_chart import _payloads, _three_track_document

    plot = build_log_plot(_three_track_document())
    messages = _payloads(build_log_surface(plot, surface_id="surface-catalog-test"))

    chart = next(
        component
        for message in messages
        if "updateComponents" in message["data"]
        for component in message["data"]["updateComponents"]["components"]
        if component["component"] == "VegaChart"
    )
    data = next(
        # The outer ["data"] is the ADK transport envelope; the inner key is
        # the A2UI field, and A2UI names it "value".
        message["data"]["updateDataModel"]["value"]
        for message in messages
        if "updateDataModel" in message["data"]
    )

    # The pointer is a JSON Pointer: strip the leading slash to index the model.
    pointer = chart["spec"]["path"]
    assert pointer.startswith("/")
    assert pointer.lstrip("/") in data



# --------------------------------------------------------------------------
# Scanned sheet surface
# --------------------------------------------------------------------------


def _scan_components() -> list[dict[str, Any]]:
    """The component tree for a scanned sheet, with a representative data URI."""
    from app.render.scan_image import build_scan_components

    return build_scan_components(
        "Well_log_schlum.jpg",
        "919 x 778 px  ·  the original scan, before digitisation",
        "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
    )


def test_scan_components_validate(
    catalog: dict[str, Any], registry: Registry
) -> None:
    """Every component the scan surface emits must satisfy the live GE schema."""
    errors = _validate(_scan_components(), catalog, registry)
    assert not errors, "A2UI schema violations:\n  " + "\n  ".join(errors)


def test_image_url_accepts_a_plain_string(catalog: dict[str, Any]) -> None:
    """Pin the property that lets a data: URI travel inline.

    Image.url is typed DynamicString, and DynamicString admits a plain string.
    That is the whole reason the base64 image can sit in the component rather
    than needing its own updateDataModel message. If a future catalog narrows
    this, the scan surface must be restructured and this test says so.
    """
    common = json.loads((FIXTURES / "common_types.json").read_text())
    variants = common["$defs"]["DynamicString"]["oneOf"]
    assert {"type": "string"} in variants

    url_property = catalog["components"]["Image"]["allOf"][-1]["properties"]["url"]
    assert url_property["$ref"].endswith("#/$defs/DynamicString")


def test_scan_tree_has_root_anchor_and_resolves() -> None:
    """Rootless or dangling trees validate and then draw nothing at all."""
    components = _scan_components()
    ids = [c["id"] for c in components]

    assert components[0]["id"] == "root"
    assert len(ids) == len(set(ids)), f"duplicate component ids: {ids}"

    for comp in components:
        if "child" in comp:
            assert comp["child"] in ids, f"dangling child {comp['child']}"
        for child in comp.get("children", []):
            assert child in ids, f"dangling child {child} in {comp['id']}"
