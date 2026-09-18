"""Compile the log spec with the real Vega-Lite engine and render it headlessly.

In : a LAS file on disk.
Out: an exit code, a report, and PNGs of what Vega actually drew.
Rule: a development gate, not part of the product. The agent never calls it.

Why this exists: a Vega-Lite specification is a large nested dict, and every
way of getting it wrong looks exactly like every way of getting it right. Three
of the failure modes this project has already hit are invisible on inspection —
duplicated axis declarations, a selection parameter placed one level too high,
and container sizing applied to a concat view. Each costs a four-minute deploy
to discover from a red box in chat. vl-convert runs the same Vega-Lite compiler
the browser runs, locally, in about a second.

It checks the spec TWICE:

  1. AS AUTHORED — what we hand to Gemini Enterprise.
  2. SIZE-REWRITTEN — with width and height forced to 'container' at the top
     level, which is what Gemini Enterprise does to a spec before rendering it.
     Vega-Lite rejects container sizing on a concat view, so this pass is the
     one that proves the fixed per-track widths are doing their job.

Rendering to PNG as well as compiling is deliberate. A spec can compile cleanly
and still draw nothing, or draw the scale headers off the top of the canvas.

Usage:
    uv run python scripts/compile_check.py [LAS_FILE] [-o OUTPUT_DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import vl_convert as vlc

from app.las.parse_las import parse_las
from app.render.track_layout import build_log_plot
from app.render.vega_spec import build_log_spec

# Gemini Enterprise truncates an inline A2UI data payload near this size. The
# spec carries its data inline because Safe Vega cannot fetch a URL, so the
# whole specification has to stay under it.
_PAYLOAD_LIMIT_BYTES: int = 100 * 1024

_DEFAULT_LAS = Path(__file__).parent.parent / "tests" / "fixtures" / "las" / "SCAFFOLD-TEST-0001.las"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "las",
        nargs="?",
        default=str(_DEFAULT_LAS),
        help="the LAS file to build a spec from",
    )
    parser.add_argument("-o", "--output-dir", default="compile_check_out")
    arguments = parser.parse_args()

    source = Path(arguments.las)
    output_dir = Path(arguments.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    document = parse_las(source.read_bytes())
    plot = build_log_plot(document)
    spec = build_log_spec(plot)

    print(f"LAS      : {source}")
    print(f"Well     : {plot.well_name}")
    print(f"Tracks   : {[t.number for t in plot.tracks]}")
    print(
        "Curves   : "
        + ", ".join(c.mnemonic for t in plot.tracks for c in t.curves)
    )
    print(f"Samples  : {len(plot.depths):,}")

    payload = json.dumps(spec, separators=(",", ":")).encode("utf-8")
    headroom = _PAYLOAD_LIMIT_BYTES - len(payload)
    print(
        f"Payload  : {len(payload) / 1024:,.1f} KiB "
        f"({'OK' if headroom > 0 else 'OVER'}, "
        f"{headroom / 1024:+,.1f} KiB against the {_PAYLOAD_LIMIT_BYTES // 1024} KiB limit)"
    )

    (output_dir / "spec.json").write_bytes(json.dumps(spec, indent=2).encode("utf-8"))

    ok = _check("as-authored", spec, output_dir / "as_authored.png")

    # Gemini Enterprise injects container sizing at the top level before
    # handing the spec to Vega-Lite. Reproduce that exactly.
    rewritten = {**spec, "width": "container", "height": "container"}
    ok = _check("size-rewritten", rewritten, output_dir / "size_rewritten.png") and ok

    if not ok:
        print("\nFAILED")
        return 1

    print(f"\nPASSED — output in {output_dir}/")
    return 0


def _check(label: str, spec: dict[str, Any], png_path: Path) -> bool:
    """Compile the spec to Vega and render it, reporting what happened.

    Compilation catches structural errors; rendering catches the ones that only
    appear when marks are actually placed, such as an axis offset past the edge
    of the canvas.
    """
    try:
        vega = vlc.vegalite_to_vega(spec)
    except Exception as exc:
        print(f"\n[{label}] COMPILE FAILED\n  {type(exc).__name__}: {exc}")
        return False

    signals = [s.get("name") for s in vega.get("signals", [])]
    marks = _count_marks(vega)

    try:
        png_path.write_bytes(vlc.vegalite_to_png(spec, scale=2))
    except Exception as exc:
        print(f"\n[{label}] RENDER FAILED\n  {type(exc).__name__}: {exc}")
        return False

    if marks == 0:
        # A spec that compiles to no marks renders a blank panel and reports no
        # error anywhere. It is the single most expensive failure to diagnose
        # in production, so it fails here instead.
        print(f"\n[{label}] NO MARKS — the chart would render empty")
        return False

    print(
        f"\n[{label}] compiled and rendered"
        f"\n  marks   : {marks}"
        f"\n  signals : {len(signals)}"
        f"\n  png     : {png_path} ({png_path.stat().st_size / 1024:,.1f} KiB)"
    )
    return True


def _count_marks(vega: dict[str, Any]) -> int:
    """Count leaf marks in the compiled Vega scene graph, at any nesting depth.

    Concatenated views nest group marks inside group marks, so a top-level
    count would report three regardless of whether any curve was drawn.
    """
    def walk(node: Any) -> int:
        if isinstance(node, dict):
            if node.get("type") == "group":
                return sum(walk(child) for child in node.get("marks", []))
            if "type" in node and "from" in node:
                return 1
            return 0
        if isinstance(node, list):
            return sum(walk(child) for child in node)
        return 0

    return sum(walk(mark) for mark in vega.get("marks", []))


if __name__ == "__main__":
    sys.exit(main())
