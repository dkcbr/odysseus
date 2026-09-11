"""
tool_execution.py

Tool dispatcher and result formatter for the agent loop.
Routes tool blocks to MCP servers or native implementations.

Extracted from agent_tools.py.
"""

import asyncio
import collections
import contextvars
import json
import logging
import os
import pathlib
import re
import sys
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple



from src.tool_security import (
    BUILTIN_EMAIL_TOOLS,
    email_tool_policy_names,
    is_public_blocked_tool,
    owner_is_admin_or_single_user,
)
from src.tool_policy import ToolPolicy
from src.constants import MAX_OUTPUT_CHARS, MAX_READ_CHARS, MAX_DIFF_LINES, DATA_DIR
from src.tool_utils import _truncate, get_mcp_manager

# TODO(recovery): an uncommitted, 11-line delta touching this file and
# src/tool_security.py was lost on 2026-08-13 via an accidental
# `git checkout <branch> -- .`. Confirmed via full git history (all refs,
# entire file history back to v1.0) that it was never committed anywhere
# and is not recoverable. No currently-failing test depends on it. If/when
# reconstruction is needed, implement only what a concrete failing test or
# spec requires -- do not guess at dispatch/security behavior here.

# Persistent working directory for agent subprocesses.
# Resolves to <repo_root>/data, which is the bind-mounted volume in Docker
# (/app/data) and the local data directory for manual installs.
# Using this as cwd and HOME prevents the agent from silently creating files
# in ephemeral container layers that are lost on the next rebuild.
_AGENT_WORKDIR = DATA_DIR



# ---------------------------------------------------------------------------
# Path confinement for read_file / write_file
# ---------------------------------------------------------------------------
# read_file + write_file are admin-only tools, but the path the agent
# supplies is model-controlled. Prompt-injection in an admin's chat can
# weaponise "read /etc/shadow" or "write ~/.ssh/authorized_keys" without
# the admin noticing.
#
# Policy:
#   1. Sensitive-subpath deny list — checked FIRST. Blocks .ssh,
#      .gnupg, shell rc files, token/env files even if the root above
#      them is on the allowlist.
#   2. Allowlist — only the directories the agent legitimately needs
#      (project data/, system tmp). $HOME is NOT on the default list.
#   3. Opt-in extra roots — admin can add broader roots via the
#      "tool_path_extra_roots" setting (list of path strings).
# ---------------------------------------------------------------------------

_SENSITIVE_BASENAMES: set[str] = {
    ".ssh", ".gnupg", ".gitconfig",
    ".bashrc", ".bash_profile", ".bash_logout",
    ".zshrc", ".zprofile", ".zshenv",
    ".profile", ".tcshrc", ".cshrc",
    ".env", ".netrc",
}

_SENSITIVE_FILE_PATTERNS: tuple[str, ...] = (
    "authorized_keys", "id_rsa", "id_ed25519", "id_ecdsa",
    "known_hosts",
)

# Case-folded views used for matching. On a case-insensitive filesystem
# (Windows, default macOS) ".SSH/AUTHORIZED_KEYS" and ".env" resolve to the
# same protected files as their lowercase forms, so the deny-list has to fold
# case before comparing — the sibling resolver already normcases paths for the
# same reason. casefold (not os.path.normcase) because normcase is a no-op on
# POSIX, which is exactly where the macOS read-exfil path lives.
_SENSITIVE_BASENAMES_CF: frozenset[str] = frozenset(b.casefold() for b in _SENSITIVE_BASENAMES)
_SENSITIVE_FILE_PATTERNS_CF: frozenset[str] = frozenset(p.casefold() for p in _SENSITIVE_FILE_PATTERNS)


def _is_sensitive_path(resolved: str) -> bool:
    """Return True if *resolved* falls under a sensitive directory or
    matches a sensitive filename — regardless of what root it sits under.

    Matching is case-insensitive: on Windows / default macOS a case-variant
    name (``.SSH``, ``AUTHORIZED_KEYS``, ``Id_Rsa``) points at the same file as
    the lowercase form, so a case-sensitive check would let it slip past the
    deny-list in every file tool that relies on it.
    """
    parts = [p.casefold() for p in resolved.split(os.sep)]
    filename = parts[-1] if parts else ""

    # Check if any path component is a sensitive directory.
    for part in parts:
        if part in _SENSITIVE_BASENAMES_CF:
            return True

    # Check filename against known sensitive files.
    return filename in _SENSITIVE_FILE_PATTERNS_CF


def _tool_path_roots() -> list[str]:
    """Return the list of directory roots that read_file / write_file
    may touch. Default: project data/ + system temp dirs. Extra roots
    are loaded from the ``tool_path_extra_roots`` setting.
    """
    roots: list[str] = []

    # Project data directory — the agent's primary workspace.
    from src.constants import DATA_DIR
    roots.append(DATA_DIR)

    # /tmp (and its macOS realpath /private/tmp).
    roots.append("/tmp")
    try:
        private_tmp = os.path.realpath("/tmp")
        if private_tmp != "/tmp":
            roots.append(private_tmp)
    except OSError:
        pass

    # $TMPDIR — per-user temp root on macOS (e.g. /var/folders/.../T/).
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(tmpdir)

    # Opt-in extra roots from settings.
    try:
        from src.settings import get_setting
        extra = get_setting("tool_path_extra_roots")
        if isinstance(extra, list):
            roots.extend(str(r) for r in extra if r)
    except Exception:
        pass

    # Deduplicate; resolve symlinks so containment is unambiguous.
    seen: set[str] = set()
    out: list[str] = []
    for r in roots:
        try:
            real = os.path.realpath(r)
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        out.append(real)
    return out


def _resolve_tool_path(raw_path: str) -> str:
    """Resolve and confine a model-supplied path.

    Order of checks:
      1. Non-empty path.
      2. Sensitive-subpath deny list (blocks .ssh, .gnupg, etc.
         even when the root is on the allowlist).
      3. Allowlist containment (must land under one of the roots).

    Returns the realpath on success. Raises ValueError on rejection.
    Symlinks are resolved before comparison.

    When a workspace is active for this turn, paths are confined to it instead
    of the default allowlist (see _resolve_tool_path_in_workspace).
    """
    ws = get_active_workspace()
    if ws:
        return _resolve_tool_path_in_workspace(ws, raw_path)
    if raw_path is None or not str(raw_path).strip():
        raise ValueError("path is required")
    expanded = os.path.expanduser(str(raw_path).strip())
    resolved = os.path.realpath(expanded)

    if _is_sensitive_path(resolved):
        raise ValueError(
            f"path '{raw_path}' is inside a sensitive directory "
            f"(e.g. .ssh, .gnupg) or matches a sensitive filename"
        )

    for root in _tool_path_roots():
        if resolved == root:
            return resolved
        try:
            common = os.path.commonpath([resolved, root])
        except ValueError:
            continue
        if common == root:
            return resolved
    raise ValueError(
        f"path '{raw_path}' is outside the allowed roots"
    )


