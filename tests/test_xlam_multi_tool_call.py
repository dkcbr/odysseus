"""Real regression test, 2026-09-25: xLAM's own documented template asks
for "additional parallel tool calls as needed" in a single JSON array, and
llama-xlam-2-8b-fc-r was confirmed live (via LM Studio) to actually honor
that -- a two-action prompt produced a real, correct two-entry array. The
bug was Odysseus's own parser only ever keeping the first entry and
silently dropping the rest. See _xlam_calls_to_blocks's docstring in
src/tool_parsing.py for the full context.
"""
import json

from src.agent_tools import parse_tool_blocks, strip_tool_blocks


def test_xlam_multi_call_array_all_calls_extracted():
    raw = json.dumps(
        [
            {"name": "web_search", "arguments": {"query": "first lookup"}},
            {"name": "bash", "arguments": {"command": "date"}},
        ]
    )

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 2
    assert blocks[0].tool_type == "web_search"
    assert blocks[0].content == "first lookup"
    assert blocks[1].tool_type == "bash"
    assert blocks[1].content == "date"
    assert strip_tool_blocks(raw).strip() == ""


def test_xlam_single_call_array_still_works():
    raw = json.dumps([{"name": "web_search", "arguments": {"query": "hello world"}}])

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert blocks[0].content == "hello world"


def test_xlam_array_skips_invalid_entries_but_keeps_valid_ones():
    raw = json.dumps(
        [
            {"name": "web_search", "arguments": {"query": "first"}},
            {"name": "", "arguments": {}},
            {"name": "web_search", "arguments": {"query": "second"}},
        ]
    )

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 2
    assert blocks[0].content == "first"
    assert blocks[1].content == "second"
