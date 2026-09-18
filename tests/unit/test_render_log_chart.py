"""Unit tests for the render path: LAS document -> tracks -> Vega-Lite -> A2UI.

The emphasis is on the failures that produce a chart which looks fine and is
wrong, or no chart at all with nothing in the logs, because those are the ones
neither the compiler nor the renderer will report:

  - curves drawn in value order instead of depth order
  - a depth axis merged away by shared layer axis resolution
  - the zoom parameter duplicated across tracks
  - the Vega spec inlined into a component that cannot hold an object
  - a component tree with no 'root' anchor

Compilation against the real Vega-Lite engine is covered by
scripts/compile_check.py, which needs a 32 MB binary and belongs in the
pre-deploy gate rather than in the unit suite.
"""

from __future__ import annotations

import json

import pytest

from app.contracts import (
    CWLS_NULL_VALUE,
    CurveSample,
    CurveTrace,
    LasDocument,
    ScaleType,
)
from app.render.a2ui_emit import build_log_components, build_log_surface
from app.render.a2ui_envelope import A2A_DATA_PART_CLOSE_TAG, A2A_DATA_PART_OPEN_TAG
from app.render.track_layout import build_log_plot
from app.render.vega_spec import ZOOM_PARAM, build_log_spec, chart_height
from app.render.vega_track import DATA_NAME, DEPTH_FIELD

_DEPTHS = [7000.0, 7000.5, 7001.0, 7001.5]


def _trace(mnemonic: str, values: list[float], unit: str = "") -> CurveTrace:
    """A curve sampled on the shared depth index."""
    return CurveTrace(
        mnemonic=mnemonic,
        unit=unit,
        description=mnemonic,
        samples=[
            CurveSample(depth=depth, value=value, confidence=1.0)
            for depth, value in zip(_DEPTHS, values, strict=True)
        ],
    )


def _document(*traces: CurveTrace) -> LasDocument:
    return LasDocument(
        well_name="TEST_WELL",
        depth_min=_DEPTHS[0],
        depth_max=_DEPTHS[-1],
        depth_step=0.5,
        depth_units="FT",
        curves=list(traces),
    )


def _three_track_document() -> LasDocument:
    """One curve on each conventional track, plus the shared resistivity trio."""
    return _document(
        _trace("GR", [40.0, 45.0, 50.0, 55.0], "GAPI"),
        _trace("SP", [-10.0, -12.0, -14.0, -16.0], "MV"),
        _trace("RXO", [0.5, 0.6, 0.7, 0.8], "OHMM"),
        _trace("ILM", [1.0, 1.1, 1.2, 1.3], "OHMM"),
        _trace("ILD", [2.0, 2.1, 2.2, 2.3], "OHMM"),
        _trace("NPHI", [0.20, 0.21, 0.22, 0.23], "V/V"),
        _trace("RHOB", [2.40, 2.41, 2.42, 2.43], "G/C3"),
    )


# --------------------------------------------------------------------------
# track_layout — which curve goes where
# --------------------------------------------------------------------------


def test_curves_land_on_their_conventional_tracks() -> None:
    plot = build_log_plot(_three_track_document())

    assert [track.number for track in plot.tracks] == [1, 2, 3]
    assert [c.mnemonic for c in plot.tracks[0].curves] == ["GR", "SP"]
    assert [c.mnemonic for c in plot.tracks[1].curves] == ["RXO", "ILM", "ILD"]
    assert [c.mnemonic for c in plot.tracks[2].curves] == ["NPHI", "RHOB"]


def test_resistivity_track_is_logarithmic() -> None:
    """Reading a log track as linear misestimates resistivity by a decade."""
    plot = build_log_plot(_three_track_document())
    resistivity = plot.tracks[1]

    assert all(c.scale_type is ScaleType.LOGARITHMIC for c in resistivity.curves)
    assert all(c.scale_type is ScaleType.LINEAR for c in plot.tracks[0].curves)