def _resolve_tool_path_in_workspace(workspace: str, raw_path: str) -> str:
    """Confine a model-supplied path to the active workspace.

    Layered on top of upstream's path policy: the workspace is the allowed
    root (relative paths resolve under it; paths that escape it are rejected),
    and the sensitive-file deny list (.ssh, .gnupg, id_rsa, …) still applies
    inside it. When no workspace is set, callers use _resolve_tool_path (the
    default data/tmp allowlist) instead.
    """
    if raw_path is None or not str(raw_path).strip():
        raise ValueError("path is required")
    base = os.path.realpath(workspace)
    expanded = os.path.expanduser(str(raw_path).strip())
    candidate = expanded if os.path.isabs(expanded) else os.path.join(base, expanded)
    resolved = os.path.realpath(candidate)
    if _is_sensitive_path(resolved):
        raise ValueError(
            f"path '{raw_path}' is inside a sensitive directory "
            f"(e.g. .ssh, .gnupg) or matches a sensitive filename"
        )
    if resolved != base:
        # normcase so containment holds on case-insensitive filesystems
        # (Windows, default macOS): it lowercases on Windows and is a no-op on
        # POSIX. commonpath raises ValueError across Windows drives (C: vs D:)
        # or mixed abs/rel — both mean "outside", so the except rejects them.
        nbase = os.path.normcase(base)
        try:
            if os.path.commonpath([os.path.normcase(resolved), nbase]) != nbase:
                raise ValueError
        except ValueError:
            raise ValueError(f"path '{raw_path}' is outside the workspace ({workspace})")
    return resolved



# ---------------------------------------------------------------------------
# Active workspace (per-turn, context-local)
# ---------------------------------------------------------------------------
# Set ONCE in execute_tool_block from the request's `workspace`. The path
# resolvers (_resolve_tool_path / _resolve_search_root) and the subprocess cwd
# helper (agent_cwd) read it from here, so confinement is enforced in a single
# place: any tool that resolves paths through these helpers is confined
# automatically and cannot accidentally bypass the workspace. contextvars are
# task-local, so concurrent turns don't leak into each other.
_active_workspace: contextvars.ContextVar = contextvars.ContextVar(
    "agent_active_workspace", default=None
)


def get_active_workspace() -> Optional[str]:
    """The folder the agent is confined to this turn, or None."""
    return _active_workspace.get()


def vet_workspace(raw: str) -> Optional[str]:
    """Validate a requested workspace path at bind time.

    Returns the canonical path, or None when it is unusable: not a real
    directory, or itself a sensitive path (.ssh, .gnupg, ...). The in-workspace
    resolver deny-lists sensitive paths *inside* the workspace, but the
    empty-path search root is the workspace itself, so the root has to be
    vetted before it is ever bound.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    resolved = os.path.realpath(os.path.expanduser(raw))
    if not os.path.isdir(resolved) or _is_sensitive_path(resolved):
        return None
    # Reject filesystem roots: binding / (or a Windows drive/UNC root) as the
    # workspace would make every absolute path "inside" it, collapsing the
    # confinement into host-wide file access. A root is its own dirname, which
    # also covers C:\ and \\server\share without platform-specific lists.
    if os.path.dirname(resolved) == resolved:
        return None
    return resolved


def agent_cwd() -> str:
    """Working directory for agent subprocesses (bash/python/background jobs):
    the active workspace when set, else the persistent data dir."""
    return get_active_workspace() or _AGENT_WORKDIR


def get_mcp_manager():
    from src import agent_tools
    return agent_tools.get_mcp_manager()




def _resolve_search_root(raw_path: str) -> str:
    """Resolve + confine a code-nav path (grep/glob/ls).

    With a workspace active, the workspace folder is the root and a supplied
    path is confined inside it. Otherwise an empty path defaults to the agent's
    primary root (project data dir) and a supplied path is confined by the
    global allowlist + sensitive-file policy.
    """
    raw = (raw_path or "").strip()
    ws = get_active_workspace()
    if ws:
        return os.path.realpath(ws) if not raw else _resolve_tool_path_in_workspace(ws, raw)
    if not raw:
        roots = _tool_path_roots()
        return roots[0] if roots else os.path.realpath(".")
    return _resolve_tool_path(raw)

logger = logging.getLogger(__name__)


_ADMIN_TOOLS = {
    "app_api",
    "manage_endpoints",
    "manage_mcp",
    "manage_webhooks",
    "manage_tokens",
    "manage_settings",
    "download_model",
    "serve_model",
    "serve_preset",
    "stop_served_model",
    "cancel_download",
}


def _owner_is_admin(owner: Optional[str]) -> bool:
    """Mirror route-level admin behavior for agent tool execution."""
    return owner_is_admin_or_single_user(owner)

# ---------------------------------------------------------------------------
# MCP-backed tool helpers
# ---------------------------------------------------------------------------

# Map legacy tool names -> (MCP server_id, MCP tool_name)
_MCP_TOOL_MAP = {
    "bash":           ("bash",       "bash"),
    "python":         ("python",     "python"),
    "read_file":      ("filesystem", "read_file"),
    "write_file":     ("filesystem", "write_file"),
    "web_search":     ("web_search", "web_search"),
    "web_fetch":      ("web_fetch",  "web_fetch"),
    "generate_image": ("image_gen",  "generate_image"),
}
_EMAIL_MCP_OWNER_ARG = "_odysseus_owner"


def _parse_qualified_mcp_args(tool: str, content: str) -> tuple[Dict, Optional[str]]:
    raw = (content or "").strip()
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        if tool.startswith("mcp__email__"):
            return {}, "Email MCP tool arguments must be a JSON object."
        return {}, None
    if not isinstance(parsed, dict):
        if tool.startswith("mcp__email__"):
            return {}, "Email MCP tool arguments must be a JSON object."
        return {}, None
    return parsed, None


def _parse_generate_image(content: str) -> Dict:
    lines = content.strip().split("\n")
    args = {"prompt": lines[0].strip() if lines else ""}
    for i, key in enumerate(["model", "size", "quality"], 1):
        if len(lines) > i and lines[i].strip():
            args[key] = lines[i].strip()
    return args


def _parse_manage_memory(content: str) -> Dict:
    lines = content.strip().split("\n")
    action = lines[0].strip().lower() if lines else ""
    args = {"action": action}
    if action == "add":
        args["text"] = lines[1].strip() if len(lines) > 1 else ""
        if len(lines) > 2 and lines[2].strip():
            args["category"] = lines[2].strip().lower()
    elif action == "edit":
        args["memory_id"] = lines[1].strip() if len(lines) > 1 else ""
        args["text"] = lines[2].strip() if len(lines) > 2 else ""
    elif action == "delete":
        args["memory_id"] = lines[1].strip() if len(lines) > 1 else ""
    elif action == "search":
        args["text"] = lines[1].strip() if len(lines) > 1 else ""
    elif action == "list":
        if len(lines) > 1 and lines[1].strip():
            args["category"] = lines[1].strip().lower()
    return args


def _parse_write_file(content: str) -> Dict:
    lines = content.split("\n", 1)
    return {"path": lines[0].strip(), "content": lines[1] if len(lines) > 1 else ""}


_MCP_ARG_PARSERS: Dict[str, Callable[[str], Dict[str, str]]] = {
    "bash":           lambda c: {"command": c},
    "python":         lambda c: {"code": c},
    "web_search":     lambda c: {"query": c.split("\n")[0].strip()},
    "web_fetch":      lambda c: {"url": c.split("\n")[0].strip()},
    "read_file":      lambda c: {"path": c.split("\n")[0].strip()},
    "write_file":     _parse_write_file,
    "generate_image": _parse_generate_image,
    "manage_memory":  _parse_manage_memory,
}


# Primary argument key(s) for the legacy line-parsed tools. When a fenced
# block's content is a JSON object carrying one of these keys, it's structured
# inline args (the relaxed parser's ```web_search {"query": "..."}``` shape) —
# use the object directly instead of letting the line-based parsers wrap the
# whole JSON string as the query/url/path/prompt. Keyed off membership only
# (the primary key never changes), so this can't drift; an unrecognized object
# safely falls through to the line-based parser, i.e. the previous behavior.
#
# IMPORTANT — this only covers the MCP path. _build_mcp_args is reached via
# _call_mcp_tool only for _MCP_TOOL_MAP tools (so an entry outside that map is
# dead, as manage_memory was). And of these, only generate_image has a live MCP
# server today; web_search/web_fetch/read_file/write_file have none, so they run
# via _direct_fallback -> TOOL_HANDLERS, whose handlers decode JSON themselves
# (see ReadFileTool/WriteFileTool/WebSearchTool/WebFetchTool). The entries here
# are kept as defense-in-depth for if/when those servers are added. The live
# fix for each server-less tool lives in its handler. test_write_file_inline_
# json_args and test_mcp_json_primary_keys_are_all_live pin both halves.
_MCP_JSON_PRIMARY_KEYS: Dict[str, tuple] = {
    "web_search":     ("query", "queries"),
    "web_fetch":      ("url",),
    "read_file":      ("path",),
    "write_file":     ("path",),
    "generate_image": ("prompt",),
}


def _build_mcp_args(tool: str, content: str) -> Dict:
    """Convert fenced-block text content to structured MCP arguments."""
    primaries = _MCP_JSON_PRIMARY_KEYS.get(tool)
    if primaries and content.strip().startswith("{"):
        try:
            decoded = json.loads(content.strip())
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, dict) and any(k in decoded for k in primaries):
            return decoded
    parser = _MCP_ARG_PARSERS.get(tool)
    return parser(content) if parser else {}


async def _call_mcp_tool(
    tool: str,
    content: str,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
) -> Dict:
    """Route a legacy tool call through the MCP manager, with direct fallbacks."""
    mcp = get_mcp_manager()
    if not mcp:
        return await _direct_fallback(tool, content, progress_cb=progress_cb) or {"error": f"MCP manager not available for tool '{tool}'", "exit_code": 1}

    server_id, tool_name = _MCP_TOOL_MAP[tool]
    qualified = f"mcp__{server_id}__{tool_name}"
    args = _build_mcp_args(tool, content)
    result = await mcp.call_tool(qualified, args)

    # If MCP server not connected, try direct fallback
    if isinstance(result, dict) and result.get("exit_code") == 1 and "not connected" in result.get("error", ""):
        fallback = await _direct_fallback(tool, content, progress_cb=progress_cb)
        if fallback:
            return fallback

    # generate_image runs as a text-only MCP tool, so the saved image URL never
    # reaches the agent loop's structured forwarding (which renders the image via
    # buildImageBubble on result["image_url"]). Lift it out of the tool's stdout so
    # the image renders deterministically — no dependence on the model echoing the
    # URL into its prose (which it mangles/hallucinates).
    if tool == "generate_image":
        _promote_image_fields(result)

    return result


def _promote_image_fields(result: Dict) -> None:
    """Lift the image URL (+ prompt/model/size) from a successful generate_image MCP
    text result into structured fields the agent loop already forwards to
    buildImageBubble. Only acts on a dict result with exit_code 0; matches the
    generated-image URL by pattern (absolute or relative) so it's robust to the
    result's wording."""
    if not isinstance(result, dict) or result.get("exit_code") != 0:
        return
    out = result.get("stdout") or ""
    m = re.search(r'(?:https?://[^\s)\]]+)?/api/generated-image/[A-Za-z0-9._-]+', out)
    if not m:
        return
    result["image_url"] = m.group(0).strip()
    for field, pat in (
        ("image_prompt", r'^Generated image for:\s*(.+)$'),
        ("image_model", r'^model:\s*(.+)$'),
        ("image_size", r'^size:\s*(.+)$'),
    ):
        fm = re.search(pat, out, re.M)
        if fm:
            result[field] = fm.group(1).strip()


