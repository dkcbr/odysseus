"""
src/agents/odysseus_client.py

Convenience entry point -- re-exports the split auth/dashboard modules so
existing code that imports from here keeps working:

    from src.agents.odysseus_client import get_session, OdysseusDashboard

The real logic now lives in:
    src/agents/odysseus_auth.py       -- login, cookie persistence, session validity
    src/agents/odysseus_dashboard.py  -- /api/agents/* + /api/mcp/call wrappers
"""

from src.agents.odysseus_auth import (  # noqa: F401
    DEFAULT_BASE_URL,
    DEFAULT_COOKIE_FILE,
    login,
    get_session,
    is_session_valid,
)
from src.agents.odysseus_dashboard import OdysseusDashboard  # noqa: F401

# Backwards-compatible aliases matching earlier iterations of this module.
odysseus_login = login
odysseus_client = get_session


if __name__ == "__main__":
    import json

    session = get_session()
    dash = OdysseusDashboard(session)
    print(json.dumps(dash.recent(), indent=2))
