"""
DEPRECATED -- moved to src/agents/capabilities.py (2026-07-15).

This file is kept only as a pointer so nothing silently imports stale logic.
Update any remaining `from src.capabilities import ...` references to
`from src.agents.capabilities import ...` instead.
"""
from src.agents.capabilities import AGENT_CAPABILITIES, get_agent_capability, is_tool_allowed  # noqa: F401
