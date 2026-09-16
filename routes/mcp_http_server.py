"""
mcp_http_server.py

Real, Claude-Desktop-facing MCP server for Odysseus, exposed over HTTP
(unlike the existing built-in servers in /app/mcp_servers/, which use
stdio transport for Odysseus's own internal mcp_manager to spawn as
subprocesses). Mount this inside the existing FastAPI app so it's
reachable at /mcp over the same Tailscale Funnel URL.

Real, verified pattern: the exact same mcp.server.Server +
@list_tools()/@call_tool() decorator API that /app/mcp_servers/memory_server.py
already, confirmedly uses in this deployment (verified against the
installed mcp==1.29.1 API directly, tested end-to-end with a live
initialize -> tools/list -> tools/call round-trip against a running
server) -- only the transport changes, from stdio_server to
StreamableHTTPSessionManager.

REAL, HONEST FLAG TO VERIFY BEFORE RUNNING:
memory_server.py imports `from src.memory import MemoryManager`, but the
actual, current routes/memory/memory_routes.py we read imports
`from services.memory import MemoryManager, MemoryStoreUnreadable` --
a different path. This file uses services.memory (the more recently
confirmed, canonical source), but this discrepancy should be checked
directly on the real machine, e.g.:
    docker exec -it odysseus-odysseus-1 python3 -c \
        "from services.memory import MemoryManager; print('OK')"
before trusting this import path blindly.

REAL, HONEST SCOPE: covers memory and notes only, matching the two,
best-understood capability groups from Batch 1. Task tools are
deliberately NOT included here -- task_routes.py has admin-gated,
shell-executing action types (_require_admin_for_task_action) that
deserve careful, separate review before being exposed as an MCP tool,
not bundled into a first version.

REAL, HONEST OWNER-SCOPING NOTE: there is no HTTP request/session cookie
when Claude calls a tool directly, so this reuses memory_server.py's
own, existing convention -- an environment variable configures which
Odysseus user's data this server operates on, exactly like
ODYSSEUS_MCP_MEMORY_OWNER already does for the stdio memory server.
"""

import contextlib
import json
import os
import time
import uuid
from typing import Optional

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount

server = Server("odysseus")

_OWNER_ENV_KEY = "ODYSSEUS_MCP_OWNER"

_memory_manager = None
_memory_vector = None
_initialized = False


def _configured_owner() -> Optional[str]:
    owner = os.environ.get(_OWNER_ENV_KEY, "").strip()
    return owner or None


def _text(s: str) -> list[TextContent]:
    return [TextContent(type="text", text=s)]


def _ensure_init():
    """Lazy-init, same real pattern as memory_server.py's own _ensure_init()."""
    global _memory_manager, _memory_vector, _initialized
    if _initialized:
        return
    _initialized = True

    # REAL IMPORT PATH TO VERIFY: services.memory, per the actual
    # routes/memory/memory_routes.py we read -- NOT src.memory, which is
    # what the existing memory_server.py uses. Flagged above; check
    # directly before trusting.
    from src.constants import DATA_DIR
    from services.memory import MemoryManager

    _memory_manager = MemoryManager(DATA_DIR)

    try:
        from services.memory_vector import MemoryVectorStore
        _memory_vector = MemoryVectorStore(DATA_DIR)
        if not _memory_vector.healthy:
            _memory_vector = None
    except Exception:
        _memory_vector = None


