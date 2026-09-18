"""A2UI v0.9 component tree for the petrophysics data inventory card.

In : A BucketInventory produced by app.gcs.inventory.
Out: A flat list of A2UI v0.9 component dictionaries for 'updateComponents'.
Rule: Every component here MUST validate against the Gemini Enterprise composite
      catalog. Key constraints enforced by that schema:
        - Card accepts exactly ONE 'child' (a component id), never 'children'.
        - Multiple elements must be wrapped in a Column or Row.
        - Text uses 'variant' (h1-h5 | caption | body), never 'usageHint'.
        - Unknown properties are rejected outright (unevaluatedProperties: false).

      Beyond the schema, A2UI v0.9 imposes one protocol invariant that the JSON
      Schema does NOT encode, stated in prose only:

        "One of the components in one of the component lists MUST have an 'id'
         of 'root' to serve as the root of the component tree."
        -- third_party/a2ui/specification/v0_9/docs/a2ui_protocol.md:182

      The official SDK adds that it must also be first
      (third_party/a2ui/agent_sdks/python/a2ui_agent/src/a2ui/schema/constants.py:105).

      Violating this is silent: the payload validates, the renderer accepts it,
      and then draws nothing at all because it has no entry point into the tree.
      tests/unit/test_a2ui_catalog_validation.py enforces both the schema and
      this invariant before deploy.
"""

from __future__ import annotations

from typing import Any

try:
    from app.contracts import BucketInventory, GcsObjectInfo
except ImportError:
    from contracts import BucketInventory, GcsObjectInfo

# The root component id is fixed by the A2UI v0.9 protocol: it MUST be the
# literal string "root". The renderer looks up "root" to find where to start
# drawing; any other id leaves it with a valid but unreachable component tree.
ROOT_CARD_ID: str = "root"

# Remaining ids are free-form. Stable names keep the rendered tree predictable
# and make failures easy to locate in the Gemini Enterprise validation message,
# which reports the offending component id verbatim.
ROOT_COLUMN_ID: str = "inventory-column"


def _text(component_id: str, text: str, variant: str = "body") -> dict[str, Any]:
    """Build a schema-valid Text component."""
    return {
        "id": component_id,
        "component": "Text",
        "text": text,
        "variant": variant,
    }


def _format_object_line(obj: GcsObjectInfo) -> str:
    """Render a single object as a human-readable inventory line."""
    return f"• {obj.basename}  —  {obj.size_kib:,.1f} KiB  ({obj.content_type})"


def build_inventory_components(inventory: BucketInventory) -> list[dict[str, Any]]:
    """Build the full A2UI component list describing the live bucket inventory.

    The card reports what the agent actually observed in Cloud Storage this turn:
    bucket, region, object counts and per-file detail. When the scan failed we
    render the error instead of an empty list, so a permissions or connectivity
    problem is visible in the chat surface rather than silently looking like an
    empty bucket.
    """
    children: list[str] = []
    components: list[dict[str, Any]] = []

    def add(component: dict[str, Any]) -> None:
        """Append a component and register it as a child of the root column."""
        components.append(component)
        children.append(component["id"])

    add(_text("inv-title", "Petrophysics Data Inventory", "h3"))
    add(
        _text(
            "inv-location",
            f"gs://{inventory.bucket}  ·  region {inventory.region}",
            "caption",
        )
    )
    add({"id": "inv-divider", "component": "Divider"})

    if not inventory.ok:
        # Surface the real failure reason rather than an empty success state.
        add(_text("inv-error-heading", "Inventory scan failed", "h5"))
        add(_text("inv-error-detail", inventory.error or "Unknown error", "body"))
    elif inventory.total_objects == 0:
        add(_text("inv-empty", "No well log scans or LAS files found.", "body"))
    else:
        add(
            _text(
                "inv-summary",
                f"{len(inventory.scans)} scanned log(s) awaiting digitisation  ·  "
                f"{len(inventory.las_files)} digitised LAS file(s)  ·  "
                f"{inventory.total_bytes / 1024:,.1f} KiB total",
                "body",
            )
        )

        if inventory.scans:
            add(_text("inv-scans-heading", "Scanned well logs", "h5"))
            for idx, obj in enumerate(inventory.scans):
                add(_text(f"inv-scan-{idx}", _format_object_line(obj), "body"))

        if inventory.las_files:
            add(_text("inv-las-heading", "Digitised LAS files", "h5"))
            for idx, obj in enumerate(inventory.las_files):
                add(_text(f"inv-las-{idx}", _format_object_line(obj), "body"))
        else:
            add(
                _text(
                    "inv-las-none",
                    "No LAS files yet — run digitisation to produce CWLS LAS 2.0 output.",
                    "caption",
                )
            )

    # Column wraps every element; Card holds exactly one child, per the catalog.
    column = {
        "id": ROOT_COLUMN_ID,
        "component": "Column",
        "children": children,
    }
    card = {
        "id": ROOT_CARD_ID,
        "component": "Card",
        "child": ROOT_COLUMN_ID,
    }

    # The Card carries id "root" and is emitted first: A2UI v0.9 requires a
    # component with that id to exist, and the SDK requires it to lead the list.
    return [card, column, *components]
