"""Pin the A2UI v0.9 lifecycle envelope against the specification's own examples.

This is the guard that was missing. tests/unit/test_a2ui_catalog_validation.py
validates COMPONENTS against the catalog's JSON Schema, which is thorough — but
the catalog says nothing about the messages that carry those components. The
lifecycle envelope had no schema and therefore no test, and an updateDataModel
that used the key 'data' instead of 'value' shipped to production, where it
failed with "Expected undefined, received undefined /updateDataModel" and took
the whole surface down with it.

Every expected shape below is copied from the example stream in the published
specification, https://a2ui.org/specification/v0.9-a2ui/ ("Example Stream"),
which is reproduced verbatim in _SPEC_EXAMPLE_STREAM so a reader can check these
assertions without leaving the file.
"""

from __future__ import annotations

import json

import pytest

from app.render.a2ui_envelope import (
    A2A_DATA_PART_CLOSE_TAG,
    A2A_DATA_PART_OPEN_TAG,
    wrap_a2ui_part,
)
from app.render.a2ui_lifecycle import (
    DEFAULT_GE_CATALOG_ID,
    build_create_surface,
    build_delete_surface,
    build_update_components,
    build_update_data_model,
)

# The specification's example stream, verbatim. Four messages, one per line.
# Kept as text rather than as parsed dicts so that it is obviously a quotation
# and not something a previous edit could have quietly adjusted to match the
# code.
_SPEC_EXAMPLE_STREAM = [
    '{"version": "v0.9", "createSurface":{"surfaceId":"contact_form_1",'
    '"catalogId":"https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"}}',
    '{"version": "v0.9", "updateComponents":{"surfaceId":"contact_form_1",'
    '"components":[{"id":"root","component":"Card","child":"form_container"}]}}',
    '{"version": "v0.9", "updateDataModel":{"surfaceId":"contact_form_1",'
    '"path":"/contact","value":{"firstName":"John"}}}',
    '{"version": "v0.9", "deleteSurface":{"surfaceId":"contact_form_1"}}',
]

_SURFACE = "surface-abc12345"


def _body(message) -> dict:
    """The JSON the renderer actually receives, unwrapped from the ADK Part."""
    raw = wrap_a2ui_part(message).inline_data.data.decode("utf-8")
    inner = raw[len(A2A_DATA_PART_OPEN_TAG) : -len(A2A_DATA_PART_CLOSE_TAG)]
    return json.loads(inner)["data"]


def test_the_spec_example_stream_names_the_four_lifecycle_messages() -> None:
    """Guards the quotation above against being edited to fit the code.

    If this fails, the examples have been altered and every other assertion in
    this file is measuring itself.
    """
    keys = [
        next(k for k in json.loads(line) if k != "version")
        for line in _SPEC_EXAMPLE_STREAM
    ]
    assert keys == [
        "createSurface",
        "updateComponents",
        "updateDataModel",
        "deleteSurface",
    ]


def test_create_surface_matches_the_spec_shape() -> None:
    body = _body(build_create_surface(surface_id=_SURFACE))
    expected_keys = set(json.loads(_SPEC_EXAMPLE_STREAM[0])["createSurface"])

    assert body["version"] == "v0.9"
    assert set(body["createSurface"]) == expected_keys
    assert body["createSurface"]["surfaceId"] == _SURFACE
    assert body["createSurface"]["catalogId"] == DEFAULT_GE_CATALOG_ID


def test_update_components_matches_the_spec_shape() -> None:
    components = [{"id": "root", "component": "Card", "child": "col"}]
    body = _body(
        build_update_components(surface_id=_SURFACE, components=components)
    )
    expected_keys = set(json.loads(_SPEC_EXAMPLE_STREAM[1])["updateComponents"])

    assert set(body["updateComponents"]) == expected_keys
    assert body["updateComponents"]["components"] == components


def test_update_data_model_carries_value_not_data() -> None:
    """THE REGRESSION. 'data' renders nothing and reports a useless error.

    The key is 'value'. Google's own builder confirms it: MessageBuilder.kt:137
    emits add("value", contents) — the Kotlin parameter is called 'contents',
    which is what makes this worth pinning rather than remembering.
    """
    body = _body(build_update_data_model(surface_id=_SURFACE, value={"spec": {"a": 1}}))
    message = body["updateDataModel"]

    assert "value" in message, "updateDataModel must use 'value'"
    assert "data" not in message, "'data' is not a field the renderer knows"
    assert message["value"] == {"spec": {"a": 1}}


def test_update_data_model_omits_path_when_replacing_the_whole_model() -> None:
    """No path means replace everything, which is what a fresh surface wants.

    An empty 'path' would be a JSON Pointer to the root by a different route and
    is not what the spec's examples show, so it must be absent rather than "".
    """
    message = _body(build_update_data_model(surface_id=_SURFACE, value={"spec": 1}))[
        "updateDataModel"
    ]
    assert set(message) == {"surfaceId", "value"}


def test_update_data_model_scopes_to_a_path_when_one_is_given() -> None:
    body = _body(
        build_update_data_model(
            surface_id=_SURFACE, value={"firstName": "John"}, path="/contact"
        )
    )
    expected_keys = set(json.loads(_SPEC_EXAMPLE_STREAM[2])["updateDataModel"])

    assert set(body["updateDataModel"]) == expected_keys
    assert body["updateDataModel"]["path"] == "/contact"


def test_delete_surface_matches_the_spec_shape() -> None:
    body = _body(build_delete_surface(surface_id=_SURFACE))
    expected_keys = set(json.loads(_SPEC_EXAMPLE_STREAM[3])["deleteSurface"])

    assert set(body["deleteSurface"]) == expected_keys


def test_every_lifecycle_message_declares_the_protocol_version() -> None:
    """A message without a version is rejected before the renderer sees it."""
    messages = [
        build_create_surface(surface_id=_SURFACE),
        build_update_components(surface_id=_SURFACE, components=[{"id": "root"}]),
        build_update_data_model(surface_id=_SURFACE, value={"spec": 1}),
        build_delete_surface(surface_id=_SURFACE),
    ]
    assert [_body(m)["version"] for m in messages] == ["v0.9"] * 4


def test_a_non_dict_data_model_is_refused_loudly() -> None:
    """A list at the root of the data model has no JSON Pointer keys to bind to."""
    with pytest.raises(TypeError, match="value must be a dictionary"):
        build_update_data_model(surface_id=_SURFACE, value=[1, 2, 3])  # type: ignore[arg-type]