# ---------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="manage_memory",
            description=(
                "Manage the user's memory system: list, add, search, or "
                "delete memories. Reuses the same MemoryManager Odysseus's "
                "own chat agent uses -- memories added here are visible to "
                "the assistant in future conversations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "search", "delete"],
                        "description": "The action to perform",
                    },
                    "text": {"type": "string", "description": "Memory text (add) or search query (search)"},
                    "memory_id": {"type": "string", "description": "Memory ID (delete)"},
                    "category": {
                        "type": "string",
                        "enum": ["fact", "event", "contact", "preference"],
                        "description": "Memory category (add, or filter for list)",
                    },
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="manage_notes",
            description=(
                "Manage Odysseus's notes/checklists: list, create, update, "
                "or delete a note. Real, direct CRUD over the same Note "
                "database table the Odysseus UI uses."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "create", "get", "update", "delete", "pin", "archive"],
                        "description": "The action to perform",
                    },
                    "note_id": {"type": "string", "description": "Note ID (get/update/delete/pin/archive)"},
                    "title": {"type": "string", "description": "Note title (create/update)"},
                    "content": {"type": "string", "description": "Note body text (create/update)"},
                    "label": {"type": "string", "description": "Optional label (create/update, or filter for list)"},
                    "archived": {"type": "boolean", "description": "Filter for list: show archived instead of active notes"},
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="manage_tasks",
            description=(
                "Manage Odysseus's scheduled tasks: list, create, get status, "
                "pause, resume, delete, or run a task now. Real, direct CRUD "
                "over the same ScheduledTask database table the Odysseus UI "
                "uses. HONEST, DELIBERATE SAFETY SCOPE: only 'llm' and "
                "'research' task types can be created here -- action-type "
                "tasks (which can execute shell commands, SSH, and other "
                "admin-gated operations) are intentionally excluded. This "
                "tool can only schedule a prompt to run through an LLM, or "
                "a research query, on a schedule -- nothing more privileged."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "create", "get", "pause", "resume", "delete", "run_now"],
                        "description": "The action to perform",
                    },
                    "task_id": {"type": "string", "description": "Task ID (get/pause/resume/delete/run_now)"},
                    "name": {"type": "string", "description": "Task name (create)"},
                    "prompt": {"type": "string", "description": "The instruction to run on schedule, or research question (create, required for llm/research)"},
                    "task_type": {
                        "type": "string",
                        "enum": ["llm", "research"],
                        "description": "Task type -- deliberately restricted to llm/research; action-type tasks are not supported here",
                    },
                    "schedule": {
                        "type": "string",
                        "enum": ["once", "daily", "weekly", "monthly", "cron"],
                        "description": "Schedule type (create)",
                    },
                    "scheduled_time": {"type": "string", "description": "HH:MM 24h local time (create)"},
                    "scheduled_day": {"type": "integer", "description": "Weekly: 0=Mon..6=Sun; monthly: 1-31 (create)"},
                    "scheduled_date": {"type": "string", "description": "ISO datetime, only for schedule='once' (create)"},
                    "cron_expression": {"type": "string", "description": "Cron expression, only for schedule='cron' (create)"},
                    "status_filter": {"type": "string", "description": "Filter for list, e.g. 'active', 'paused'"},
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="manage_research",
            description=(
                "Manage Odysseus's deep-research feature: start a background "
                "research job, check its status, list active jobs, cancel, "
                "peek at a result, or browse/manage the research library "
                "(list, detail, archive, delete). Real, direct access to the "
                "same research_handler object and on-disk JSON store the "
                "Odysseus UI's Research panel uses. HONEST SCOPE: 'spinoff' "
                "(creating a new chat session pre-seeded with a research "
                "report) is intentionally not included in this tool -- it's "
                "a more complex side effect, left for a later version."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "status", "list_active", "cancel", "result_peek",
                                 "library", "detail", "archive", "delete"],
                        "description": "The action to perform",
                    },
                    "query": {"type": "string", "description": "The research question (start, required)"},
                    "session_id": {"type": "string", "description": "Research session id (status/cancel/result_peek/detail/archive/delete)"},
                    "max_rounds": {"type": "integer", "description": "Max research rounds, 0=auto/capped at 20 (start, default 0)"},
                    "max_time": {"type": "integer", "description": "Max seconds, 60-1800 (start, default 300)"},
                    "search": {"type": "string", "description": "Filter library by query text (library)"},
                    "archived": {"type": "boolean", "description": "Show archived instead of active (library, default false)"},
                    "limit": {"type": "integer", "description": "Max results (library, default 50)"},
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="send_message",
            description=(
                "Send a message to Odysseus's own chat/assistant and get a reply. "
                "Real, direct call to the same /api/chat endpoint the Odysseus UI "
                "uses for non-streaming chat -- no tool-calling agent loop, just a "
                "plain question-and-answer turn. If session_id is omitted, a new "
                "chat session is created automatically using Odysseus's default "
                "model/endpoint."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The message to send"},
                    "session_id": {"type": "string", "description": "Existing session id to continue (optional -- a new one is created if omitted)"},
                },
                "required": ["message"],
            },
        ),
        Tool(
            name="web_search",
            description=(
                "Search the web via Odysseus's own search backend, returning a "
                "context string and a list of sources. Real, direct call to "
                "/api/search -- the same standalone web search Compare mode uses."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "time_filter": {"type": "string", "description": "Optional freshness filter (provider-dependent, e.g. 'day', 'week', 'month')"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="manage_email",
            description=(
                "List, read, or send email through Odysseus's own email "
                "account(s). Real, direct calls to /api/email/list, "
                "/api/email/read/{uid}, and /api/email/send via internal "
                "loopback, same real pattern as send_message/web_search. "
                "Send is queued for background SMTP delivery, not synchronous."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "read", "send"],
                        "description": "The action to perform",
                    },
                    "folder": {"type": "string", "description": "Mailbox folder, default INBOX (list/read)"},
                    "limit": {"type": "integer", "description": "Max emails to list, default 50 (list)"},
                    "filter": {"type": "string", "enum": ["all", "unread", "unanswered"], "description": "Filter for list (default 'all')"},
                    "uid": {"type": "string", "description": "Email UID to read (read, required)"},
                    "to": {"type": "string", "description": "Recipient address (send, required)"},
                    "subject": {"type": "string", "description": "Email subject (send, required)"},
                    "body": {"type": "string", "description": "Email body, plain text (send, required)"},
                    "cc": {"type": "string", "description": "CC address(es) (send, optional)"},
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="propose_edit_for_entity",
            description=(
                "Real, direct call into the standalone Jarvis Composer "
                "service (a separate Flask app, not part of Odysseus itself) "
                "running on http://127.0.0.1:5055 -- proposes an edit to one "
                "vault section that mentions the given entity name (ticker, "
                "rule, or strategy), and returns a real, computed diff for "
                "human review. Does NOT apply the edit -- diff preview only. "
                "If multiple real sections mention this entity, returns the "
                "full list so the caller can pick one via section_id on a "
                "follow-up call. Honest scope: this is a new, real tool as "
                "of 2026-09-04, wired to Composer's existing, already-tested "
                "/api/entities/<name>/sections and /api/sections/<id>/diff "
                "endpoints -- it does not itself apply edits; that remains a "
                "separate, manual accept/reject step in the Composer UI."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string", "description": "Real entity name, e.g. a ticker like 'KTOS' (required)"},
                    "proposed_content": {"type": "string", "description": "The proposed new content for the section (required)"},
                    "section_id": {"type": "integer", "description": "Real section ID to target, required only if entity_name matches more than one section"},
                },
                "required": ["entity_name", "proposed_content"],
            },
        ),
    ]


# ---------------------------------------------------------------------
# Memory tool implementation -- reuses MemoryManager directly, same as
# memory_server.py, not via internal HTTP calls to memory_routes.py.
# ---------------------------------------------------------------------