def test_neutron_scale_stays_reversed() -> None:
    """NPHI is printed high-to-low; flipping it inverts the crossover reading."""
    plot = build_log_plot(_three_track_document())
    nphi = next(c for c in plot.tracks[2].curves if c.mnemonic == "NPHI")

    assert nphi.display_min > nphi.display_max


def test_empty_tracks_are_skipped() -> None:
    plot = build_log_plot(_document(_trace("GR", [1.0, 2.0, 3.0, 4.0])))

    assert [track.number for track in plot.tracks] == [1]


def test_unknown_mnemonic_is_dropped_not_guessed() -> None:
    """An unrecognised curve has no known scale, so it cannot be drawn safely."""
    plot = build_log_plot(
        _document(
            _trace("GR", [1.0, 2.0, 3.0, 4.0]),
            _trace("WIDGET", [9.0, 9.0, 9.0, 9.0]),
        )
    )

    drawn = [c.mnemonic for track in plot.tracks for c in track.curves]
    assert drawn == ["GR"]


def test_document_with_no_known_curves_raises() -> None:
    with pytest.raises(ValueError, match="SPWLA mnemonic table"):
        build_log_plot(_document(_trace("WIDGET", [1.0, 2.0, 3.0, 4.0])))


def test_null_becomes_none_so_the_line_breaks() -> None:
    """-999.25 plotted literally would drag the curve off the track and back."""
    plot = build_log_plot(
        _document(_trace("GR", [40.0, CWLS_NULL_VALUE, 50.0, 55.0]))
    )

    assert plot.tracks[0].curves[0].values == (40.0, None, 50.0, 55.0)


def test_curves_on_different_depth_indexes_are_rejected() -> None:
    """A silent misalignment would still draw a perfectly convincing plot."""
    short = CurveTrace(
        mnemonic="SP",
        unit="MV",
        description="SP",
        samples=[CurveSample(depth=7000.0, value=1.0, confidence=1.0)],
    )
    with pytest.raises(ValueError, match="one depth index"):
        build_log_plot(_document(_trace("GR", [1.0, 2.0, 3.0, 4.0]), short))


# --------------------------------------------------------------------------
# vega_spec / vega_track — the structural rules Vega enforces
# --------------------------------------------------------------------------


def test_spec_is_an_hconcat_with_one_child_per_track() -> None:
    """facet cannot express a log: it forces one x-scale type across panels."""
    spec = build_log_spec(build_log_plot(_three_track_document()))

    assert "hconcat" in spec
    assert "facet" not in spec
    assert len(spec["hconcat"]) == 3


def test_every_concat_child_has_a_numeric_width() -> None:
    """Gemini Enterprise rewrites top-level sizing to 'container', which
    Vega-Lite rejects on a concat view unless the children are sized."""
    spec = build_log_spec(build_log_plot(_three_track_document()))

    for child in spec["hconcat"]:
        assert isinstance(child["width"], int)
        assert isinstance(child["height"], int)


def test_depth_scale_is_shared_across_tracks() -> None:
    spec = build_log_spec(build_log_plot(_three_track_document()))

    assert spec["resolve"]["scale"]["y"] == "shared"


def test_exactly_one_zoom_parameter_in_the_whole_spec() -> None:
    """A duplicated Vega signal name is a hard parse failure."""
    spec = build_log_spec(build_log_plot(_three_track_document()))

    params = [
        param
        for child in spec["hconcat"]
        for layer in child["layer"]
        for param in layer.get("params", [])
    ]
    assert [p["name"] for p in params] == [ZOOM_PARAM]
    assert params[0]["bind"] == "scales"
    # Bound to depth only: rescaling a calibrated value axis by dragging would
    # silently change what the curve reads.
    assert params[0]["select"]["encodings"] == ["y"]


def test_exactly_one_depth_axis_is_drawn_and_it_is_on_the_first_track() -> None:
    """Every layer needs the depth SCALE; only one may declare the AXIS."""
    spec = build_log_spec(build_log_plot(_three_track_document()))

    per_track = [
        [layer["encoding"]["y"].get("axis") for layer in child["layer"]]
        for child in spec["hconcat"]
    ]
    assert sum(axis is not None for axes in per_track for axis in axes) == 1
    assert per_track[0][0] is not None

    # The scale survives on every layer, or the curves stop lining up.
    for child in spec["hconcat"]:
        for layer in child["layer"]:
            assert layer["encoding"]["y"]["scale"]["reverse"] is True