_BG_MARKERS = {"#!bg", "#bg", "# bg", "#background", "# background", "@background", "# @background"}


def _split_bg_marker(content: str):
    """If the bash content's first non-empty line is a background marker
    (e.g. `#!bg`), return (True, command_without_marker); else (False, content)."""
    lines = content.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip().lower() in _BG_MARKERS:
        del lines[i]
        return True, "\n".join(lines).strip()
    return False, content


async def _direct_fallback(
    tool: str,
    content: str,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
) -> Optional[Dict]:
    _subproc_env = {
        **os.environ,
        "TERM": "xterm-256color",
        "COLUMNS": "120",
        "LINES": "40",
        "HOME": _AGENT_WORKDIR,
    }

    try:
        ctx = {
            "progress_cb": progress_cb,
            "subproc_env": _subproc_env,
            "session_id": session_id,
            "owner": owner,
        }

        from src.agent_tools import TOOL_HANDLERS
        if tool in TOOL_HANDLERS:
            return await TOOL_HANDLERS[tool](content, ctx)

    except Exception as e:
        return {"error": f"{tool}: {e}", "exit_code": 1}

    return None


# Real, the index was built by vault_ingest.py running on the HOST, where
# the vault lives at /home/dk/gdrive/Obsidian. Inside this container, the
# same content is mounted at /app/vault_data instead -- confirmed directly
# (2026-08-23) that body_path values from the index do not exist as-is
# inside the container without this translation.
_VAULT_INDEX_HOST_ROOT = "/home/dk/gdrive/Obsidian"
_VAULT_INDEX_CONTAINER_ROOT = "/app/vault_data"


def _load_vault_index():
    """Real, loads the structured vault index built on the host by
    vault_ingest.py, mounted read-only at /app/vault_index.jsonl (added
    2026-08-23). Returns an empty list, not an error, if the index isn't
    present -- callers fall back to the pre-2026-08-23 three-folder scan
    so this never becomes a hard dependency.

    Translates each record's body_path from the host path (where
    vault_ingest.py actually ran) to this container's own mount point,
    since the two are different paths to the same underlying content."""
    index_path = "/app/vault_index.jsonl"
    if not os.path.isfile(index_path):
        return []
    records = []
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                bp = record.get("body_path", "")
                if bp.startswith(_VAULT_INDEX_HOST_ROOT):
                    record["body_path"] = bp.replace(
                        _VAULT_INDEX_HOST_ROOT, _VAULT_INDEX_CONTAINER_ROOT, 1
                    )
                records.append(record)
    except OSError:
        return []
    return records