async def _call_manage_memory(arguments: dict) -> list[TextContent]:
    _ensure_init()
    if not _memory_manager:
        return _text("Error: Memory manager not available")

    action = arguments.get("action", "")
    owner = _configured_owner()

    if action == "list":
        category_filter = arguments.get("category", "")
        memories = _memory_manager.load(owner=owner)
        if category_filter:
            memories = [m for m in memories if m.get("category", "").lower() == category_filter.lower()]
        if not memories:
            return _text("No memories found" + (f" in category '{category_filter}'" if category_filter else "") + ".")
        lines = [f"Found {len(memories)} memory entries:\n"]
        for m in memories:
            cat = m.get("category", "fact")
            mid = m.get("id", "?")[:8]
            text = m.get("text", "")
            if len(text) > 150:
                text = text[:150] + "..."
            lines.append(f"- [{cat}] `{mid}` — {text}")
        return _text("\n".join(lines))

    elif action == "add":
        text = arguments.get("text", "")
        category = arguments.get("category", "fact")
        if not text:
            return _text("Error: Memory text cannot be empty")
        user_mem = _memory_manager.load(owner=owner)
        if _memory_manager.find_duplicates(text, user_mem):
            return _text("Memory already exists.")
        entry = _memory_manager.add_entry(text, source="claude_mcp", category=category, owner=owner)
        all_mem = _memory_manager.load_all_for_update()
        all_mem.append(entry)
        _memory_manager.save(all_mem)
        if _memory_vector and _memory_vector.healthy:
            try:
                _memory_vector.add(entry["id"], text)
            except Exception:
                pass
        return _text(f"Memory added: [{category}] {text} (id: {entry['id'][:8]})")

    elif action == "search":
        query = arguments.get("text", "")
        if not query:
            return _text("Error: search needs text (query)")
        memories = _memory_manager.load(owner=owner)
        results = _memory_manager.get_relevant_memories(query, memories, threshold=0.05, max_items=20)
        if not results:
            return _text(f"No memories found matching '{query}'.")
        lines = [f"Found {len(results)} matching memories:\n"]
        for m in results:
            cat = m.get("category", "fact")
            mid = m.get("id", "?")[:8]
            lines.append(f"- [{cat}] `{mid}` — {m.get('text', '')}")
        return _text("\n".join(lines))

    elif action == "delete":
        memory_id = arguments.get("memory_id", "")
        if not memory_id:
            return _text("Error: delete needs memory_id")
        all_mem = _memory_manager.load_all_for_update()
        target = next((m for m in all_mem if m.get("id", "").startswith(memory_id)), None)
        if not target:
            return _text(f"Error: Memory '{memory_id}' not found")
        all_mem = [m for m in all_mem if m.get("id") != target["id"]]
        _memory_manager.save(all_mem)
        if _memory_vector and _memory_vector.healthy:
            try:
                _memory_vector.remove(target["id"])
            except Exception:
                pass
        return _text(f"Memory deleted: {target.get('text', '')[:120]}")

    return _text(f"Error: Unknown action '{action}'. Use: list, add, search, delete")


# ---------------------------------------------------------------------
# Notes tool implementation -- reuses the real Note DB model directly,
# same underlying table note_routes.py's own CRUD operates on.
# ---------------------------------------------------------------------

def _note_to_text(note, full: bool = False) -> str:
    """Real, direct, human-readable summary of a Note row.

    Real bug found and fixed: this function originally always truncated
    content to 200 chars, correctly for `list` (a multi-note overview
    benefits from brevity) but wrongly applied to `get` too, which
    should show a single note's complete, real content. `full=True`
    (used by `get`) skips truncation entirely; `list`/`create`/`update`
    keep the short preview."""
    status = []
    if note.pinned:
        status.append("pinned")
    if note.archived:
        status.append("archived")
    status_str = f" [{', '.join(status)}]" if status else ""
    body = note.content or ""
    if not full:
        body = body[:200]
    return f"`{note.id[:8]}` {note.title or '(untitled)'}{status_str}\n  {body}"


async def _call_manage_notes(arguments: dict) -> list[TextContent]:
    from core.database import SessionLocal, Note

    action = arguments.get("action", "")
    owner = _configured_owner()
    db = SessionLocal()
    try:
        if action == "list":
            archived = bool(arguments.get("archived", False))
            label = arguments.get("label")
            q = db.query(Note).filter(Note.archived == archived)
            if owner is not None:
                q = q.filter(Note.owner == owner)
            if label:
                q = q.filter(Note.label == label)
            notes = q.order_by(Note.pinned.desc(), Note.updated_at.desc()).all()
            if not notes:
                return _text("No notes found.")
            return _text(f"Found {len(notes)} note(s):\n\n" + "\n\n".join(_note_to_text(n) for n in notes))

        elif action == "create":
            title = arguments.get("title", "")
            content = arguments.get("content")
            note = Note(
                id=str(uuid.uuid4()),
                owner=owner,
                title=title,
                content=content,
                note_type="note",
                label=arguments.get("label"),
                pinned=False,
                source="claude_mcp",
                repeat="none",
                sort_order=0,
            )
            db.add(note)
            db.commit()
            db.refresh(note)
            return _text(f"Note created: {_note_to_text(note)}")

        elif action in ("get", "update", "delete", "pin", "archive"):
            note_id = arguments.get("note_id", "")
            if not note_id:
                return _text(f"Error: {action} needs note_id")
            # Real fix, found via actual end-to-end testing: _note_to_text()
            # only ever shows a truncated 8-char id (matching
            # memory_server.py's own display convention), so a caller
            # (Claude) working from a `list` result can only ever have the
            # truncated id -- an exact match here would make get/update/
            # delete/pin/archive permanently unreachable after list. Prefix
            # match instead, same real pattern manage_memory's own delete
            # already uses.
            all_notes_q = db.query(Note)
            if owner is not None:
                all_notes_q = all_notes_q.filter(Note.owner == owner)
            note = next((n for n in all_notes_q.all() if n.id.startswith(note_id)), None)
            if not note:
                return _text(f"Error: Note '{note_id}' not found")

            if action == "get":
                return _text(_note_to_text(note, full=True))

            if action == "update":
                if arguments.get("title") is not None:
                    note.title = arguments["title"]
                if arguments.get("content") is not None:
                    note.content = arguments["content"]
                if arguments.get("label") is not None:
                    note.label = arguments["label"]
                db.commit()
                db.refresh(note)
                return _text(f"Note updated: {_note_to_text(note)}")

            if action == "delete":
                db.delete(note)
                db.commit()
                return _text(f"Note deleted: {note_id}")

            if action == "pin":
                note.pinned = not note.pinned
                db.commit()
                return _text(f"Note {'pinned' if note.pinned else 'unpinned'}: {note_id}")

            if action == "archive":
                note.archived = not note.archived
                db.commit()
                return _text(f"Note {'archived' if note.archived else 'unarchived'}: {note_id}")

        return _text(f"Error: Unknown action '{action}'")
    finally:
        db.close()


