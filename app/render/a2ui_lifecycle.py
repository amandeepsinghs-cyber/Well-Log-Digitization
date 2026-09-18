"""A2UI v0.9 lifecycle message builders for Gemini Enterprise chat surfaces.

In : Surface identifiers, UI component trees, and data model dictionaries.
Out: A2uiMessage contract objects ready to be wrapped by a2ui_envelope.
Rule: Pure functions only. Adheres strictly to A2UI v0.9 schema conventions.
"""

from typing import Any

try:
    from app.contracts import A2uiCatalogVersion, A2uiMessage
except ImportError:
    from contracts import A2uiCatalogVersion, A2uiMessage


# Default catalog schema identifier registered in Gemini Enterprise Angular renderer.
# Supports standard Material 3 components and the GE custom catalog (VegaChart, Canvas).
DEFAULT_GE_CATALOG_ID: str = (
    "https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json"
)


def build_create_surface(
    surface_id: str,
    catalog_id: str = DEFAULT_GE_CATALOG_ID
) -> A2uiMessage:
    """Build a 'createSurface' message to initialize a new visual surface in chat.

    In A2UI v0.9, 'createSurface' must precede component updates for a given surfaceId.
    Reusing an existing surfaceId without recreation causes silent drops.
    """
    if not surface_id:
        raise ValueError("surface_id cannot be empty when creating a surface.")

    return A2uiMessage(
        message_type="createSurface",
        surface_id=surface_id,
        payload={
            "catalogId": catalog_id,
        },
        catalog_version=A2uiCatalogVersion.V0_9,
    )


def build_update_components(
    surface_id: str,
    components: list[dict[str, Any]]
) -> A2uiMessage:
    """Build an 'updateComponents' message to declare or replace the UI hierarchy.

    In A2UI v0.9, components is a list of declarative component dictionaries.
    Each component specifies its component type (e.g. 'Card', 'Text', 'VegaChart')
    and references its children or data model pointers.
    """
    if not surface_id:
        raise ValueError("surface_id cannot be empty when updating components.")
    if not components:
        raise ValueError("components list cannot be empty in updateComponents.")

    return A2uiMessage(
        message_type="updateComponents",
        surface_id=surface_id,
        payload={
            "components": components,
        },
        catalog_version=A2uiCatalogVersion.V0_9,
    )


def build_update_data_model(
    surface_id: str,
    value: dict[str, Any],
    path: str | None = None
) -> A2uiMessage:
    """Build an 'updateDataModel' message to bind bulk data to the surface.

    Decoupling the data model from the component tree is an essential pattern
    for well logs. A 300-foot interval contains thousands of curve samples (~100 KB).
    Sending this data in the data model allows components (like VegaChart) to bind to
    it via JSON Pointer paths (e.g. {'path': '/chartSpec'}) without bloating the UI tree.

    THE PAYLOAD KEY IS 'value', NOT 'data'. This is the one field name in the
    lifecycle that does not read the way you would guess, and getting it wrong
    is not a silent failure but an unhelpful one: Gemini Enterprise replaces the
    whole surface with "Expected undefined, received undefined /updateDataModel".
    The spec's own example uses 'value'
    (third_party/a2ui/specification/v0_9/docs/a2ui_protocol.md, updateDataModel),
    as does Google's own builder, which names the Kotlin parameter 'contents'
    while emitting the key 'value'
    (java/com/google/frameworks/client/response/materialization/a2ui/v0_9/
     MessageBuilder.kt:137). Trust the emitted key, not the parameter name.

    Omitting 'path' replaces the entire data model for the surface, which is
    what we want: a surface id is new for every response, so there is never a
    previous model to merge into and an upsert into a path that does not exist
    yet is one more thing that can go wrong.
    """
    if not surface_id:
        raise ValueError("surface_id cannot be empty when updating data model.")
    if not isinstance(value, dict):
        raise TypeError(f"value must be a dictionary, got {type(value).__name__}.")

    payload: dict[str, Any] = {"value": value}
    if path:
        # If an explicit JSON Pointer path is specified, scope the update under that path.
        payload["path"] = path

    return A2uiMessage(
        message_type="updateDataModel",
        surface_id=surface_id,
        payload=payload,
        catalog_version=A2uiCatalogVersion.V0_9,
    )


def build_delete_surface(surface_id: str) -> A2uiMessage:
    """Build a 'deleteSurface' message to tear down an active surface canvas.

    Used when a user closes a panel or when the conversation transitions to a new state
    that invalidates previous inspection surfaces.
    """
    if not surface_id:
        raise ValueError("surface_id cannot be empty when deleting a surface.")

    return A2uiMessage(
        message_type="deleteSurface",
        surface_id=surface_id,
        payload={},
        catalog_version=A2uiCatalogVersion.V0_9,
    )