def test_depth_axis_resolves_independently_within_a_track() -> None:
    """Shared axis resolution merges the owner's axis with the others' null
    and the depth column disappears from the finished chart."""
    spec = build_log_spec(build_log_plot(_three_track_document()))

    assert spec["hconcat"][0]["resolve"]["axis"]["y"] == "independent"


def test_each_scale_gets_one_header_naming_every_curve_on_it() -> None:
    spec = build_log_spec(build_log_plot(_three_track_document()))

    titles = [
        layer["encoding"]["x"]["axis"]["title"]
        for child in spec["hconcat"]
        for layer in child["layer"]
        if layer["encoding"]["x"].get("axis")
    ]
    # Two on track 1, one shared by the resistivity trio, two on track 3.
    assert len(titles) == 5
    resistivity = [t for t in titles if "OHMM" in t]
    assert len(resistivity) == 1
    for mnemonic in ("RXO", "ILM", "ILD"):
        assert mnemonic in resistivity[0]


def test_curves_are_ordered_by_depth_not_by_reading() -> None:
    """Left alone, Vega-Lite sorts a line by x. On a log that is the reading,
    and the curve renders as a zigzag between the extremes of the track."""
    spec = build_log_spec(build_log_plot(_three_track_document()))

    for child in spec["hconcat"]:
        for layer in child["layer"]:
            assert layer["encoding"]["order"]["field"] == DEPTH_FIELD


def test_marks_are_clipped() -> None:
    """Unclipped curves overdraw the neighbouring track once zoomed."""
    spec = build_log_spec(build_log_plot(_three_track_document()))

    for child in spec["hconcat"]:
        for layer in child["layer"]:
            assert layer["mark"]["clip"] is True


def test_every_layer_carries_a_tooltip() -> None:
    spec = build_log_spec(build_log_plot(_three_track_document()))

    for child in spec["hconcat"]:
        for layer in child["layer"]:
            fields = [t["field"] for t in layer["encoding"]["tooltip"]]
            assert DEPTH_FIELD in fields
            assert layer["encoding"]["x"]["field"] in fields


def test_data_is_inline_and_never_fetched() -> None:
    """Safe Vega disables the URL loader and the page CSP blocks the fetch."""
    spec = build_log_spec(build_log_plot(_three_track_document()))

    assert DATA_NAME in spec["datasets"]
    assert len(spec["datasets"][DATA_NAME]) == len(_DEPTHS)
    assert "url" not in json.dumps(spec["datasets"])

    for child in spec["hconcat"]:
        for layer in child["layer"]:
            assert layer["data"] == {"name": DATA_NAME}


def test_one_row_per_depth_carries_every_curve() -> None:
    spec = build_log_spec(build_log_plot(_three_track_document()))
    first = spec["datasets"][DATA_NAME][0]

    assert first[DEPTH_FIELD] == 7000.0
    assert set(first) == {DEPTH_FIELD, "GR", "SP", "RXO", "ILM", "ILD", "NPHI", "RHOB"}


def test_null_survives_into_the_dataset_as_json_null() -> None:
    plot = build_log_plot(_document(_trace("GR", [40.0, CWLS_NULL_VALUE, 50.0, 55.0])))
    rows = build_log_spec(plot)["datasets"][DATA_NAME]

    assert rows[1]["GR"] is None
    assert '"GR":null' in json.dumps(rows, separators=(",", ":"))


def test_chart_height_clears_the_tracks_and_their_headers() -> None:
    """The host crops to the declared height with no scrollbar and no warning."""
    plot = build_log_plot(_three_track_document())
    child_height = build_log_spec(plot)["hconcat"][0]["height"]

    assert chart_height(plot) > child_height