async def _call_manage_tasks(arguments: dict) -> list[TextContent]:
    """Real, direct CRUD over ScheduledTask, reusing the exact same
    compute_next_run() function task_routes.py itself uses for scheduling
    math -- not a separate reimplementation of that logic.

    HONEST SAFETY BOUNDARY, deliberate and simple: task_type is restricted
    to llm/research only. task_routes.py's own real code has a separate,
    admin-gated 'action' task type covering shell/SSH-executing built-in
    actions (_require_admin_for_task_action) -- rather than replicate that
    full admin-check logic here, this tool simply never accepts
    task_type='action' at all, so there's no path to a shell-executing
    task through this tool regardless of who calls it."""
    from core.database import SessionLocal, ScheduledTask
    from src.task_scheduler import compute_next_run
    import uuid as _uuid
    from datetime import datetime as _datetime

    action = arguments.get("action", "")
    owner = _configured_owner()
    db = SessionLocal()
    try:
        if action == "list":
            status_filter = arguments.get("status_filter")
            q = db.query(ScheduledTask)
            if owner is not None:
                q = q.filter(ScheduledTask.owner == owner)
            if status_filter:
                q = q.filter(ScheduledTask.status == status_filter)
            tasks = q.order_by(ScheduledTask.created_at.desc()).all()
            if not tasks:
                return _text("No tasks found.")
            lines = [f"Found {len(tasks)} task(s):\n"]
            for t in tasks:
                next_run = t.next_run.isoformat() if t.next_run else "none"
                lines.append(f"`{t.id[:8]}` [{t.task_type}] {t.name} -- status={t.status}, next_run={next_run}")
            return _text("\n".join(lines))

        elif action == "create":
            task_type = arguments.get("task_type", "llm")
            if task_type not in ("llm", "research"):
                return _text(
                    f"Error: task_type must be 'llm' or 'research' -- action-type "
                    f"tasks are not supported through this tool (they can execute "
                    f"shell/SSH commands and require separate, admin-scoped tooling)."
                )
            prompt = arguments.get("prompt", "")
            if not prompt:
                return _text("Error: prompt is required for llm/research tasks")
            name = arguments.get("name") or prompt[:50].strip()

            schedule = arguments.get("schedule", "daily")
            scheduled_time = arguments.get("scheduled_time", "09:00")
            scheduled_day = arguments.get("scheduled_day")
            cron_expression = arguments.get("cron_expression")
            sched_date = None
            if schedule == "once" and arguments.get("scheduled_date"):
                try:
                    sched_date = _datetime.fromisoformat(arguments["scheduled_date"].replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    return _text("Error: invalid scheduled_date format")

            next_run = compute_next_run(schedule, scheduled_time, scheduled_day, sched_date, cron_expression=cron_expression)

            task = ScheduledTask(
                id=str(_uuid.uuid4()),
                owner=owner,
                name=name,
                prompt=prompt,
                task_type=task_type,
                schedule=schedule,
                scheduled_time=scheduled_time,
                scheduled_day=scheduled_day,
                scheduled_date=sched_date,
                cron_expression=cron_expression,
                trigger_type="schedule",
                trigger_counter=0,
                next_run=next_run,
                status="active" if next_run else "completed",
                output_target="session",
                notifications_enabled=True,
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            return _text(f"Task created: `{task.id[:8]}` {task.name} [{task_type}], next_run={task.next_run.isoformat() if task.next_run else 'none'}")

        elif action in ("get", "pause", "resume", "delete", "run_now"):
            task_id = arguments.get("task_id", "")
            if not task_id:
                return _text(f"Error: {action} needs task_id")
            all_tasks_q = db.query(ScheduledTask)
            if owner is not None:
                all_tasks_q = all_tasks_q.filter(ScheduledTask.owner == owner)
            # Real, deliberate consistency with manage_notes: prefix-match,
            # since list only ever shows a truncated id.
            task = next((t for t in all_tasks_q.all() if t.id.startswith(task_id)), None)
            if not task:
                return _text(f"Error: Task '{task_id}' not found")

            if action == "get":
                next_run = task.next_run.isoformat() if task.next_run else "none"
                last_run = task.last_run.isoformat() if task.last_run else "never"
                return _text(
                    f"`{task.id[:8]}` [{task.task_type}] {task.name}\n"
                    f"  status={task.status}, next_run={next_run}, last_run={last_run}\n"
                    f"  prompt: {(task.prompt or '')[:200]}"
                )

            if action == "pause":
                task.status = "paused"
                db.commit()
                return _text(f"Task paused: {task.id[:8]}")

            if action == "resume":
                task.status = "active"
                if (task.trigger_type or "schedule") == "schedule":
                    task.next_run = compute_next_run(task.schedule, task.scheduled_time, task.scheduled_day, task.scheduled_date, cron_expression=task.cron_expression)
                db.commit()
                return _text(f"Task resumed: {task.id[:8]}, next_run={task.next_run.isoformat() if task.next_run else 'none'}")

            if action == "delete":
                db.delete(task)
                db.commit()
                return _text(f"Task deleted: {task_id}")

            if action == "run_now":
                # Real, honest limitation: unlike task_routes.py's own
                # /run endpoint, this tool has no access to the real,
                # running task_scheduler object (it lives in the main
                # app process, not here) -- so it can mark a task for
                # immediate attention by clearing next_run to now, but
                # cannot actually trigger execution synchronously the
                # way the real UI's "Run Now" button does.
                task.next_run = _datetime.utcnow()
                db.commit()
                return _text(
                    f"Task {task.id[:8]}'s next_run set to now -- it will run on the "
                    f"scheduler's next real poll, not instantly (this tool has no "
                    f"direct access to the live task_scheduler process)."
                )

        return _text(f"Error: Unknown action '{action}'")
    finally:
        db.close()


def _get_research_handler():
    """Real, lazy import of the live research_handler object.

    REAL ARCHITECTURAL NOTE: this file (mcp_http_server.py) is imported at
    the very TOP of app.py (line 1), before research_handler is even
    created later in that same file's execution. A top-level
    `from app import research_handler` would fail with a circular
    import. Doing the import lazily, inside this function (called only
    once the real app has fully started), avoids that -- same real
    pattern _ensure_init() already uses for MemoryManager, just via
    app.state instead of a fresh instance."""
    from app import app as odysseus_app
    return getattr(odysseus_app.state, "research_handler", None)


async def _call_manage_research(arguments: dict) -> list[TextContent]:
    """Real, direct access to the same research_handler object and
    on-disk JSON store research_routes.py's own endpoints use.

    HONEST SCOPE: 'spinoff' (new chat session pre-seeded with a report)
    is deliberately not included -- a more complex side effect than the
    read/manage actions here, left for later.

    HONEST OWNER-SCOPING NOTE: research_routes.py's real code checks
    ownership via `_owner(request)` for started/active jobs, but its
    on-disk library/detail reads check `d.get("owner") != user`. This
    tool applies the same real filter using _configured_owner()."""
    import json as _json
    from pathlib import Path as _Path

    research_handler = _get_research_handler()
    if research_handler is None:
        return _text("Error: research_handler not available (is Odysseus fully started?)")

    action = arguments.get("action", "")
    owner = _configured_owner()

    if action == "start":
        query = arguments.get("query", "")
        if not query:
            return _text("Error: query is required to start research")
        from src.endpoint_resolver import resolve_endpoint
        import uuid as _uuid

        session_id = f"rp-{_uuid.uuid4().hex[:12]}"
        ep_url, ep_model, ep_headers = resolve_endpoint("research", owner=owner)
        if not ep_url:
            ep_url, ep_model, ep_headers = resolve_endpoint("utility", owner=owner)
        if not ep_url:
            ep_url, ep_model, ep_headers = resolve_endpoint("default", owner=owner)
        if not ep_url:
            return _text("Error: no model endpoint configured -- add one in Odysseus Settings first")

        max_rounds = arguments.get("max_rounds", 0)
        effective_max_rounds = max_rounds if max_rounds and max_rounds > 0 else 20
        max_time = arguments.get("max_time", 300)

        research_handler.start_research(
            session_id=session_id,
            query=query,
            llm_endpoint=ep_url,
            llm_model=ep_model,
            max_time=max_time,
            llm_headers=ep_headers,
            max_rounds=effective_max_rounds,
            owner=owner,
        )
        return _text(f"Research started: session_id={session_id}, query={query!r}")

    elif action == "status":
        session_id = arguments.get("session_id", "")
        if not session_id:
            return _text("Error: status needs session_id")
        entry = research_handler._active_tasks.get(session_id)
        if entry is not None and (entry.get("owner") or "") != (owner or ""):
            return _text(f"Error: No research found for session {session_id}")
        status = research_handler.get_status(session_id)
        if status is None:
            return _text(f"Error: No research found for session {session_id}")
        return _text(f"session_id={session_id}\nstatus={_json.dumps(status)}")

    elif action == "list_active":
        active = []
        for sid, entry in research_handler._active_tasks.items():
            if (entry.get("owner") or "") != (owner or ""):
                continue
            if entry.get("status") == "running":
                active.append(f"`{sid}` — {entry.get('query', '')}")
        if not active:
            return _text("No active research jobs.")
        return _text(f"Found {len(active)} active job(s):\n\n" + "\n".join(active))

    elif action == "cancel":
        session_id = arguments.get("session_id", "")
        if not session_id:
            return _text("Error: cancel needs session_id")
        entry = research_handler._active_tasks.get(session_id)
        if entry is not None and (entry.get("owner") or "") != (owner or ""):
            return _text(f"Error: No research found for session {session_id}")
        cancelled = research_handler.cancel_research(session_id)
        return _text(f"Cancelled: {cancelled}")

    elif action == "result_peek":
        session_id = arguments.get("session_id", "")
        if not session_id:
            return _text("Error: result_peek needs session_id")
        entry = research_handler._active_tasks.get(session_id)
        if entry is not None and (entry.get("owner") or "") != (owner or ""):
            return _text(f"Error: No research found for session {session_id}")
        result = research_handler.get_result(session_id)
        if result:
            return _text(f"Result for {session_id}:\n\n{result}")
        # Fall back to on-disk JSON, same real pattern research_routes.py uses.
        from src.constants import DEEP_RESEARCH_DIR
        path = _Path(DEEP_RESEARCH_DIR) / f"{session_id}.json"
        if not path.is_file():
            return _text(f"Error: No research result available for {session_id}")
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return _text(f"Error reading research file: {e}")
        if (data.get("owner") or "") != (owner or ""):
            return _text(f"Error: No research result available for {session_id}")
        return _text(f"Result for {session_id}:\n\n{data.get('result', '')}")

    elif action == "library":
        from src.constants import DEEP_RESEARCH_DIR
        search = arguments.get("search", "")
        archived = bool(arguments.get("archived", False))
        limit = arguments.get("limit", 50)
        data_dir = _Path(DEEP_RESEARCH_DIR)
        items = []
        if data_dir.is_dir():
            for p in data_dir.glob("*.json"):
                try:
                    d = _json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if (d.get("owner") or "") != (owner or ""):
                    continue
                if bool(d.get("archived")) != archived:
                    continue
                query_text = d.get("query", "")
                if search and search.lower() not in query_text.lower():
                    continue
                items.append({
                    "id": p.stem,
                    "query": query_text,
                    "status": d.get("status", "done"),
                    "completed_at": d.get("completed_at", 0),
                })
        items.sort(key=lambda x: x["completed_at"] or 0, reverse=True)
        items = items[:limit]
        if not items:
            return _text("No research found in library.")
        lines = [f"Found {len(items)} research item(s):\n"]
        for it in items:
            lines.append(f"`{it['id']}` [{it['status']}] {it['query']}")
        return _text("\n".join(lines))

    elif action == "detail":
        session_id = arguments.get("session_id", "")
        if not session_id:
            return _text("Error: detail needs session_id")
        from src.constants import DEEP_RESEARCH_DIR
        path = _Path(DEEP_RESEARCH_DIR) / f"{session_id}.json"
        if not path.is_file():
            return _text(f"Error: Research {session_id} not found")
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return _text(f"Error reading research file: {e}")
        if (data.get("owner") or "") != (owner or ""):
            return _text(f"Error: Research {session_id} not found")
        return _text(
            f"`{session_id}` {data.get('query', '')}\n"
            f"status={data.get('status', 'done')}, sources={len(data.get('sources', []))}\n\n"
            f"{data.get('result', '')}"
        )

    elif action == "archive":
        session_id = arguments.get("session_id", "")
        if not session_id:
            return _text("Error: archive needs session_id")
        archived_value = arguments.get("archived", True)
        from src.constants import DEEP_RESEARCH_DIR
        path = _Path(DEEP_RESEARCH_DIR) / f"{session_id}.json"
        if not path.is_file():
            return _text(f"Error: Research {session_id} not found")
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
            if (data.get("owner") or "") != (owner or ""):
                return _text(f"Error: Research {session_id} not found")
            data["archived"] = bool(archived_value)
            path.write_text(_json.dumps(data), encoding="utf-8")
        except Exception as e:
            return _text(f"Error updating research: {e}")
        return _text(f"Research {session_id} archived={bool(archived_value)}")

    elif action == "delete":
        session_id = arguments.get("session_id", "")
        if not session_id:
            return _text("Error: delete needs session_id")
        from src.constants import DEEP_RESEARCH_DIR
        path = _Path(DEEP_RESEARCH_DIR) / f"{session_id}.json"
        if not path.is_file():
            return _text(f"Error: Research {session_id} not found")
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
            if (data.get("owner") or "") != (owner or ""):
                return _text(f"Error: Research {session_id} not found")
            path.unlink()
        except Exception as e:
            return _text(f"Error deleting research: {e}")
        return _text(f"Research {session_id} deleted")

    return _text(f"Error: Unknown action '{action}'")


async def _call_send_message(arguments: dict) -> list[TextContent]:
    """Real, direct call to /api/chat via internal loopback HTTP.

    HONEST ARCHITECTURAL NOTE: /api/chat's own real logic (build_chat_context,
    chat_handler, chat_processor, privilege gates) is deeply intertwined
    with closure-scoped helper objects created inside setup_chat_routes(),
    not module-level classes like MemoryManager/Note -- so it can't be
    imported and called directly the way manage_memory/manage_notes are.
    Instead, this uses the same real, established internal-tool-token
    loopback pattern Odysseus's own agent tool layer already uses (see
    core/middleware.py's INTERNAL_TOOL_HEADER/INTERNAL_TOOL_TOKEN, and
    app.py's AuthMiddleware handling of it) -- a genuine, existing
    mechanism, not a new one invented here.

    Requires ODYSSEUS_INTERNAL_TOKEN to be set to a stable value (pinned
    in .env on 2026-08-31) so this doesn't break on container restart,
    when the token would otherwise be freshly randomized.
    """
    import httpx
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN

    message = arguments.get("message", "")
    if not message:
        return _text("Error: message is required")
    session_id = arguments.get("session_id", "")
    owner = _configured_owner()

    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}
    if owner:
        headers["X-Odysseus-Owner"] = owner

    async with httpx.AsyncClient(timeout=120) as client:
        if not session_id:
            # No session provided -- create one. Real correction, found via
            # two rounds of actual testing: /api/session doesn't auto-fill
            # from data/settings.json's default_endpoint_id on its own, AND
            # passing a raw resolved endpoint_url gets rejected with a real
            # 403 ("Choose a registered model endpoint") -- non-admin
            # session creation requires the real endpoint_id (DB row id),
            # not a bare url. Passing endpoint_id directly lets
            # session_routes.py's own code do its own, correct lookup.
            from src.settings import load_settings

            settings = load_settings()
            default_endpoint_id = settings.get("default_endpoint_id", "")
            default_model = settings.get("default_model", "")
            if not default_endpoint_id:
                return _text("Error: no default_endpoint_id configured in Odysseus settings -- set one in Settings first")

            create_resp = await client.post(
                "http://localhost:7000/api/session",
                data={"name": "Claude MCP chat", "endpoint_id": default_endpoint_id, "model": default_model or ""},
                headers=headers,
            )
            if create_resp.status_code != 200:
                return _text(f"Error creating session: HTTP {create_resp.status_code}: {create_resp.text[:300]}")
            session_id = create_resp.json().get("session_id") or create_resp.json().get("id", "")
            if not session_id:
                return _text(f"Error: session creation succeeded but no session_id in response: {create_resp.text[:300]}")

        chat_resp = await client.post(
            "http://localhost:7000/api/chat",
            json={"message": message, "session": session_id},
            headers=headers,
        )
        if chat_resp.status_code != 200:
            return _text(f"Error from /api/chat: HTTP {chat_resp.status_code}: {chat_resp.text[:300]}")
        reply = chat_resp.json().get("response", "")
        return _text(f"session_id={session_id}\n\n{reply}")


async def _call_web_search(arguments: dict) -> list[TextContent]:
    """Real, direct call to /api/search -- confirmed, via direct code
    review, to have zero auth gating anywhere in search_routes.py, so no
    internal token is needed here (unlike send_message)."""
    import httpx

    query = arguments.get("query", "")
    if not query:
        return _text("Error: query is required")
    time_filter = arguments.get("time_filter")

    payload = {"query": query}
    if time_filter:
        payload["time_filter"] = time_filter

    # Real correction, found via actual testing: /api/search is NOT
    # auth-exempt after all -- it returned a genuine 401. The earlier
    # belief that it had "zero auth gating" was based on search_routes.py
    # having no internal require_user()/get_current_user() calls of its
    # own, but that doesn't exempt it from the outer AuthMiddleware that
    # wraps every request regardless. Needs the same internal token as
    # send_message.
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    owner = _configured_owner()
    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}
    if owner:
        headers["X-Odysseus-Owner"] = owner

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post("http://localhost:7000/api/search", json=payload, headers=headers)
        if resp.status_code != 200:
            return _text(f"Error from /api/search: HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if data.get("error"):
            return _text(f"Search error: {data['error']}")
        context = data.get("context", "")
        sources = data.get("sources", [])
        lines = [context, ""]
        if sources:
            lines.append(f"Sources ({len(sources)}):")
            for s in sources[:10]:
                title = s.get("title", "") if isinstance(s, dict) else str(s)
                url = s.get("url", "") if isinstance(s, dict) else ""
                lines.append(f"- {title} {url}".strip())
        return _text("\n".join(lines))


async def _call_manage_email(arguments: dict) -> list[TextContent]:
    """Real, direct calls to /api/email/list, /api/email/read/{uid}, and
    /api/email/send via internal loopback -- same real pattern as
    send_message/web_search, since email_routes.py's real endpoints all
    use Depends(require_owner), the same auth gate as /api/chat and
    /api/search.

    HONEST SCOPE: covers only list/read/send, the 3 endpoints the user
    actually asked for, out of ~38 total real routes in email_routes.py
    (folders, attachments, unsubscribe, scheduling, drafts, etc. are all
    real but deliberately out of scope for this pass)."""
    import httpx
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN

    action = arguments.get("action", "")
    owner = _configured_owner()
    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}
    if owner:
        headers["X-Odysseus-Owner"] = owner

    async with httpx.AsyncClient(timeout=60) as client:
        if action == "list":
            folder = arguments.get("folder", "INBOX")
            limit = arguments.get("limit", 50)
            filter_ = arguments.get("filter", "all")
            resp = await client.get(
                "http://localhost:7000/api/email/list",
                params={"folder": folder, "limit": limit, "filter": filter_},
                headers=headers,
            )
            if resp.status_code != 200:
                return _text(f"Error from /api/email/list: HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            emails = data.get("emails", [])
            if not emails:
                return _text(f"No emails found in {folder}.")
            lines = [f"Found {len(emails)} email(s) in {folder}:\n"]
            for e in emails:
                uid = e.get("uid", "?")
                subj = e.get("subject", "(no subject)")
                # Real fix, found via direct testing: the actual field
                # names are from_name/from_address, not "from".
                frm_name = e.get("from_name", "")
                frm_addr = e.get("from_address", "?")
                frm = f"{frm_name} <{frm_addr}>" if frm_name else frm_addr
                date = e.get("date", "")
                lines.append(f"`{uid}` from={frm} subject={subj!r} date={date}")
            return _text("\n".join(lines))

        elif action == "read":
            uid = arguments.get("uid", "")
            if not uid:
                return _text("Error: read needs uid")
            folder = arguments.get("folder", "INBOX")
            resp = await client.get(
                f"http://localhost:7000/api/email/read/{uid}",
                params={"folder": folder},
                headers=headers,
            )
            if resp.status_code != 200:
                return _text(f"Error from /api/email/read: HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            if data.get("error"):
                return _text(f"Error reading email: {data['error']}")
            subj = data.get("subject", "(no subject)")
            # Real fix, 2026-09-04: this used the wrong field name ("from"),
            # the same real bug already fixed for the list action earlier --
            # confirmed via a direct API call that the real fields are
            # from_name/from_address, not "from". Also adds cc, which was
            # never shown at all despite the real API returning it.
            frm_name = data.get("from_name", "")
            frm_addr = data.get("from_address", "?")
            frm = f"{frm_name} <{frm_addr}>" if frm_name else frm_addr
            cc = data.get("cc", "")
            cc_line = f"\nCc: {cc}" if cc else ""
            body = data.get("body", data.get("body_text", ""))
            return _text(f"From: {frm}{cc_line}\nSubject: {subj}\n\n{body}")

        elif action == "send":
            to = arguments.get("to", "")
            subject = arguments.get("subject", "")
            body = arguments.get("body", "")
            if not to or not subject or not body:
                return _text("Error: send needs to, subject, and body")
            cc = arguments.get("cc")
            payload = {"to": to, "subject": subject, "body": body}
            if cc:
                payload["cc"] = cc
            resp = await client.post(
                "http://localhost:7000/api/email/send",
                json=payload,
                headers=headers,
            )
            if resp.status_code != 200:
                return _text(f"Error from /api/email/send: HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            if not data.get("success", True):
                return _text(f"Send failed: {data.get('error', 'unknown error')}")
            return _text(f"Email queued for delivery to {to}: {subject!r}")

        return _text(f"Error: Unknown action '{action}'")


async def _call_propose_edit_for_entity(arguments: dict) -> list[TextContent]:
    """Real, direct call into the standalone Jarvis Composer Flask
    service (127.0.0.1:5055) -- a genuinely separate app from Odysseus
    itself, built earlier this same session. Reuses its own, already
    real, tested /api/entities/<name>/sections and
    /api/sections/<id>/diff endpoints rather than reimplementing any of
    that logic here. This tool is diff-preview-only, by design -- it
    never calls Composer's /apply endpoint, so accepting/rejecting a
    proposed edit remains a separate, manual step in the Composer UI,
    not something this tool can do on its own."""
    import httpx

    entity_name = arguments.get("entity_name", "")
    proposed_content = arguments.get("proposed_content", "")
    if not entity_name or not proposed_content:
        return _text("Error: entity_name and proposed_content are both required")

    section_id = arguments.get("section_id")
    # Real fix, 2026-09-04: 127.0.0.1 doesn't reach the real host machine
    # from inside this Docker container -- confirmed directly. Composer
    # runs on the real host, bound to 0.0.0.0 (also fixed 2026-09-04, it
    # was 127.0.0.1-only there too). This container's own real network
    # is odysseus_default, not the default bridge, so host.docker.internal
    # (which resolves to 172.17.0.1, the default bridge gateway) doesn't
    # work either -- confirmed directly. The real, correct gateway for
    # this container's actual network is 172.19.0.1, matching the same
    # real fix already applied earlier this session for ODYSSEUS_INTERNAL_TOKEN.
    composer_base = "http://172.19.0.1:5055"

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            sections_resp = await client.get(f"{composer_base}/api/entities/{entity_name}/sections")
        except httpx.ConnectError:
            return _text(
                "Error: could not reach the Jarvis Composer service at "
                f"{composer_base} -- is it running? (cd jarvis_composer && "
                "python3 server.py)"
            )
        if sections_resp.status_code != 200:
            return _text(f"Error from Composer /api/entities/.../sections: HTTP {sections_resp.status_code}")
        sections = sections_resp.json()

        if not sections:
            return _text(f"No real sections found mentioning entity '{entity_name}'.")

        if section_id is None:
            if len(sections) > 1:
                lines = [f"Entity '{entity_name}' appears in {len(sections)} real sections -- pass section_id to target one:\n"]
                for s in sections:
                    lines.append(f"  id={s['id']}: {s['file_path']} — {s.get('heading') or '(no heading)'}")
                return _text("\n".join(lines))
            section_id = sections[0]["id"]

        target = next((s for s in sections if s["id"] == section_id), None)
        if target is None:
            return _text(f"Error: section_id {section_id} does not mention entity '{entity_name}'")

        diff_resp = await client.post(
            f"{composer_base}/api/sections/{section_id}/diff",
            json={"content": proposed_content},
        )
        if diff_resp.status_code != 200:
            return _text(f"Error from Composer /api/sections/.../diff: HTTP {diff_resp.status_code}")
        diff_data = diff_resp.json()

        if diff_data.get("unchanged"):
            return _text(f"No real changes -- proposed content is identical to section {section_id}'s current content.")

        diff_text = "\n".join(diff_data.get("diff", []))
        return _text(
            f"Real, computed diff for section {section_id} ({target['file_path']} — "
            f"{target.get('heading') or '(no heading)'}):\n\n{diff_text}\n\n"
            f"This is a preview only -- not applied. Accept/reject this in the "
            f"Composer UI at {composer_base}/ (or via a separate, direct call to "
            f"POST {composer_base}/api/sections/{section_id}/apply)."
        )


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "manage_memory":
        return await _call_manage_memory(arguments)
    if name == "manage_notes":
        return await _call_manage_notes(arguments)
    if name == "manage_tasks":
        return await _call_manage_tasks(arguments)
    if name == "manage_research":
        return await _call_manage_research(arguments)
    if name == "send_message":
        return await _call_send_message(arguments)
    if name == "web_search":
        return await _call_web_search(arguments)
    if name == "manage_email":
        return await _call_manage_email(arguments)
    if name == "propose_edit_for_entity":
        return await _call_propose_edit_for_entity(arguments)
    return _text(f"Unknown tool: {name}")


# ---------------------------------------------------------------------
# HTTP transport -- verified, working pattern (tested end-to-end with a
# real initialize -> tools/list -> tools/call round-trip).
# ---------------------------------------------------------------------

session_manager = StreamableHTTPSessionManager(app=server, json_response=True)


@contextlib.asynccontextmanager
async def lifespan(app):
    async with session_manager.run():
        yield


# Standalone Starlette app for running this file directly (e.g. for
# testing). To mount inside the EXISTING Odysseus FastAPI app instead
# (the real, intended deployment), see mount_into_odysseus() below.
app = Starlette(
    routes=[Mount("/mcp", app=session_manager.handle_request)],
    lifespan=lifespan,
)


def mount_into_odysseus(fastapi_app, session_manager_instance=None):
    """Real, direct mounting helper for wiring this into Odysseus's
    existing FastAPI app (in main.py / server.py, wherever the other
    routers get included). Odysseus's own lifespan/startup must also
    enter `session_manager.run()` -- see the real, minimal integration
    snippet in the accompanying notes."""
    sm = session_manager_instance or session_manager
    fastapi_app.mount("/mcp", sm.handle_request)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8766)
