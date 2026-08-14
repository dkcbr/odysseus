"""Real, targeted regression test for the tool-selection bug found and
fixed 2026-08-14: a workspace/local-computer-request heuristic in
agent_loop.py used to REPLACE the entire relevant-tools set with a
hardcoded _WORKSPACE_TERMINUS_TOOLS set instead of adding to it, silently
dropping every ALWAYS_AVAILABLE tool except ask_user/update_plan (which
happened to already be inside _WORKSPACE_TERMINUS_TOOLS). This is why
create_document_office/get_portfolio_context were never offered to the
model on round 2+ of a real agent chat.

Checks the real source code pattern directly (not a reimplementation of
the logic), matching test_recovered_tools_present.py's approach, so this
doesn't pull in the embedding/ChromaDB stack and can't silently drift
from what the real code actually does.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


def test_workspace_terminus_assignment_is_a_union_not_a_replacement():
    src = _read("src/agent_loop.py")
    # The real, exact bug: `_relevant_tools = set(_WORKSPACE_TERMINUS_TOOLS)`
    # (a full replacement) instead of `_relevant_tools |= set(...)` (a union).
    # Assert the destructive form is NOT present, and the safe form IS.
    assert not re.search(r"_relevant_tools\s*=\s*set\(_WORKSPACE_TERMINUS_TOOLS\)", src), (
        "Found the exact destructive replacement pattern that caused "
        "ALWAYS_AVAILABLE tools (get_portfolio_context, create_document_office, "
        "etc.) to be silently dropped on round 2+ of a real agent chat. "
        "Must be a union (|=), not a replacement (=)."
    )
    assert re.search(r"_relevant_tools\s*\|=\s*set\(_WORKSPACE_TERMINUS_TOOLS\)", src), (
        "Expected the real, fixed union assignment for the workspace-terminal "
        "override -- not found. Has this code moved or changed shape?"
    )


def test_workspace_terminus_tools_defined():
    # Sanity: the referenced constant genuinely exists (catches the set
    # itself being renamed/removed without updating the assignment above).
    src = _read("src/agent_loop.py")
    assert "_WORKSPACE_TERMINUS_TOOLS" in src