# --------------------------------------------------------------------------
# a2ui_emit — the wire contract
# --------------------------------------------------------------------------


def _payloads(parts: list) -> list[dict]:
    """Unwrap the <a2a_datapart_json> envelopes back into message dicts."""
    out = []
    for part in parts:
        raw = part.inline_data.data.decode("utf-8")
        assert raw.startswith(A2A_DATA_PART_OPEN_TAG)
        assert raw.endswith(A2A_DATA_PART_CLOSE_TAG)
        out.append(
            json.loads(raw[len(A2A_DATA_PART_OPEN_TAG) : -len(A2A_DATA_PART_CLOSE_TAG)])
        )
    return out


def test_three_messages_go_out_as_three_separate_parts() -> None:
    """Bundled into one Part they are accepted and then silently ignored."""
    plot = build_log_plot(_three_track_document())
    parts = build_log_surface(plot, surface_id="surface-test")

    assert len(parts) == 3
    messages = _payloads(parts)
    kinds = [next(k for k in m["data"] if k != "version") for m in messages]
    assert kinds == ["createSurface", "updateDataModel", "updateComponents"]


def test_data_model_is_sent_before_the_components_that_read_it() -> None:
    """A component bound to a path that does not exist yet renders empty."""
    plot = build_log_plot(_three_track_document())
    messages = _payloads(build_log_surface(plot, surface_id="surface-test"))

    order = [next(k for k in m["data"] if k != "version") for m in messages]
    assert order.index("updateDataModel") < order.index("updateComponents")


def test_the_spec_travels_in_the_data_model_not_the_component() -> None:
    """VegaChart.spec is a DynamicValue, which has no object variant: an
    inline specification fails catalog validation outright."""
    plot = build_log_plot(_three_track_document())
    messages = _payloads(build_log_surface(plot, surface_id="surface-test"))

    data_model = next(m["data"]["updateDataModel"] for m in messages if "updateDataModel" in m["data"])
    components = next(m["data"]["updateComponents"] for m in messages if "updateComponents" in m["data"])

    chart = next(c for c in components["components"] if c["component"] == "VegaChart")
    assert chart["spec"] == {"path": "/spec"}

    # The outer ["data"] is the ADK transport envelope; the inner key is the
    # A2UI field, and A2UI names it "value".
    pointed_at = data_model["value"]["spec"]
    assert "hconcat" in pointed_at


def test_component_tree_is_anchored_on_root() -> None:
    """A rootless tree validates, renders nothing, and logs nothing."""
    components = build_log_components(build_log_plot(_three_track_document()))

    assert components[0]["id"] == "root"


def test_component_ids_are_unique_and_children_resolve() -> None:
    components = build_log_components(build_log_plot(_three_track_document()))
    ids = [c["id"] for c in components]

    assert len(ids) == len(set(ids))
    for comp in components:
        if "child" in comp:
            assert comp["child"] in ids
        for child in comp.get("children", []):
            assert child in ids


def test_chart_component_declares_its_height() -> None:
    """The component defaults to 290 px and crops a 560 px track silently."""
    plot = build_log_plot(_three_track_document())
    chart = next(
        c for c in build_log_components(plot) if c["component"] == "VegaChart"
    )

    assert chart["height"] == chart_height(plot)


def test_an_oversized_payload_is_refused_rather_than_truncated() -> None:
    """Truncation is reported nowhere: the well simply arrives half missing."""
    long_depths = [7000.0 + 0.5 * i for i in range(20000)]
    trace = CurveTrace(
        mnemonic="GR",
        unit="GAPI",
        description="Gamma Ray",
        samples=[
            CurveSample(depth=d, value=123.4567, confidence=1.0) for d in long_depths
        ],
    )
    document = LasDocument(
        well_name="LONG_WELL",
        depth_min=long_depths[0],
        depth_max=long_depths[-1],
        depth_step=0.5,
        depth_units="FT",
        curves=[trace],
    )

    with pytest.raises(ValueError, match="inline A2UI limit"):
        build_log_surface(build_log_plot(document), surface_id="surface-test")
