"""Plot the digitised curves beside the scan they came from, for the eye to judge.

In : a scanned log sheet.
Out: a PNG with the source scan on the left and the traced curves redrawn on
     their own axes to the right of it, at matching depths.
Rule: a development tool, not part of the product. Extraction accuracy cannot
      be judged from numbers — a curve can be complete, smooth, in range and
      still be the wrong curve — and this is the cheapest way to look at it.
      Deliberately matplotlib and deliberately not Vega, A2UI or Gemini
      Enterprise: none of those are needed to answer "did it work", and
      requiring them would make the answer cost a deployment.

      The agent never calls this. Delete it once the product renderer exists,
      or keep it as a dev tool; nothing depends on it either way.

Usage:
    set -a; source .env; set +a      # the header read needs Vertex credentials
    uv run python scripts/qc_plot.py [IMAGE] [-o OUTPUT.png]
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")  # No display on a Cloudtop; render straight to a file.
import matplotlib.pyplot as plt  # noqa: E402 - must follow the backend choice

from app.calibrate.depth_axis import fit_depth_axis
from app.contracts import CWLS_NULL_VALUE, ScaleType
from app.detect.depth_ticks import detect_depth_ticks
from app.detect.gridlines import detect_gridlines
from app.detect.regions import detect_regions
from app.header.ocr_header import read_sheet_text
from app.header.parse_header import parse_depth_label, parse_depth_unit, parse_scales
from app.ingest.load_image import load_image, to_array
from app.integration.pipeline_digitise import extract_curves
from app.las.mnemonics import SPWLA_MNEMONICS

logger = logging.getLogger(__name__)

_LINE_WIDTH = 0.9


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "image",
        nargs="?",
        default=str(Path(__file__).parents[2] / "Well_log_schlum.jpg"),
        help="the scanned log sheet to digitise and plot",
    )
    parser.add_argument("-o", "--output", default="qc_plot.png")
    arguments = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    source = Path(arguments.image)
    image = load_image(source.read_bytes())

    # Geometry, then the one header read, then the calibrations — the same
    # order the agent's own tool uses.
    grid = detect_gridlines(image)
    layout = detect_regions(image, grid)
    ticks = detect_depth_ticks(image, layout, grid)

    text = read_sheet_text(image, layout, ticks)
    axes = parse_scales(text, layout)
    depth_units = parse_depth_unit(text.depth_header)
    depths = tuple(parse_depth_label(label) for label in text.depth_labels)
    depth = fit_depth_axis(ticks, depths, depth_units, layout)

    curves = extract_curves(image, layout, grid, depth, axes)
    _draw(source, image, layout, axes, curves, Path(arguments.output))

    print(f"Wrote {arguments.output}")
    for mnemonic, trace in curves.items():
        print(
            f"  {mnemonic:5s} {len(trace.samples):4d} samples  "
            f"{trace.coverage_fraction:5.1%} observed  "
            f"confidence {trace.mean_confidence:.2f}"
        )


def _draw(source, image, layout, axes, curves, output: Path) -> None:
    """Source scan on the left, one panel per track to the right of it."""
    tracks = layout.tracks
    figure = plt.figure(figsize=(4 + 3 * len(tracks), 11))
    columns = figure.add_gridspec(1, 1 + len(tracks), width_ratios=[1.6] + [1] * len(tracks))

    scan = figure.add_subplot(columns[0, 0])
    scan.imshow(to_array(image))
    scan.set_title(source.name, fontsize=9)
    scan.axis("off")

    for position, track in enumerate(tracks, start=1):
        panel = figure.add_subplot(columns[0, position])
        on_track = [a for a in axes if a.track_bounds.track_name == track.name]
        _draw_track(figure, panel, track, on_track, curves, first=position == 1)

    figure.suptitle("Digitised curves against the source scan", fontsize=11)
    figure.tight_layout()
    figure.savefig(output, dpi=130)


def _draw_track(figure, panel, track, on_track, curves, first: bool) -> None:
    """One track, with a separate top axis per curve because scales differ.

    Two curves on the same printed track almost never share a scale — gamma ray
    reads 0 to 150 where the curve beside it reads -80 to 20 — so each gets its
    own axis, exactly as the printed header does.
    """
    panel.set_title(track.name, fontsize=9)
    panel.set_yticks([])
    panel.set_xticks([])

    for order, axis in enumerate(on_track):
        trace = curves.get(axis.mnemonic)
        if trace is None:
            continue
        spec = SPWLA_MNEMONICS[axis.mnemonic]

        # A separate x-axis per curve, stacked upward so the labels do not
        # overlap, in the curve's own printed colour.
        overlay = panel.twiny()
        overlay.spines["top"].set_position(("axes", 1.0 + 0.055 * order))
        overlay.spines["top"].set_color(spec.colour)
        overlay.tick_params(axis="x", colors=spec.colour, labelsize=7)

        if axis.scale_type is ScaleType.LOGARITHMIC:
            overlay.set_xscale("log")
        overlay.set_xlim(axis.value_min, axis.value_max)

        depths = np.array([sample.depth for sample in trace.samples])
        # NULL becomes NaN so matplotlib leaves a visible break rather than
        # drawing a line through an interval nobody measured.
        values = np.array(
            [
                np.nan if sample.value == CWLS_NULL_VALUE else sample.value
                for sample in trace.samples
            ]
        )

        overlay.plot(
            values,
            depths,
            color=spec.colour,
            linewidth=_LINE_WIDTH,
            linestyle=(0, spec.dash) if spec.dash else "solid",
        )
        overlay.set_xlabel(
            f"{axis.mnemonic}  {axis.value_min:g} to {axis.value_max:g} {axis.unit}",
            fontsize=7,
            color=spec.colour,
        )
        # Depth increases downward on every well log ever printed.
        overlay.set_ylim(depths.max(), depths.min())

        if first and order == 0:
            overlay.set_ylabel("Depth")
            overlay.tick_params(axis="y", labelsize=7, colors="black")


if __name__ == "__main__":
    main()
