"""ADK Agent for the Well Log Digitisation workflow.

In : a user prompt, for example 'digitise the Schlumberger scan' or 'show me
     WELL_LOG_SCHLUM'.
Out: a text response, plus an A2UI v0.9 surface — the requested well log as an
     interactive three-track chart, or the live contents of the agent's bucket.
Rule: the chart is built deterministically in Python and attached by a
      callback. The model never sees a Vega specification and cannot author,
      edit or hallucinate one; it decides only WHICH well to show.
      tests/unit/test_a2ui_catalog_validation.py gates the payload before deploy.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

try:
    from app.gcs.inventory import scan_bucket_inventory
    from app.gcs.read_bytes import read_bytes
    from app.ingest.load_image import load_image
    from app.integration import pipeline_render
    from app.integration.tools import (
        PENDING_CHART_KEY,
        PENDING_SCAN_KEY,
        digitise_scanned_log,
        inspect_scanned_log,
        list_well_logs,
        render_well_log,
        show_scanned_log,
    )
    from app.render.a2ui_emit import build_log_surface
    from app.render.a2ui_envelope import (
        A2A_DATA_PART_CLOSE_TAG,
        A2A_DATA_PART_OPEN_TAG,
        wrap_a2ui_part,
    )
    from app.render.a2ui_lifecycle import (
        build_create_surface,
        build_update_components,
    )
    from app.render.inventory_card import build_inventory_components
    from app.render.scan_image import build_scan_surface
except ImportError:
    from gcs.inventory import scan_bucket_inventory
    from gcs.read_bytes import read_bytes
    from ingest.load_image import load_image
    from integration import pipeline_render
    from integration.tools import (
        PENDING_CHART_KEY,
        PENDING_SCAN_KEY,
        digitise_scanned_log,
        inspect_scanned_log,
        list_well_logs,
        render_well_log,
        show_scanned_log,
    )
    from render.a2ui_emit import build_log_surface
    from render.a2ui_envelope import (
        A2A_DATA_PART_CLOSE_TAG,
        A2A_DATA_PART_OPEN_TAG,
        wrap_a2ui_part,
    )
    from render.a2ui_lifecycle import (
        build_create_surface,
        build_update_components,
    )
    from render.inventory_card import build_inventory_components
    from render.scan_image import build_scan_surface

logger = logging.getLogger(__name__)

# Standard fast model for conversational routing and wire handshake
MODEL = "gemini-2.5-flash"


def emit_a2ui_surface(
    callback_context: CallbackContext | None = None,
    **kwargs: Any,
) -> types.Content | None:
    """Attach the A2UI surface this turn earned: a chart, a scan, or the inventory.

    Runs after the agent has composed its text answer, which is the only point
    late enough to attach a rendered surface. A tool that ran during the turn
    leaves behind what it wants shown; this reads that and draws it.

    Exactly ONE surface goes out, and the order below is the precedence. A
    second surface in the same reply pushes the first below the fold, so the
    more specific request wins: a chart is only ever queued by someone who
    asked for a well, a scan only by someone who asked for the picture, and the
    inventory is what is left when neither was asked for. Chart outranks scan
    because digitising queues a chart, and a reader who has just watched a file
    be written wants to see the result rather than the input.

    Returning None on failure preserves the agent's text answer; an exception
    here would abort the whole turn. Every failure path is logged at ERROR
    level so production Cloud Logging shows exactly what broke.
    """
    # Surface ids must be new for every response. Reusing one that has already
    # been created fails silently: no error, no update, no card.
    surface_id = f"surface-{uuid.uuid4().hex[:8]}"

    pending_chart = _take_pending(callback_context, PENDING_CHART_KEY)
    pending_scan = _take_pending(callback_context, PENDING_SCAN_KEY)

    if pending_chart:
        return _emit_log_chart(pending_chart, surface_id)
    if pending_scan:
        return _emit_scan_image(pending_scan, surface_id)
    return _emit_inventory_card(surface_id)


def _take_pending(
    callback_context: CallbackContext | None,
    key: str,
) -> str | None:
    """Read and clear whatever a tool queued under `key`, if anything.

    BOTH keys are read and cleared every turn, even though only one surface is
    drawn. Leaving the loser in place would make it reattach to some later
    reply, in a different context, about a different file.
    """
    if callback_context is None:
        return None

    pending = callback_context.state.get(key)
    if not pending:
        return None

    callback_context.state[key] = ""
    return pending


def _emit_scan_image(object_name: str, surface_id: str) -> types.Content | None:
    """Show the scanned sheet as it was filed, whether it arrived as an image or a PDF."""
    try:
        image = load_image(read_bytes(object_name))
        parts = build_scan_surface(
            image,
            surface_id=surface_id,
            title=object_name.rsplit("/", 1)[-1],
            caption=(
                f"{image.width:,} × {image.height:,} px  ·  "  # noqa: RUF001 - the multiplication sign is the correct glyph for pixel dimensions
                "the original scan, before digitisation"
            ),
        )
    except Exception as exc:
        logger.error(
            "emit_a2ui_surface: FALLBACK - no image for %s, %s: %s",
            object_name,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return None

    logger.info(
        "emit_a2ui_surface: scan surface=%s object=%s parts=%d",
        surface_id,
        object_name,
        len(parts),
    )
    return types.Content(role="model", parts=parts)


def _emit_log_chart(object_name: str, surface_id: str) -> types.Content | None:
    """Draw a digitised LAS as a three-track chart."""
    try:
        plot = pipeline_render.render(object_name)
        parts = build_log_surface(plot, surface_id=surface_id)
    except Exception as exc:
        logger.error(
            "emit_a2ui_surface: FALLBACK - no chart for %s, %s: %s",
            object_name,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return None

    logger.info(
        "emit_a2ui_surface: chart surface=%s well=%s parts=%d",
        surface_id,
        plot.well_name,
        len(parts),
    )
    return types.Content(role="model", parts=parts)


def _emit_inventory_card(surface_id: str) -> types.Content | None:
    """Show the live Cloud Storage inventory: what is here and what is digitised."""
    try:
        inventory = scan_bucket_inventory()
        components = build_inventory_components(inventory)
        parts = [
            wrap_a2ui_part(build_create_surface(surface_id=surface_id)),
            wrap_a2ui_part(
                build_update_components(
                    surface_id=surface_id, components=components
                )
            ),
        ]
    except Exception as exc:
        logger.error(
            "emit_a2ui_surface: FALLBACK - suppressing inventory card, %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return None

    logger.info(
        "emit_a2ui_surface: inventory surface=%s components=%d scan_ok=%s objects=%d",
        surface_id,
        len(components),
        inventory.ok,
        inventory.total_objects,
    )
    return types.Content(role="model", parts=parts)


def strip_fabricated_a2ui(
    llm_response: LlmResponse | None = None,
    **kwargs: Any,
) -> LlmResponse | None:
    """Delete any A2UI payload the model wrote into its own prose.

    THE MODEL CAN SEE OUR SURFACES AND WILL COPY THEM. A2UI travels as
    text/plain wrapped in <a2a_datapart_json> tags, so every surface we attach
    re-enters the conversation as text the model reads on the next turn. It
    imitates what it reads: observed in production emitting three such blobs in
    one reply, two copied verbatim from the previous turn — reusing that turn's
    surface id — and a third inventing a 'WellLogChart' component that exists in
    no catalog. The copies are worse than the invention, because a repeated
    surface id makes the renderer drop the real surface too.

    An instruction alone will not hold; the instruction below is belt and this
    is braces. Only the model's TEXT parts are touched. Our own surfaces are
    inline_data blobs attached later by the after-agent callback, so they never
    pass through here.

    Returns None when nothing was found, which tells ADK to keep the response
    untouched rather than replacing it with an identical copy.
    """
    if llm_response is None or llm_response.content is None:
        return None

    parts = llm_response.content.parts or []
    cleaned: list[types.Part] = []
    removed = 0

    for part in parts:
        text = getattr(part, "text", None)
        if not text or A2A_DATA_PART_OPEN_TAG not in text:
            cleaned.append(part)
            continue

        stripped = _remove_datapart_blobs(text)
        removed += 1
        # A part that was nothing but a payload is dropped rather than left as
        # an empty bubble in the transcript.
        if stripped.strip():
            cleaned.append(types.Part(text=stripped))

    if not removed:
        return None

    logger.warning(
        "strip_fabricated_a2ui: removed A2UI payloads from %d model text part(s)",
        removed,
    )
    # Never hand back a response with no parts at all: downstream treats that as
    # a malformed turn. An empty text part is a blank answer, which is honest.
    llm_response.content.parts = cleaned or [types.Part(text="")]
    return llm_response


def sanitize_llm_request_history(
    callback_context: CallbackContext | None = None,
    llm_request: LlmRequest | None = None,
    **kwargs: Any,
) -> LlmResponse | None:
    """Delete any A2UI payload from the conversation history before calling the LLM.

    A2UI travels across the wire as text/plain wrapped in <a2a_datapart_json> tags.
    When an A2UI surface was emitted on turn N, ADK includes that envelope in
    `llm_request.contents` on turn N+1. If the model reads raw <a2a_datapart_json>
    or base64 image data URIs in its history, it tries to imitate them by
    generating UI payloads in prose, generating tens of thousands of tokens and
    spinning until MAX_TOKENS (4+ minutes).

    This callback scrubs all <a2a_datapart_json> regions from all historical parts
    in `llm_request.contents`, so the model receives only clean prose in its context.
    It also enforces max_output_tokens=1024 as a hard backstop against runaway loops.
    """
    if llm_request is None or not getattr(llm_request, "contents", None):
        return None

    for content in llm_request.contents:
        if not getattr(content, "parts", None):
            continue
        cleaned_parts: list[types.Part] = []
        for part in content.parts:
            text = getattr(part, "text", None)
            if text and A2A_DATA_PART_OPEN_TAG in text:
                stripped = _remove_datapart_blobs(text)
                if stripped.strip():
                    cleaned_parts.append(types.Part(text=stripped))
            else:
                cleaned_parts.append(part)

        content.parts = cleaned_parts or [types.Part(text="")]

    if llm_request.config is None:
        llm_request.config = types.GenerateContentConfig(max_output_tokens=1024)
    elif (
        not getattr(llm_request.config, "max_output_tokens", None)
        or llm_request.config.max_output_tokens > 1024
    ):
        llm_request.config.max_output_tokens = 1024

    return None


def _remove_datapart_blobs(text: str) -> str:
    """Cut every <a2a_datapart_json> region out of a string.

    Scanned rather than regexed because the payload is JSON and may itself
    contain the closing angle-bracket sequences a pattern would trip over. An
    opening tag with no closing tag truncates the rest of the string: a partial
    payload is not prose and the model was mid-fabrication when it ran out.
    """
    out: list[str] = []
    rest = text
    while True:
        start = rest.find(A2A_DATA_PART_OPEN_TAG)
        if start == -1:
            out.append(rest)
            return "".join(out)

        out.append(rest[:start])
        end = rest.find(A2A_DATA_PART_CLOSE_TAG, start)
        if end == -1:
            return "".join(out)
        rest = rest[end + len(A2A_DATA_PART_CLOSE_TAG):]


root_agent = Agent(
    name="log_digitiser",
    description="Well Log Digitisation Agent - Automated Petrophysical Well Log Digitisation & Interactive Multi-Track A2UI Visualization for Gemini Enterprise",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    generate_content_config=types.GenerateContentConfig(
        max_output_tokens=1024,
    ),
    instruction=(
        "You are the Well Log Digitisation Agent. "
        "You digitise scanned well logs into CWLS/SPWLA LAS 2.0 files and render "
        "interactive multi-track log plots in Gemini Enterprise. "
        "Your data lives in Cloud Storage in asia-south1 (Mumbai).\n\n"
        "TOOLS\n"
        "- list_well_logs: what is in the bucket, which scans are still only "
        "images or PDFs, and which already have a LAS. Use it whenever you are "
        "asked what is available, how many there are, or what has been done.\n"
        "- show_scanned_log: displays the scanned sheet itself, the picture. "
        "Use it whenever the user asks to see, view or look at a scan. The platform "
        "automatically attaches and displays the image below your answer. Do not "
        "describe a scan instead of showing it, and do NOT attempt to emit base64 "
        "data or markup yourself.\n"
        "- inspect_scanned_log: what is printed on one scan — tracks, scales, "
        "depth range — without changing anything. Use it before digitising.\n"
        "- digitise_scanned_log: traces the curves and WRITES a LAS to the "
        "bucket. See the confirmation rule below.\n"
        "- render_well_log: shows a well that has already been digitised.\n\n"
        "WHICH FILE\n"
        "When a request could mean more than one file, list the candidates and "
        "ask which one. Never pick for the user: two scans of the same well are "
        "usually different runs, and showing the wrong one is not a small error. "
        "If show_scanned_log returns candidates, put them to the user verbatim.\n\n"
        "CONFIRMATION\n"
        "digitise_scanned_log writes a file to the user's Cloud Storage bucket "
        "and takes a minute or two. Never call it on your own initiative. When "
        "the user asks about digitising, first state which scan you would read, "
        "which object key the LAS would be written to, and that it takes a "
        "minute or two, then ask them to confirm and stop. Call the tool only "
        "after they have agreed. If they name the file and say 'go ahead' in "
        "the same breath, that is agreement — do not ask twice.\n\n"
        "SURFACES\n"
        "Charts, images and the inventory card are attached to your reply "
        "automatically. Digitising attaches the chart of what you just wrote, "
        "so say the plot is below rather than offering to show it separately. "
        "When show_scanned_log queues a scan, confirm in one sentence that the "
        "image is displayed below. Never describe a chart or image you have not "
        "been told was drawn, and never invent file names, counts, sizes or curve "
        "readings. Refer the user to the attached surface for the authoritative detail.\n"
        "NEVER write UI markup of any kind in your reply. Do not emit "
        "<a2a_datapart_json> tags, A2UI messages, surface ids, component trees, "
        "data: URLs, base64 strings, or chart specifications, even if you have "
        "seen them earlier in this conversation. Those are the transport's business, "
        "not yours. Your reply is prose only."
    ),
    tools=[
        list_well_logs,
        show_scanned_log,
        inspect_scanned_log,
        digitise_scanned_log,
        render_well_log,
    ],
    before_model_callback=sanitize_llm_request_history,
    after_model_callback=strip_fabricated_a2ui,
    after_agent_callback=emit_a2ui_surface,
)

app = App(
    root_agent=root_agent,
    name="log_digitiser",
)