def search_vault_impl(query: str) -> str:
    """Real, in-process vault search. No search tool existed for this
    deployment before 2026-08-19 (confirmed directly, GitHub issue #14,
    and two real live tests) -- the container had no filesystem access
    to the real, host Obsidian vault at all.

    Extracted 2026-08-19 into a standalone function so both the real
    tool dispatch (below) and the deterministic trigger
    (detect_vault_search_trigger + its real caller in
    routes/chat_routes.py) share the same real logic, rather than
    duplicating it.

    Backend swapped 2026-08-23 to use the structured index built by
    vault_ingest.py/vault_search.py (see
    knowledge/infrastructure-constraints/operations/never-ingest-generated-directories.md
    for why a denylist-based ingestion pipeline was built separately from
    this tool's own, pre-existing allowlist-based scope). The external
    interface (single query string in, formatted snippet string out) is
    deliberately unchanged -- this is a backend swap, not a new tool, so
    existing trigger wiring, schema, and UI expectations keep working
    without modification.
    """
    vault_root = "/app/vault_data"
    index_records = _load_vault_index()
    effective_query = query  # real, safe default; only overridden below when a category is actually detected

    if index_records:
        # Real, structured path: use the index to find candidate files,
        # optionally narrowed if the query text names one of the real,
        # actual top-level vault categories directly (e.g. "search my
        # Portfolio notes for X"). Falls through to a full-index scan if
        # no category name appears in the query.
        query_lower = query.lower()
        known_categories = {r["category"].lower() for r in index_records}
        matched_category = next(
            (c for c in known_categories if c != "root" and c in query_lower),
            None,
        )
        # Real, when a category name is detected, strip it (and common
        # framing words around it) out of the actual search text -- the
        # raw query otherwise never matches any real file verbatim (e.g.
        # "search my Portfolio notes for rung" is not a real substring
        # anywhere, even though "rung" alone genuinely is). Confirmed
        # directly this was needed: the unmodified query returned zero
        # matches for a real, valid category-scoped search.
        effective_query = query
        if matched_category:
            for framing_word in (matched_category, "notes", "note", "search", "my", "for", "in"):
                pattern = re.compile(re.escape(framing_word), re.IGNORECASE)
                effective_query = pattern.sub(" ", effective_query)
            effective_query = " ".join(effective_query.split()) or query
        # Real, preserves the original function's own deliberate choice to
        # never scan the vault root (confirmed directly: 40+ unrelated
        # files there would slow every search on an already I/O-slow
        # mount). Root-level records are only included if the query
        # explicitly names "root" as a category -- never by default.
        candidates = [
            r for r in index_records
            if r["category"].lower() != "root"
            and (matched_category is None or r["category"].lower() == matched_category)
        ]
        search_dirs = None  # not used on this path
        all_files = []
        seen_paths = set()
        for r in candidates:
            fpath = r.get("body_path")
            if not fpath or fpath in seen_paths or not os.path.isfile(fpath):
                continue
            seen_paths.add(fpath)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    all_files.append((fpath, f.read()))
            except Exception:
                continue
    else:
        # Real, pre-2026-08-23 fallback: the original three-folder scan,
        # kept so this tool degrades gracefully rather than breaking if
        # the index mount or the index file itself is ever unavailable.
        search_dirs = []
        for sub in ("Portfolio", "Thesis", "Watchlist"):
            sub_path = os.path.join(vault_root, sub)
            if os.path.isdir(sub_path):
                search_dirs.append(sub_path)
        all_files = []
        seen_paths = set()
        for d in search_dirs:
            for fname in os.listdir(d):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(d, fname)
                if fpath in seen_paths or not os.path.isfile(fpath):
                    continue
                seen_paths.add(fpath)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        all_files.append((fpath, f.read()))
                except Exception:
                    continue

    # Real, added 2026-08-19 after a live test caught this directly:
    # a natural, verbose query like "the 'Man in the Car Paradox'
    # chapter from The Psychology of Money" doesn't appear as one
    # exact, contiguous substring anywhere -- the file has "Man in the
    # Car Paradox" and "Psychology of Money" as separate phrases. If
    # the query contains an explicit quoted portion, search for that
    # first (the real, most likely intended search term); only fall
    # back to the full, literal query string if no quotes are present
    # or the quoted portion itself doesn't match anything.
    quoted = re.findall(r"['\u2018\u2019\"]([^'\u2018\u2019\"]{3,})['\u2018\u2019\"]", query)
    search_terms = quoted + [effective_query] if quoted else [effective_query]

    for term in search_terms:
        term_lower = term.lower()
        matches = []
        for fpath, text in all_files:
            if term_lower in text.lower():
                idx = text.lower().find(term_lower)
                # Real, widened from the original 200 chars (which cut
                # snippets off mid-word/mid-sentence, confirmed directly
                # by a real, live "onlookers picture themse..." example)
                # to 400, and snapped to real word boundaries rather than
                # an arbitrary character count, so snippets read cleanly.
                raw_start = max(0, idx - 400)
                raw_end = min(len(text), idx + len(term) + 400)

                start = raw_start
                if raw_start > 0:
                    space_idx = text.find(" ", raw_start)
                    if 0 <= space_idx < raw_start + 40:
                        start = space_idx + 1

                end = raw_end
                if raw_end < len(text):
                    space_idx = text.rfind(" ", max(raw_start, raw_end - 40), raw_end)
                    if space_idx != -1:
                        end = space_idx

                snippet = text[start:end].strip()
                prefix = "..." if start > 0 else ""
                suffix = "..." if end < len(text) else ""
                rel_path = os.path.relpath(fpath, vault_root)
                matches.append(f"### {rel_path}\n{prefix}{snippet}{suffix}")
        if matches:
            return f"Found {len(matches)} matching file(s) for query '{term}':\n\n" + "\n\n".join(matches[:5])

    return f"No matches found in the vault for '{query}'."


# Real, deliberately narrow, conservative trigger set -- added 2026-08-19
# after confirming directly (live test) that models don't reliably choose
# to call search_vault on their own, matching the same tool-invocation
# reliability limitation found repeatedly elsewhere in this engagement.
# Matches process_correction_command's own pattern in src/memory.py:
# bypass the model entirely for genuinely unambiguous phrasing, rather
# than trying to nudge the model into calling the tool more often.
# Deliberately conservative -- false negatives (missing a real vault
# query) are far safer than false positives (silently intercepting an
# unrelated message and answering only from vault content).
VAULT_SEARCH_TRIGGERS = [
    re.compile(r"^search (?:my )?(?:vault|notes|obsidian) for (.+)$", re.IGNORECASE),
    re.compile(r"^what does (?:my )?(?:vault|notes|obsidian) say about (.+?)\??$", re.IGNORECASE),
    re.compile(r"^find (?:in|from) (?:my )?(?:vault|notes|obsidian)[:,]? (.+)$", re.IGNORECASE),
]


