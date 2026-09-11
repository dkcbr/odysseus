"""Real, targeted test for the 3 tools lost to an uncommitted-work git
checkout on 2026-08-13 (get_portfolio_context, lookup_ticker,
create_document_office), then partially reconstructed. Written to catch
another silent loss the same way -- checks presence across every real
registry each tool needs, not just one.

Parsed with ast/regex instead of importing, matching
test_tool_index_schema_parity.py's approach, so this doesn't pull in the
embedding/ChromaDB stack.
"""
import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXPECTED_TOOLS = {"get_portfolio_context", "lookup_ticker", "create_document_office"}


def _assigned_value(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
    raise AssertionError(f"{name} assignment not found")


def _read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


def test_schema_tool_names_include_expected():
    src = _read("src/tool_schemas.py")
    value = _assigned_value(ast.parse(src), "FUNCTION_TOOL_SCHEMAS")
    names = {item["function"]["name"] for item in ast.literal_eval(value)}
    missing = EXPECTED_TOOLS - names
    assert not missing, f"Missing from FUNCTION_TOOL_SCHEMAS: {sorted(missing)}"


def test_always_available_includes_expected():
    src = _read("src/tool_index.py")
    value = _assigned_value(ast.parse(src), "ALWAYS_AVAILABLE")
    # ALWAYS_AVAILABLE = frozenset({...}) is a Call node; literal_eval only
    # the set argument, not the frozenset(...) wrapper itself.
    assert isinstance(value, ast.Call), f"Expected a Call node, got {value!r}"
    names = ast.literal_eval(value.args[0])
    missing = EXPECTED_TOOLS - set(names)
    assert not missing, f"Missing from ALWAYS_AVAILABLE: {sorted(missing)}"


def test_tool_tags_includes_expected():
    src = _read("src/agent_tools/__init__.py")
    # TOOL_TAGS may be a set union across multiple lines/expressions --
    # too complex to reliably ast.literal_eval. Check within just the
    # TOOL_TAGS assignment's source text for each tool name as a string.
    start = src.index("TOOL_TAGS = ")
    end = src.index("\n\n", start)
    tool_tags_src = src[start:end]
    missing = [t for t in EXPECTED_TOOLS if f'"{t}"' not in tool_tags_src]
    assert not missing, f"Missing from TOOL_TAGS: {sorted(missing)}"


def test_tool_execution_has_a_real_branch_for_each():
    # Two valid, real dispatch mechanisms in this codebase: an if/elif
    # chain in tool_execution.py, OR a dict-based dispatch entry in
    # agent_tools/__init__.py (e.g. lookup_ticker -> TickerLookupTool().execute).
    # A tool is fine as long as it's wired into at least one.
    exec_src = _read("src/tool_execution.py")
    dispatch_src = _read("src/agent_tools/__init__.py")
    for t in EXPECTED_TOOLS:
        in_exec = bool(re.search(rf'tool\s*==\s*"{t}"|"{t}"\s*(,|in)', exec_src))
        in_dispatch_dict = f'"{t}":' in dispatch_src
        assert in_exec or in_dispatch_dict, (
            f"No execution branch or dispatch entry found for {t!r} "
            "in tool_execution.py or agent_tools/__init__.py"
        )


def test_tool_sections_has_prompt_text_for_each():
    src = _read("src/agent_loop.py")
    for t in EXPECTED_TOOLS:
        assert f'"{t}":' in src, f"No TOOL_SECTIONS prompt text found for {t!r} in agent_loop.py"
