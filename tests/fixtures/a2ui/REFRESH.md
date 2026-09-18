# Pinned A2UI v0.9 Schemas

These files are **verbatim copies** of the schemas the Gemini Enterprise Angular
renderer validates agent payloads against in the browser. They are pinned here so
`tests/unit/test_a2ui_catalog_validation.py` can reproduce that validation locally
in ~2 seconds instead of requiring a ~4 minute deploy to discover a schema error.

| File | Upstream source |
|---|---|
| `ge_composite_catalog.json` | `https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json` |
| `common_types.json` | `https://a2ui.org/specification/v0_9/common_types.json` |

## Refreshing

Re-download when Gemini Enterprise ships a new catalog, then re-run the tests to
surface any breaking component changes before they reach production:

```bash
curl -s "https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json" \
  -o tests/fixtures/a2ui/ge_composite_catalog.json
curl -s "https://a2ui.org/specification/v0_9/common_types.json" \
  -o tests/fixtures/a2ui/common_types.json

uv run pytest tests/unit/test_a2ui_catalog_validation.py -v
```

## Constraints these fixtures enforce

Discovered the hard way, from a production `Validation failed for component 'Card'`
error in the Gemini Enterprise chat surface:

- `Card` takes exactly one **`child`** (a component id string). It does **not**
  accept `children`, and it has no `appearance` property. Wrap multiple elements
  in a `Column` or `Row`.
- `Text` styling uses **`variant`** (`h1`–`h5`, `caption`, `body`), not `usageHint`.
- All components require an `id`.
- Schemas set `unevaluatedProperties: false`, so **any** unrecognised key is a
  hard validation failure — there is no silent tolerance for extra fields.
