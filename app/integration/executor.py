"""A2UI runtime extension negotiating executor for Gemini Enterprise and A2A.

In : Incoming A2A RequestContext with client-declared requested_extensions.
Out: Activated A2UI version recorded in context state; execution forwarded to ADK.
Rule: This executor intercepts each request before execution. Declaring the extension
      on the agent card (Step 6) is necessary but not sufficient; runtime negotiation
      is required so Gemini Enterprise acknowledges the A2UI envelope and does not
      silently drop UI components.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Sequence

from a2a.server.events import EventQueue
from a2a.types import AgentCard
from google.adk.a2a.executor.a2a_agent_executor import (
    A2aAgentExecutor,
    A2aAgentExecutorConfig,
    RequestContext,
)

try:
    from app.contracts import ACTIVE_A2UI_CATALOG_VERSION, A2uiCatalogVersion
    from app.integration.agent_card import A2UI_V09_EXTENSION_URI
except ImportError:
    from contracts import ACTIVE_A2UI_CATALOG_VERSION, A2uiCatalogVersion
    from integration.agent_card import A2UI_V09_EXTENSION_URI

logger = logging.getLogger(__name__)

# Standard A2UI extension URI prefix across Google Cloud and Gemini Enterprise:
A2UI_EXTENSION_PREFIX: str = "https://a2ui.org/a2a-extension/a2ui/"

# Legacy v0.8 URI for backwards-compatibility checking:
A2UI_V08_EXTENSION_URI: str = f"{A2UI_EXTENSION_PREFIX}v0.8"

# Key used to record the active UI version in the request context state dictionary:
A2UI_STATE_KEY: str = "active_a2ui_version"


def extract_requested_a2ui_extensions(context: RequestContext) -> list[str]:
    """Extract all A2UI extension URIs requested by the calling client.
    
    In A2A protocol:
    - Gemini Enterprise passes requested extensions in HTTP header 'X-A2A-Extensions',
      which the server puts into `context.requested_extensions` (set of strings).
    - Alternatively, in JSON-RPC message payloads, `context.message.extensions` can
      contain explicit AgentExtension objects.
    """
    matched: list[str] = []

    # Check context.requested_extensions (from call_context HTTP headers)
    if hasattr(context, "requested_extensions") and context.requested_extensions:
        for ext in context.requested_extensions:
            if isinstance(ext, str) and ext.startswith(A2UI_EXTENSION_PREFIX):
                matched.append(ext)

    # Check context.message.extensions (from JSON-RPC body)
    if (
        hasattr(context, "message")
        and context.message
        and hasattr(context.message, "extensions")
        and context.message.extensions
    ):
        for ext in context.message.extensions:
            uri = getattr(ext, "uri", None)
            if uri and isinstance(uri, str) and uri.startswith(A2UI_EXTENSION_PREFIX):
                if uri not in matched:
                    matched.append(uri)

    return matched


def extract_supported_a2ui_extensions(agent_card: AgentCard | None) -> list[str]:
    """Extract all A2UI extension URIs supported and advertised by this agent.
    
    Reads from the agent card capabilities built in Step 6 (agent_card.py).
    If no card is provided, falls back to the production target (A2UI v0.9).
    """
    if not agent_card or not hasattr(agent_card, "capabilities") or not agent_card.capabilities:
        return [A2UI_V09_EXTENSION_URI]

    supported: list[str] = []
    for ext in agent_card.capabilities.extensions:
        uri = getattr(ext, "uri", None)
        if uri and isinstance(uri, str) and uri.startswith(A2UI_EXTENSION_PREFIX):
            supported.append(uri)

    return supported or [A2UI_V09_EXTENSION_URI]


def resolve_a2ui_version(matched_uris: Sequence[str]) -> str | None:
    """Resolve the highest compatible A2UI version from matched extension URIs.
    
    Prefers v0.9 (modern Angular runtime with VegaChart and Material 3) over
    legacy v0.8 (Lit renderer).
    """
    if not matched_uris:
        return None

    # Version priority ranking: higher index = higher preference
    version_priority = {
        "v0.8": 1,
        "v0.9": 2,
    }

    best_version: str | None = None
    best_rank = 0

    for uri in matched_uris:
        version_str = uri.removeprefix(A2UI_EXTENSION_PREFIX).strip()
        rank = version_priority.get(version_str, 0)
        if rank > best_rank:
            best_rank = rank
            best_version = version_str

    # If unmatched unknown version, strip prefix and return as fallback
    if best_version is None and matched_uris:
        best_version = matched_uris[0].removeprefix(A2UI_EXTENSION_PREFIX).strip()

    return best_version


def try_activate_a2ui_extension(
    context: RequestContext,
    agent_card: AgentCard | None = None,
) -> str | None:
    """Negotiate and activate the A2UI extension for the current request turn.
    
    Step 7 Core Logic:
    1. Inspects client requested extensions from headers / message.
    2. Compares against agent's supported capabilities.
    3. If overlap is found, determines the highest mutually supported version (e.g. 'v0.9').
    4. Records the negotiated version in `context.call_context.state[A2UI_STATE_KEY]`.
    5. Calls `context.add_activated_extension(...)` if supported by the container.
    6. Returns the activated version string (or None if client does not support A2UI).
    """
    requested = extract_requested_a2ui_extensions(context)
    if not requested:
        logger.debug("No A2UI extensions requested by client. Operating in text-only mode.")
        return None

    supported = extract_supported_a2ui_extensions(agent_card)
    common_uris = [uri for uri in requested if uri in supported]

    if not common_uris:
        logger.warning(
            "Client requested A2UI extensions %s, but agent only supports %s. Falling back to text.",
            requested,
            supported,
        )
        return None

    activated_version = resolve_a2ui_version(common_uris)
    if not activated_version:
        return None

    # Record activated version in call context state for downstream callbacks & tools
    try:
        if hasattr(context, "call_context") and hasattr(context.call_context, "state"):
            context.call_context.state[A2UI_STATE_KEY] = activated_version
    except Exception as e:
        logger.warning("Could not set A2UI_STATE_KEY in call_context.state: %s", e)

    # If runtime context supports add_activated_extension, record it formally
    selected_uri = f"{A2UI_EXTENSION_PREFIX}{activated_version}"
    if hasattr(context, "add_activated_extension") and callable(context.add_activated_extension):
        try:
            context.add_activated_extension(selected_uri)
        except Exception as e:
            logger.debug("context.add_activated_extension failed: %s", e)

    logger.info("Successfully negotiated A2UI extension: version=%s (URI=%s)", activated_version, selected_uri)
    return activated_version


class A2uiNegotiatingExecutor(A2aAgentExecutor):
    """Subclass of Google ADK A2aAgentExecutor that performs runtime A2UI negotiation.
    
    Before delegating turn execution to the underlying ADK agent runner, this executor
    evaluates the client's requested extensions, activates A2UI v0.9 if supported,
    and stores the active mode in context state so the render callbacks (Step 23)
    know whether to emit A2UI DataParts or fall back to plain text.
    """

    def __init__(
        self,
        *,
        runner: Any,
        agent_card: AgentCard | None = None,
        config: Optional[A2aAgentExecutorConfig] = None,
        use_legacy: bool = False,
        force_new_version: bool = False,
    ):
        super().__init__(
            runner=runner,
            config=config,
            use_legacy=use_legacy,
            force_new_version=force_new_version,
        )
        self._agent_card = agent_card

    def set_agent_card(self, agent_card: AgentCard) -> None:
        """Dynamically bind the built agent card to this executor."""
        self._agent_card = agent_card

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Intercept the A2A execution to negotiate A2UI before delegating to ADK."""
        active_version = try_activate_a2ui_extension(context, self._agent_card)
        if active_version:
            logger.info("A2uiNegotiatingExecutor: Activated A2UI %s for task %s", active_version, context.task_id)
        else:
            logger.debug("A2uiNegotiatingExecutor: Running without A2UI for task %s", context.task_id)

        # Proceed with normal ADK agent execution
        await super().execute(context, event_queue)
