"""Agent card capabilities declaration for Gemini Enterprise and Agent Registry.

In : None.
Out: An AgentCapabilities object declaring ADK executor and A2UI v0.9 support.
Rule: This module provides the capabilities passed to attach_a2a_routes in fast_api_app.py.
"""

from a2a.types import AgentCapabilities, AgentExtension


# Standard extension URIs recognized by Gemini Enterprise and the ADK runtime:

# 1. ADK Agent Executor Extension:
# Advertises that the agent uses Google's modern ADK executor runtime.
ADK_AGENT_EXECUTOR_EXTENSION_URI: str = (
    "https://google.github.io/adk-docs/a2a/a2a-extension/"
)

# 2. A2UI v0.9 Protocol Extension:
# Advertises that the agent can produce A2UI v0.9 JSON surfaces (createSurface,
# updateComponents, updateDataModel) for rendering interactive components (VegaChart, Canvas).
# Without this declaration on the card, Gemini Enterprise treats the agent as text-only.
A2UI_V09_EXTENSION_URI: str = "https://a2ui.org/a2a-extension/a2ui/v0.9"
DEFAULT_GE_CATALOG_ID: str = (
    "https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json"
)


def build_agent_capabilities() -> AgentCapabilities:
    """Build the comprehensive A2A capabilities descriptor for the agent card.
    
    This function explicitly advertises both streaming capability and the
    A2UI v0.9 extension to the calling host (Gemini Enterprise or Agent Registry).
    """
    from google.protobuf.struct_pb2 import Struct

    catalog_params = Struct()
    catalog_params.update({"supportedCatalogIds": [DEFAULT_GE_CATALOG_ID]})

    return AgentCapabilities(
        streaming=True,
        extensions=[
            AgentExtension(
                uri=ADK_AGENT_EXECUTOR_EXTENSION_URI,
                description="Ability to use the modern ADK agent executor implementation",
            ),
            AgentExtension(
                uri=A2UI_V09_EXTENSION_URI,
                description="Ability to render rich A2UI v0.9 interactive components (VegaChart, Canvas)",
                params=catalog_params,
            ),
        ],
    )