def detect_vault_search_trigger(message: str) -> Optional[str]:
    """Real, direct, deterministic check: does this message explicitly,
    unambiguously ask to search the vault? Returns the extracted query
    string if so, else None. Only matches an explicit vault/notes/
    Obsidian reference -- does not try to infer general "does the user
    want vault content" intent from phrasing alone, since that's exactly
    the kind of judgment call that's proven unreliable to leave to the
    model tonight, and a wrong guess here would silently intercept an
    unrelated message."""
    stripped = message.strip()
    for pattern in VAULT_SEARCH_TRIGGERS:
        m = pattern.match(stripped)
        if m:
            query = m.group(1).strip().rstrip("?.")
            if len(query) >= 3:  # real, minimal sanity floor, matches process_correction_command's own length-floor pattern
                return query
    return None


# Real, deliberately narrow, conservative trigger for the "how many
# shares of X do I own" class of question -- added 2026-08-19 after
# confirming directly (repeated live tests) that models frequently
# either call the wrong real tool (qwen2.5:7b -> lookup_ticker instead
# of get_portfolio_context) or correctly call the right tool but then
# mis-synthesize the raw document (the real, earlier "17 shares" bug).
# This goes one step further than a tool-selection trigger: it answers
# directly from the already-parsed, confirmed holdings data
# (src/portfolio_parser.py), bypassing the model's synthesis step
# entirely for this narrow, high-value, unambiguous case -- not just
# forcing the right tool call and hoping the model reads the result
# correctly.
HOLDINGS_QUERY_TRIGGER = re.compile(
    r"how many shares (?:of|in) ([a-zA-Z][a-zA-Z0-9.\-]{0,9}) do i (?:own|have)\??$",
    re.IGNORECASE,
)


def detect_holdings_query(message: str) -> Optional[str]:
    """Real, direct, deterministic check: does this message
    unambiguously ask for a specific ticker's share count? Returns the
    extracted ticker symbol (uppercased) if so, else None. Deliberately
    narrow -- only the single, canonical 'how many shares of X do I
    own' phrasing, not general portfolio questions (balance, strategy,
    trading rules), which still need the full document and remain the
    model's responsibility via get_portfolio_context."""
    m = HOLDINGS_QUERY_TRIGGER.search(message.strip())
    if not m:
        return None
    return m.group(1).upper()


def answer_holdings_query(ticker: str) -> str:
    """Real, direct, deterministic answer for a holdings query --
    reads and parses data/portfolio_context.md directly via the
    already-built, already-tested src/portfolio_parser.py, and states
    the confirmed share count (plus any pending order) without any
    model synthesis step at all. This is the real, root-cause fix for
    the "17 shares" class of bug (a confirmed holding summed with a
    separate, unexecuted pending order): there is no synthesis step
    here for a model to get wrong."""
    import os
    try:
        from src.portfolio_parser import parse_portfolio_context
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "portfolio_context.md")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        parsed = parse_portfolio_context(text)
    except Exception as e:
        # Real, fixed 2026-08-28 (CodeQL, "Information exposure through an
        # exception"): the raw exception message used to flow directly into
        # this chat-facing string. Log the real detail server-side instead,
        # return a generic message to the caller.
        logger.exception(f"Failed to read/parse portfolio data for holdings query: {e}")
        return "Could not read portfolio data to answer this request."

    if ticker not in parsed.confirmed_holdings:
        pending_buy = parsed.pending_qty_for(ticker, "BUY")
        if pending_buy:
            return f"You have no confirmed holding of {ticker}, but there is a separate, unexecuted pending buy order for {pending_buy:g} shares."
        return f"You don't currently have a confirmed holding of {ticker} in the portfolio data."

    confirmed = parsed.confirmed_holdings[ticker]
    pending_buy = parsed.pending_qty_for(ticker, "BUY")
    pending_sell = parsed.pending_qty_for(ticker, "SELL")
    answer = f"You currently own {confirmed:g} shares of {ticker} (confirmed)."
    if pending_buy:
        answer += f" There is also a separate, unexecuted pending buy order for {pending_buy:g} more shares."
    if pending_sell:
        answer += f" There is also a separate, unexecuted pending sell order for {pending_sell:g} shares."
    return answer


async def _document_tool_dispatch(
    tool: str,
    content: str,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
) -> Optional[Dict]:
    """Route a document tool through TOOL_HANDLERS with the right ctx shape."""
    from src.agent_tools import TOOL_HANDLERS
    ctx = {"session_id": session_id, "owner": owner}
    if tool in TOOL_HANDLERS:
        return await TOOL_HANDLERS[tool](content, ctx)
    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def execute_tool_block(
    block: Any,
    session_id: Optional[str] = None,
    disabled_tools: Optional[set] = None,
    owner: Optional[str] = None,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    workspace: Optional[str] = None,
    tool_policy: Optional[Any] = None,
) -> Tuple[str, Dict]:
    """Execute a single tool block. Returns (description, result_dict).

    Thin wrapper: bind the per-turn workspace (so the path resolvers + subprocess
    cwd confine to it) for the duration of this call, then delegate. Reset on the
    way out so the binding never leaks to the next tool call.
    """
    token = _active_workspace.set(workspace or None)
    try:
        output = await _execute_tool_block_impl(
            block,
            session_id=session_id,
            disabled_tools=disabled_tools,
            owner=owner,
            progress_cb=progress_cb,
            tool_policy=tool_policy,
        )
        return output
    finally:
        _active_workspace.reset(token)


