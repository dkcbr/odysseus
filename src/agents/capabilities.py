"""
src/agents/capabilities.py

Agent capability registry -- defines which MCP servers and tools each
planned agent role is allowed to use. Backed by data/agent_capabilities.json
so there is a single, human-editable source of truth (edit the JSON, not
this file, to change the actual capability data).

Import as: from src.agents.capabilities import AGENT_CAPABILITIES
       or (from within src/agents/): from .capabilities import AGENT_CAPABILITIES
"""

import json
import logging
from pathlib import Path

from src.constants import DATA_DIR

logger = logging.getLogger(__name__)

_CAPABILITIES_FILE = Path(DATA_DIR) / "agent_capabilities.json"


def _load_capabilities() -> dict:
    """Load and validate the agent capability registry from disk.

    Returns an empty dict (with a warning logged) if the file is missing or
    malformed, so a bad/absent config degrades gracefully instead of crashing
    the whole app at import time.
    """
    if not _CAPABILITIES_FILE.exists():
        logger.warning(
            "Agent capabilities file not found at %s -- AGENT_CAPABILITIES will be empty.",
            _CAPABILITIES_FILE,
        )
        return {}

    try:
        with open(_CAPABILITIES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load agent capabilities from %s: %s", _CAPABILITIES_FILE, e)
        return {}

    # Drop the "_note" metadata key -- it's documentation, not an agent entry.
    data.pop("_note", None)

    for agent_name, profile in data.items():
        if not isinstance(profile, dict):
            logger.warning("Ignoring malformed capability entry '%s' (not a dict)", agent_name)
            continue
        for required_key in ("servers", "allowed_tools", "forbidden_tools", "tasks"):
            if required_key not in profile:
                logger.warning(
                    "Agent capability '%s' is missing '%s' -- defaulting to empty list",
                    agent_name, required_key,
                )
                profile[required_key] = []

    return data


AGENT_CAPABILITIES: dict = _load_capabilities()


def get_agent_capability(agent_name: str) -> dict | None:
    """Look up a single agent's capability profile by name, or None if unknown."""
    return AGENT_CAPABILITIES.get(agent_name)


def is_tool_allowed(agent_name: str, tool_name: str, server_name: str | None = None) -> bool:
    """Check whether a given agent role is allowed to call a given tool name.

    Returns False for unknown agents (fail closed) and for tools on the
    forbidden list even if they also appear in allowed_tools by mistake.

    If server_name is given, ALSO requires that server to be in the agent's
    "servers" list -- this catches the case where a tool name happens to be
    allowed for the agent in general, but the specific server it's being
    called on isn't one the agent is actually scoped to use.
    """
    profile = get_agent_capability(agent_name)
    if not profile:
        return False
    if tool_name in profile.get("forbidden_tools", []):
        return False
    if tool_name not in profile.get("allowed_tools", []):
        return False
    if server_name is not None and server_name not in profile.get("servers", []):
        return False
    return True
