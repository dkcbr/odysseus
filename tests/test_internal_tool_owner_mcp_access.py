"""Real, direct regression test for a significant bug found and fixed
2026-09-25 while investigating why Claude Sonnet 5 (an API model) had
zero access to any genuine MCP-server-sourced tool (public_com,
gods_eye_view, etc.) despite the servers themselves being confirmed
healthy and correctly connected.

Root-caused precisely, live: internal loopback requests (the agent's
own tool-layer calls) carry the reserved pseudo-username
core.middleware.INTERNAL_TOOL_USER ("internal-tool"), which
require_admin() already explicitly trusts -- but
owner_is_admin_or_single_user() did not, so blocked_tools_for_owner()
treated every internal-tool call as an untrusted public user and hid
all MCP schemas (src/agent_loop.py forces mcp_mgr = None whenever
blocked_tools_for_owner() returns a non-empty set). Confirmed directly
before this fix: a real, live chat_stream request asking to control
the gods-eye-view globe got mcp_schemas_total=0; the exact same
request after this fix got mcp_schemas_total=308 and correctly called
the real MCP tools."""

from src.tool_security import owner_is_admin_or_single_user, blocked_tools_for_owner
from core.middleware import INTERNAL_TOOL_USER


def test_internal_tool_user_is_trusted_like_an_admin():
    assert owner_is_admin_or_single_user(INTERNAL_TOOL_USER) is True


def test_internal_tool_user_has_no_blocked_tools():
    assert blocked_tools_for_owner(INTERNAL_TOOL_USER) == set()


def test_unknown_owner_string_is_not_trusted():
    # Real, deliberate contrast: this fix trusts the one specific,
    # reserved pseudo-username that require_admin() already trusts --
    # it does not weaken the check for any other, arbitrary owner
    # string a real public/non-admin user might have.
    assert owner_is_admin_or_single_user("some-random-username") is False
    assert blocked_tools_for_owner("some-random-username") != set()