async def _execute_tool_block_impl(
    block: Any,
    session_id: Optional[str] = None,
    disabled_tools: Optional[set] = None,
    owner: Optional[str] = None,
    progress_cb: Optional[Callable[[Dict], Awaitable[None]]] = None,
    tool_policy: Optional[Any] = None,
) -> Tuple[str, Dict]:
    """Execute a single tool block. Returns (description, result_dict).

    `progress_cb` is forwarded to long-running subprocess tools
    (bash, python) so the agent loop can emit `tool_progress` SSE
    events while the command is in flight. Ignored by other tools.
    """
    from src.tool_implementations import (
        do_search_chats, do_manage_tasks,
        do_manage_skills, do_skill_introspect, do_api_call, do_manage_notes,
        do_manage_calendar,
        do_download_model, do_serve_model, do_list_served_models, do_stop_served_model,
        do_tail_serve_output,
        do_list_downloads, do_cancel_download, do_search_hf_models, do_list_cached_models,
        do_list_serve_presets, do_serve_preset, do_adopt_served_model,
        do_list_cookbook_servers,
        do_edit_image, do_trigger_research, do_manage_research, do_resolve_contact,
        do_manage_contact,
        do_vault_search, do_vault_get, do_vault_unlock,
        do_app_api,
    )

    # HACK:
    # This is a temporary workaround for a circular dependency between
    # tool_execution.py and agent_tools.__init__.py.
    #
    # See issue #4277:
    # refactor(tools): Move the registry from __init__.py into a
    # dedicated registry.py module.
    #
    # Do not copy this pattern elsewhere. This import should be removed
    # once the registry refactor is completed.
    try:
        agent_tools_mod = __import__("src.agent_tools", fromlist=["TOOL_HANDLERS"])
        dynamic_handlers = getattr(agent_tools_mod, "TOOL_HANDLERS", {})
    except ImportError:
        dynamic_handlers = {}

    tool = block.tool_type
    content = block.content

    # The block/disable gates below must match every policy-equivalent
    # spelling of the tool name (bare email names alias their mcp__email__
    # form — see email_tool_policy_names), not just the spelling the model
    # happened to emit.
    policy_names = email_tool_policy_names(tool)

    # Misformatted tool call detection: model put JSON inside ```python``` (or
    # similar) without naming the tool. Common with MiniMax-style outputs.
    # Return a helpful error so the model retries with the correct format.
    if tool in ("python", "json", "xml") and content.strip().startswith("{") and content.strip().endswith("}"):
        try:
            parsed = json.loads(content.strip())
            if isinstance(parsed, dict):
                desc = f"{tool}: misformatted tool call"
                result = {
                    "error": (
                        f"You wrote a JSON object inside a ```{tool}``` block, but that's not a tool call.\n"
                        "To call a tool, use the tool name as the fence tag, e.g.\n"
                        "```resolve_contact\n"
                        "{\"name\": \"...\"}\n"
                        "```\n"
                        "or\n"
                        "```send_email\n"
                        "{\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\"}\n"
                        "```"
                    ),
                    "exit_code": 1,
                }
                return desc, result
        except (ValueError, TypeError):
            pass

    # Reject tools that the user has disabled for this request
    if disabled_tools and not policy_names.isdisjoint(disabled_tools):
        desc = f"{tool}: BLOCKED"
        result = {"error": f"Tool '{tool}' is disabled by user.", "exit_code": 1}
        logger.info(f"Tool blocked by user: {tool}")
        return desc, result

    if tool_policy and any(tool_policy.blocks(name) for name in policy_names):
        desc = f"{tool}: BLOCKED"
        result = {
            "error": f"Execution of tool '{tool}' is forbade by the active guide-only policy.",
            "exit_code": 1,
        }
        logger.warning("Tool policy blocked tool=%s", tool)
        return desc, result

    if tool in _ADMIN_TOOLS and not _owner_is_admin(owner):
        desc = f"{tool}: BLOCKED"
        result = {"error": f"Tool '{tool}' requires an admin user.", "exit_code": 1}
        logger.warning("Admin tool blocked for non-admin owner=%r tool=%s", owner, tool)
        return desc, result

    if is_public_blocked_tool(tool) and not _owner_is_admin(owner):
        desc = f"{tool}: BLOCKED"
        result = {
            "error": (
                f"Tool '{tool}' is restricted to admin users on this deployment. "
                "Ask an admin to perform this action or grant the needed permission."
            ),
            "exit_code": 1,
        }
        logger.warning("Public tool policy blocked owner=%r tool=%s", owner, tool)
        return desc, result


    # Background execution: a `bash` block whose first line is the `#!bg`
    # marker runs DETACHED — returns a job id immediately so the chat stream
    # isn't held open for a multi-minute install/ffmpeg/download. The always-on
    # monitor re-invokes the agent with the full output when the job finishes.
    if tool == "bash" and session_id:
        _is_bg, _bg_cmd = _split_bg_marker(content)
        if _is_bg and _bg_cmd:
            from src import bg_jobs
            rec = bg_jobs.launch(_bg_cmd, session_id=session_id, cwd=agent_cwd())
            short = _bg_cmd.strip().split(chr(10))[0][:80]
            desc = f"bash (background): {short}"
            result = {
                "output": (
                    f"Started background job `{rec['id']}`. It is running detached; "
                    f"do NOT wait for it or poll it. You will be automatically re-invoked "
                    f"with its full output when it finishes. Continue with other work, or "
                    f"end your turn now and resume when the result arrives. If the user "
                    f"later asks to check progress or stop it, call the manage_bg_jobs "
                    f"tool yourself (output or kill); do not tell them to run a tool "
                    f"command, and do not surface raw tool syntax in your reply."
                ),
                "exit_code": 0,
                "bg_job_id": rec["id"],
            }
            logger.info(f"Tool executed: {desc} -> bg job {rec['id']}")
            return desc, result

    # Route MCP-extracted tools through the MCP manager. Forward
    # the progress callback so long-running subprocess tools
    # (bash, python) can stream `tool_progress` events to the UI.
    if tool in _MCP_TOOL_MAP:
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}"
        result = await _call_mcp_tool(tool, content, progress_cb=progress_cb)
    elif tool in ("grep", "glob", "ls", "get_workspace"):
        # Code-navigation tools — no MCP server; run the direct implementation.
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}"
        result = await _direct_fallback(tool, content, progress_cb=progress_cb) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool in ("apply_patch", "todowrite"):
        first_line = content.split(chr(10))[0][:80]
        desc = f"{tool}: {first_line}" if first_line else tool
        result = await _direct_fallback(tool, content, session_id=session_id, owner=owner) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool == "manage_bg_jobs":
        # Inspect/kill detached `bash` jobs; needs session_id to scope to chat.
        desc = f"manage_bg_jobs: {content.split(chr(10))[0][:80]}"
        result = await _direct_fallback(tool, content, session_id=session_id, owner=owner) \
            or {"error": "manage_bg_jobs: execution failed", "exit_code": 1}
    elif tool in ("create_document", "update_document", "edit_document",
                  "suggest_document", "manage_documents"):
        desc = f"{tool}: {content.split(chr(10))[0][:80]}"
        result = await _document_tool_dispatch(tool, content, session_id, owner) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
        if tool in ("edit_document", "suggest_document") and "title" in (result or {}):
            desc = f"{tool}: {result.get('title', '')}"
    elif tool == "search_chats":
        query = content.split("\n")[0].strip()
        desc = f"search_chats: {query[:80]}"
        result = await do_search_chats(query, owner=owner)
    elif tool in ("chat_with_model", "ask_teacher", "list_models"):
        # Migrated to the agent_tools registry (#3629): dispatched through
        # TOOL_HANDLERS with the owner/session ctx these tools need, instead
        # of the legacy dispatch_ai_tool elif. The impls live in
        # src/agent_tools/model_interaction_tools.py.
        first_line = content.split(chr(10))[0].strip()[:60]
        desc = f"{tool}: {first_line}" if first_line else tool
        result = await _document_tool_dispatch(tool, content, session_id, owner) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool in ("create_session", "list_sessions", "send_to_session", "manage_session"):
        # Migrated to the agent_tools registry (#3629): dispatched through
        # TOOL_HANDLERS with the owner/session ctx these tools need. The impls
        # live in src/agent_tools/session_tools.py.
        first_line = content.split(chr(10))[0].strip()[:60]
        desc = f"{tool}: {first_line}" if first_line else tool
        result = await _document_tool_dispatch(tool, content, session_id, owner) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool == "create_document_office":
        desc = "create_document_office"
        try:
            import json as _json
            import os
            import sys as _sys
            _sys.path.insert(0, "/app/data/scripts")
            from create_document import make_docx, make_xlsx, make_pptx, make_pdf

            args = _json.loads(content) if content else {}
            fmt = args.get("format")
            filename = args.get("filename", f"document.{fmt}")
            if not filename.endswith(f".{fmt}"):
                filename = f"{filename}.{fmt}"
            out_path = os.path.join("/app/data/uploads", filename)

            if fmt == "docx":
                path = make_docx(args.get("title", ""), args.get("sections", []), out_path)
            elif fmt == "xlsx":
                path = make_xlsx(args.get("rows", []), out_path)
            elif fmt == "pptx":
                path = make_pptx(args.get("slides", []), out_path)
            elif fmt == "pdf":
                path = make_pdf(args.get("title", ""), args.get("sections", []), out_path)
            else:
                raise ValueError(f"unsupported format: {fmt}")

            size = os.path.getsize(path)
            result = {"stdout": f"Created {path} ({size} bytes)", "stderr": "", "exit_code": 0}
        except Exception as e:
            result = {"error": f"create_document_office: {e}", "exit_code": 1}
    elif tool == "get_portfolio_context":
        desc = "get_portfolio_context"
        try:
            import os
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "portfolio_context.md")
            with open(path) as f:
                text = f.read()
            # Real, added 2026-08-17: a compact, explicit confirmed-vs-pending
            # summary, prepended ahead of the full raw document. Added after
            # confirming directly (repro_harness.py, 3 independent models,
            # qwen3-14b-longctx / gemma4-e2b-longctx / nemotron) that models
            # given the full, correct context were consistently summing a
            # confirmed holding with a real, separate, still-unexecuted
            # pending order into a premature total (KTOS: 16 confirmed + 1
            # pending buy -> reported as "17 shares", confidently, every
            # time). See src/portfolio_parser.py for the real parsing logic;
            # this does not replace the full document below, just makes the
            # confirmed/pending distinction explicit before the model has to
            # infer it from table position alone.
            try:
                from src.portfolio_parser import parse_portfolio_context
                parsed = parse_portfolio_context(text)
                if parsed.confirmed_holdings:
                    summary_lines = [
                        "## CONFIRMED vs PENDING (computed, not manually maintained -- see full tables below for source)",
                        "Do NOT add pending_buy to confirmed_shares -- pending orders have NOT executed.",
                        "",
                        "| Ticker | confirmed_shares | pending_buy | pending_sell |",
                        "|--------|------------------|-------------|--------------|",
                    ]
                    for ticker in sorted(parsed.confirmed_holdings):
                        confirmed = parsed.confirmed_holdings[ticker]
                        pb = parsed.pending_qty_for(ticker, "BUY")
                        ps = parsed.pending_qty_for(ticker, "SELL")
                        if pb or ps:
                            summary_lines.append(f"| {ticker} | {confirmed:g} | {pb:g} | {ps:g} |")
                    if len(summary_lines) > 5:
                        text = "\n".join(summary_lines) + "\n\n---\n\n" + text
            except Exception:
                pass  # real, deliberate: never let the summary computation break the underlying tool
            result = {"stdout": text, "stderr": "", "exit_code": 0}
        except Exception as e:
            result = {"error": f"get_portfolio_context: {e}", "exit_code": 1}
    elif tool == "search_vault":
        desc = "search_vault"
        try:
            args = json.loads(content) if content else {}
            query = (args.get("query") or "").strip()
            if not query:
                result = {"error": "search_vault requires a non-empty 'query' argument", "exit_code": 1}
            else:
                output = search_vault_impl(query)
                result = {"stdout": output, "stderr": "", "exit_code": 0}
        except Exception as e:
            result = {"error": f"search_vault: {e}", "exit_code": 1}
    elif tool in ("pipeline", "manage_memory", "ui_control"):
        from src.ai_interaction import dispatch_ai_tool
        desc, result = await dispatch_ai_tool(tool, content, session_id, owner=owner)
    elif tool == "manage_tasks":
        desc = "manage_tasks"
        result = await do_manage_tasks(content, owner=owner)
    elif tool == "skill_introspect":
        desc = "skill_introspect"
        result = await do_skill_introspect(content, owner=owner)
    elif tool == "manage_skills":
        desc = "manage_skills"
        result = await do_manage_skills(content, owner=owner)
    elif tool == "api_call":
        first_line = content.split("\n")[0].strip()[:60]
        desc = f"api_call: {first_line}"
        result = await do_api_call(content)
    elif tool in ("manage_endpoints", "manage_mcp", "manage_webhooks", "manage_tokens", "manage_settings"):
        # Registry-dispatched (agent_tools.admin_tools); owner threaded for ownership/admin checks.
        desc = tool
        result = await _direct_fallback(tool, content, owner=owner) \
            or {"error": f"{tool}: execution failed", "exit_code": 1}
    elif tool == "manage_notes":
        desc = "manage_notes"
        result = await do_manage_notes(content, owner=owner)
    elif tool == "manage_calendar":
        desc = "manage_calendar"
        result = await do_manage_calendar(content, owner=owner)
    elif tool == "download_model":
        desc = "download_model"
        result = await do_download_model(content, owner=owner)
    elif tool == "serve_model":
        desc = "serve_model"
        result = await do_serve_model(content, owner=owner)
    elif tool == "list_served_models":
        desc = "list_served_models"
        result = await do_list_served_models(content, owner=owner)
    elif tool == "stop_served_model":
        desc = "stop_served_model"
        result = await do_stop_served_model(content, owner=owner)
    elif tool == "tail_serve_output":
        desc = "tail_serve_output"
        result = await do_tail_serve_output(content, owner=owner)
    elif tool == "list_downloads":
        desc = "list_downloads"
        result = await do_list_downloads(content, owner=owner)
    elif tool == "cancel_download":
        desc = "cancel_download"
        result = await do_cancel_download(content, owner=owner)
    elif tool == "search_hf_models":
        desc = "search_hf_models"
        result = await do_search_hf_models(content, owner=owner)
    elif tool == "list_cached_models":
        desc = "list_cached_models"
        result = await do_list_cached_models(content, owner=owner)
    elif tool == "app_api":
        desc = "app_api"
        result = await do_app_api(content, owner=owner)
    elif tool == "list_serve_presets":
        desc = "list_serve_presets"
        result = await do_list_serve_presets(content, owner=owner)
    elif tool == "serve_preset":
        desc = "serve_preset"
        result = await do_serve_preset(content, owner=owner)
    elif tool == "adopt_served_model":
        desc = "adopt_served_model"
        result = await do_adopt_served_model(content, owner=owner)
    elif tool == "list_cookbook_servers":
        desc = "list_cookbook_servers"
        result = await do_list_cookbook_servers(content, owner=owner)
    elif tool == "edit_image":
        desc = "edit_image"
        result = await do_edit_image(content, owner=owner)
    elif tool == "edit_file":
        result = await _direct_fallback(tool, content) or {"error": "edit failed", "exit_code": 1}
        desc = result.get("output") or result.get("error") or "edit_file"
    elif tool == "trigger_research":
        desc = "trigger_research"
        result = await do_trigger_research(content, owner=owner)
    elif tool == "manage_research":
        desc = "manage_research"
        result = await do_manage_research(content, owner=owner)
    elif tool == "resolve_contact":
        desc = "resolve_contact"
        result = await do_resolve_contact(content, owner=owner)
    elif tool == "manage_contact":
        desc = "manage_contact"
        result = await do_manage_contact(content, owner=owner)
    elif tool == "vault_search":
        desc = "vault_search"
        result = await do_vault_search(content, owner=owner)
    elif tool == "vault_get":
        desc = "vault_get"
        result = await do_vault_get(content, owner=owner)
    elif tool == "vault_unlock":
        desc = "vault_unlock"
        result = await do_vault_unlock(content, owner=owner)
    elif tool in BUILTIN_EMAIL_TOOLS:
        # Bare email tool name from fenced-block models (e.g. Ollama) — route to MCP email server.
        # Non-admin owners never reach here: BUILTIN_EMAIL_TOOLS ⊆ NON_ADMIN_BLOCKED_TOOLS,
        # so is_public_blocked_tool() above already rejected them.
        mcp = get_mcp_manager()
        qualified = f"mcp__email__{tool}"
        desc = f"email: {tool}"
        if mcp:
            _raw = content.strip()
            args = {}
            _args_error = None
            if _raw:
                # A non-empty body is always meant to be the call's arguments,
                # and every email tool takes a JSON object. Anything that
                # isn't one is a correctable error — NOT a silent empty-args
                # call, which would read the DEFAULT mailbox/folder instead of
                # the one the model meant (#3966 class). Only an EMPTY body
                # keeps the no-arg path (e.g. ```list_email_accounts```).
                try:
                    parsed = json.loads(_raw)
                except (json.JSONDecodeError, TypeError) as _je:
                    # Covers both `{account: "work"}` (looks like JSON, bad)
                    # and `account: work` (not JSON at all).
                    _args_error = (
                        f"'{tool}' arguments are not valid JSON ({_je}). "
                        'Send a JSON object, e.g. {"account": "work"} — '
                        "keys and string values need double quotes."
                    )
                else:
                    if isinstance(parsed, dict):
                        args = parsed
                    else:
                        _args_error = (
                            f"'{tool}' arguments must be a JSON object, "
                            'e.g. {"uid": "..."} — got a JSON array/value instead.'
                        )
            if _args_error is not None:
                result = {"error": _args_error, "exit_code": 1}
            else:
                if owner:
                    args = dict(args)
                    args[_EMAIL_MCP_OWNER_ARG] = owner
                result = await mcp.call_tool(qualified, args)
        else:
            result = {"error": "MCP manager not available", "exit_code": 1}
    elif tool.startswith("mcp__"):
        # MCP tool dispatch
        mcp = get_mcp_manager()
        if mcp:
            desc = f"mcp: {tool}"
            args, parse_error = _parse_qualified_mcp_args(tool, content)
            if parse_error:
                result = {"error": parse_error, "exit_code": 1}
            else:
                if tool.startswith("mcp__email__") and owner:
                    args = dict(args)
                    args[_EMAIL_MCP_OWNER_ARG] = owner
                result = await mcp.call_tool(tool, args)
        else:
            desc = f"mcp: {tool}"
            result = {"error": "MCP manager not available", "exit_code": 1}


    elif tool in dynamic_handlers:
        first_line = content.split(chr(10))[0][:80]
        desc = f"registry: {tool} {first_line}".strip()
        res = await _direct_fallback(tool, content, progress_cb=progress_cb)

        if isinstance(res, tuple):
            desc, result = res
        else:
            result = res or {"error": f"{tool}: execution failed", "exit_code": 1}

    else:
        desc = f"unknown: {tool}"
        result = {
            "error": f"Unknown tool: {tool}",
            "exit_code": 1
        }

    logger.info(f"Tool executed: {desc} -> exit_code={result.get('exit_code', 'n/a')}")
    return desc, result


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

