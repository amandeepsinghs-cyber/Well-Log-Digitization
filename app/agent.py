"""Root entry point for the Log Digitisation Agent application.

Delegates agent construction to app.integration.agent, maintaining strict
separation between scaffold interface and domain integration code.
"""

from app.integration.agent import app, root_agent

__all__ = ["root_agent", "app"]