# Keys handled by the dedicated branches below — never echo them as raw JSON.
_FORMATTER_HANDLED_KEYS = {
    "stdout", "stderr", "exit_code", "content", "size",
    "response", "results", "session_id", "name", "model", "session_name",
    "success", "path", "action", "title", "doc_id", "version", "applied",
    "error", "output",
}


def format_tool_result(description: str, result: Dict) -> str:
    """Format a tool result into text for feeding back to the LLM."""
    parts = [f"### {description}"]

    if "stdout" in result:
        if result["stdout"]:
            parts.append(f"**stdout:**\n```\n{result['stdout']}\n```")
        if result["stderr"]:
            parts.append(f"**stderr:**\n```\n{result['stderr']}\n```")
        parts.append(f"**exit_code:** {result.get('exit_code', 'unknown')}")
    elif "output" in result:
        # bash / python canonical result shape: {"output": ..., "exit_code": ...}
        parts.append(f"```\n{result['output']}\n```")
        if result.get("exit_code") not in (0, None):
            parts.append(f"**exit_code:** {result['exit_code']}")
    elif "content" in result:
        parts.append(f"**content ({result.get('size', '?')} chars):**\n```\n{result['content']}\n```")
    elif "response" in result:
        model = result.get("model", result.get("session_name", ""))
        if model:
            parts.append(f"**{model} responded:**\n{result['response']}")
        else:
            parts.append(result["response"])
    elif "results" in result:
        parts.append(result["results"])
    elif "session_id" in result and "name" in result:
        parts.append(f"Session created: **{result['name']}** (id: `{result['session_id']}`, model: {result.get('model', 'unknown')})")
    elif "success" in result:
        if result["success"]:
            parts.append(f"File written: {result['path']} ({result['size']} bytes)")
        else:
            parts.append(f"Error: {result.get('error', 'unknown')}")
    elif "action" in result:
        action = result["action"]
        if action == "create":
            parts.append(f"Document created: \"{result.get('title', '')}\" (id: {result['doc_id']}, v{result['version']})")
        elif action == "update":
            parts.append(f"Document updated: \"{result.get('title', '')}\" (v{result['version']})")
        elif action == "edit":
            parts.append(f'Document edited: "{result.get("title", "")}" (v{result.get("version", "?")}, {result.get("applied", 0)} edit(s) applied)')
    elif "error" in result:
        parts.append(f"**Error:** {result['error']}")

    # Surface any additional structured payload (events, tasks, notes, calendars,
    # documents, attachments, etc.) that the dedicated branches above don't show.
    # Without this, tools that return {"response": "...", "events": [...]} would
    # silently drop the events list and the model would only see the summary line.
    extra = {k: v for k, v in result.items() if k not in _FORMATTER_HANDLED_KEYS}
    if extra:
        try:
            extra_json = json.dumps(extra, indent=2, default=str, ensure_ascii=False)
            # Cap to avoid blowing the context window on huge payloads.
            if len(extra_json) > 8000:
                extra_json = extra_json[:8000] + f"\n... (truncated, {len(extra_json)} chars total)"
            parts.append(f"**data:**\n```json\n{extra_json}\n```")
        except (TypeError, ValueError):
            pass

    return "\n".join(parts)
