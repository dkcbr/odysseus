"""
routes/tasks.py

Real task queue/scheduler for standing agents. Fully DB-native as of
Phase 4 cutover: task state lives in /app/data/agent_tasks.db's `tasks`
table (the durable source of truth), with every lifecycle transition
mirrored into the append-only `task_events` table for the Task History
panel. The legacy in-memory TASKS list, its hybrid code paths, and every
DB-read fallback to that list have been removed -- a DB read failure now
surfaces as a real, visible error rather than silently degrading to
stale in-memory state.

Mounted at /api/agent-tasks (NOT /api/tasks, which is Odysseus's own
pre-existing scheduled-task system in routes/task_routes.py).

Every endpoint requires admin auth (require_admin), matching every other
route in this codebase.
"""

import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.middleware import require_admin
from src.agents.capabilities import is_tool_allowed, AGENT_CAPABILITIES
from routes.tasks_history import (
    log_event, get_history_db,
    get_failed_tasks_db, get_agent_history_db,
    get_tasks_by_status_db, get_total_task_count_db,
    DB_PATH, _hydrate_task_row,
)

router = APIRouter(prefix="/api/agent-tasks", tags=["agent-tasks"])

AGENT_HEALTH: dict[str, dict] = {}
STALE_AFTER_SECONDS = 10

DESIRED_AGENTS: dict[str, dict] = {
    "browser_agent": {"enabled": True, "description": "Controls Playwright browser automation"},
    "filesystem_agent": {"enabled": True, "description": "Handles filesystem operations"},
}

# Real, restored 2026-08-09 -- matches the host-side path (data/agent_worker_logs)
# agent_worker.py writes to, via the existing bind mount.
WORKER_LOG_DIR = Path("/app/data/agent_worker_logs")


class TaskCreate(BaseModel):
    agent: str
    server: str
    tool: str
    arguments: dict = {}
    priority: int = 5
    max_retries: int = 3
    schedule_at: float | None = None


class TaskResult(BaseModel):
    result: dict = {}


def create_task_db_native(body: TaskCreate) -> dict:
    now = time.time()
    task = {
        "id": str(uuid4())[:8],
        "agent": body.agent,
        "server": body.server,
        "tool": body.tool,
        "arguments": body.arguments,
        "priority": body.priority,
        "retry_count": 0,
        "max_retries": body.max_retries,
        "schedule_at": body.schedule_at,
        "status": "pending",
        "result": None,
        "created_at": now,
        "updated_at": now,
    }
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """INSERT INTO tasks
               (id, created_at, updated_at, agent, server, tool, arguments,
                priority, retry_count, max_retries, schedule_at, status, result)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task["id"], task["created_at"], task["updated_at"], task["agent"],
             task["server"], task["tool"], json.dumps(task["arguments"]),
             task["priority"], task["retry_count"], task["max_retries"],
             task["schedule_at"], task["status"], None),
        )
        conn.commit()
    finally:
        conn.close()
    log_event(task, "created")
    return task


def get_task_db_native(task_id: str) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    finally:
        conn.close()
    return _hydrate_task_row(row) if row else None


def _atomic_claim(task_id: str, now: float) -> bool:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE tasks SET status='running', updated_at=? WHERE id=? AND status='pending'",
            (now, task_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def _atomic_reject(task_id: str, result: dict, now: float) -> bool:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE tasks SET status='failed', result=?, updated_at=? WHERE id=? AND status='pending'",
            (json.dumps(result), now, task_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def get_pending_tasks_db_native(agent: str | None = None) -> list[dict]:
    """Real candidate discovery + enforcement + atomic claim/reject, fully
    DB-native. Enforcement (agent-enabled, tool-allowed, retry-limit) stays
    in Python -- registry/capabilities/health are in-memory, non-DB-backed
    data, and SQLite has no way to express "is this tool allowed" anyway."""
    now = time.time()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        if agent is not None:
            rows = conn.execute(
                """SELECT * FROM tasks WHERE status='pending' AND agent=?
                   AND (schedule_at IS NULL OR schedule_at <= ?)
                   ORDER BY priority DESC, created_at ASC""",
                (agent, now),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM tasks WHERE status='pending'
                   AND (schedule_at IS NULL OR schedule_at <= ?)
                   ORDER BY priority DESC, created_at ASC""",
                (now,),
            ).fetchall()
    finally:
        conn.close()

    hydrated = [_hydrate_task_row(row) for row in rows]
    claimed_batch: list[dict] = []

    for task in hydrated:
        if not DESIRED_AGENTS.get(task["agent"], {}).get("enabled", False):
            result = {"error": "Agent disabled or not registered", "agent": task["agent"]}
            if _atomic_reject(task["id"], result, now):
                task = {**task, "status": "failed", "updated_at": now, "result": result}
                log_event(task, "rejected_disabled")
                claimed_batch.append(task)
            continue

        if not is_tool_allowed(task["agent"], task["tool"], task["server"]):
            result = {"error": "Tool not allowed", "agent": task["agent"],
                      "server": task["server"], "tool": task["tool"], "allowed": False}
            if _atomic_reject(task["id"], result, now):
                task = {**task, "status": "failed", "updated_at": now, "result": result}
                log_event(task, "rejected_tool")
                claimed_batch.append(task)
            continue

        if task["retry_count"] >= task["max_retries"]:
            result = {"error": "Max retries exceeded"}
            if _atomic_reject(task["id"], result, now):
                task = {**task, "status": "failed", "updated_at": now, "result": result}
                log_event(task, "failed")
                claimed_batch.append(task)
            continue

        if _atomic_claim(task["id"], now):
            task = {**task, "status": "running", "updated_at": now}
            log_event(task, "claimed")
            claimed_batch.append(task)

    return claimed_batch


def complete_task_db_native(task_id: str, worker_result: dict) -> dict:
    task = get_task_db_native(task_id)
    if task is None:
        raise HTTPException(404, f"Task not found: {task_id}")
    if task["status"] != "running":
        raise HTTPException(409, f"Task {task_id} is not running (status={task['status']})")

    now = time.time()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE tasks SET status='success', result=?, updated_at=? WHERE id=? AND status='running'",
            (json.dumps(worker_result), now, task_id),
        )
        conn.commit()
    finally:
        conn.close()

    if cur.rowcount == 0:
        raise HTTPException(409, f"Task {task_id} was modified concurrently -- completion not applied")

    task = {**task, "status": "success", "result": worker_result, "updated_at": now}
    log_event(task, "success")
    return task


def fail_task_db_native(task_id: str, worker_result: dict) -> dict:
    """Retry-then-fail semantics, atomic via optimistic concurrency on the
    OLD retry_count (rather than a SQL CASE expression) so the pending-vs-
    failed decision lives in exactly one place -- Python -- instead of
    being computed twice and risking the two disagreeing."""
    task = get_task_db_native(task_id)
    if task is None:
        raise HTTPException(404, f"Task not found: {task_id}")
    if task["status"] != "running":
        raise HTTPException(409, f"Task {task_id} is not running (status={task['status']})")

    old_retry_count = task["retry_count"]
    new_retry_count = old_retry_count + 1
    now = time.time()

    if new_retry_count < task["max_retries"]:
        new_status = "pending"
        event_type = "requeued"
    else:
        new_status = "failed"
        event_type = "failed"

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """UPDATE tasks SET retry_count=?, status=?, result=?, updated_at=?
               WHERE id=? AND status='running' AND retry_count=?""",
            (new_retry_count, new_status, json.dumps(worker_result), now, task_id, old_retry_count),
        )
        conn.commit()
    finally:
        conn.close()

    if cur.rowcount == 0:
        raise HTTPException(409, f"Task {task_id} was modified concurrently -- retry not applied")

    task = {**task, "retry_count": new_retry_count, "status": new_status,
            "result": worker_result, "updated_at": now}
    log_event(task, event_type)
    return task


@router.post("")
async def create_task(body: TaskCreate, request: Request):
    """Enqueue a new task for an agent to pick up later."""
    require_admin(request)
    return create_task_db_native(body)


@router.get("/pending")
async def get_pending_tasks(request: Request, agent: str | None = None):
    """Claim and return ALL eligible tasks for this agent, priority-sorted
    (highest first, ties broken by oldest first). Returns [] if nothing
    eligible.

    Eligible means: pending, matches agent, allowed by capability rules,
    not scheduled for the future, and has retries remaining. Capability
    violations are rejected here immediately (marked "failed", never
    handed to a worker) rather than claimed and failed later inside
    call_tool().
    """
    require_admin(request)
    return get_pending_tasks_db_native(agent)


def _build_agent_registry() -> dict:
    """Merge the declarative DESIRED_AGENTS registry with real capability
    data pulled live from src/agents/capabilities.py's AGENT_CAPABILITIES."""
    registry = {}
    for agent, info in DESIRED_AGENTS.items():
        cap = AGENT_CAPABILITIES.get(agent, {})
        registry[agent] = {
            "enabled": info.get("enabled", False),
            "description": info.get("description", ""),
            "servers": cap.get("servers", []),
            "allowed_tools": cap.get("allowed_tools", []),
            "forbidden_tools": cap.get("forbidden_tools", []),
        }
    return registry


@router.get("/registry")
async def get_agent_registry(request: Request):
    """Declarative registry of agents that should exist."""
    require_admin(request)
    return _build_agent_registry()


def _build_queue_snapshot() -> dict:
    """Shared snapshot builder for /queue and /dashboard. Fully DB-native:
    task-derived fields come from the tasks table; registry and
    AGENT_HEALTH are separate, non-DB-backed data and stay as they are.
    A DB read failure now surfaces as a real 500, not a silent fallback.
    """
    now = time.time()

    pending = get_tasks_by_status_db("pending")
    running = get_tasks_by_status_db("running")
    failed = get_tasks_by_status_db("failed")
    success = get_tasks_by_status_db("success")
    total = get_total_task_count_db()

    health = {}
    for agent, info in AGENT_HEALTH.items():
        age = now - info["last_seen"]
        health[agent] = {
            "last_seen": info["last_seen"],
            "seconds_since_heartbeat": round(age, 1),
            "status": "stale" if age > STALE_AFTER_SECONDS else "alive",
        }

    return {
        "registry": _build_agent_registry(),
        "agents": health,
        "pending": pending,
        "running": running,
        "failed": failed,
        "success": success,
        "total": total,
    }


@router.get("/queue")
async def get_queue_snapshot(request: Request):
    """Full queue visualization: every non-terminal task plus recent
    terminal ones, priority-sorted, with agent health merged in."""
    require_admin(request)
    return _build_queue_snapshot()


@router.get("/dashboard")
async def queue_dashboard(request: Request):
    """Alias for /queue -- same combined task+health snapshot."""
    require_admin(request)
    return _build_queue_snapshot()


@router.get("/failed")
async def get_failed_tasks(request: Request, agent: str | None = None):
    """Return all FAILED tasks, including ones rejected at claim time.
    Fully DB-native, pure read. A DB failure surfaces as a real 500."""
    require_admin(request)
    return get_failed_tasks_db(agent=agent)


@router.post("/heartbeat/{agent}")
async def agent_heartbeat(agent: str, request: Request):
    """Record that an agent worker is alive right now."""
    require_admin(request)
    now = time.time()
    AGENT_HEALTH[agent] = {"last_seen": now, "status": "alive"}
    return {"agent": agent, "last_seen": now}


@router.get("/health")
async def get_agent_health(request: Request):
    """Health snapshot for every agent that has ever sent a heartbeat."""
    require_admin(request)
    now = time.time()
    result = {}
    for agent, info in AGENT_HEALTH.items():
        age = now - info["last_seen"]
        result[agent] = {
            "last_seen": info["last_seen"],
            "seconds_since_heartbeat": round(age, 1),
            "status": "stale" if age > STALE_AFTER_SECONDS else "alive",
        }
    return result


@router.post("/restart/{agent}")
async def restart_agent(agent: str, request: Request):
    """Mark an agent as needing a restart (recorded only; agent_supervisor.py
    on the host is what actually restarts systemd units)."""
    require_admin(request)
    if agent not in DESIRED_AGENTS:
        raise HTTPException(404, f"Unknown agent: {agent}")
    return {"agent": agent, "restart": "requested"}


@router.post("/registry/{agent}/enable")
async def enable_agent(agent: str, request: Request):
    """Enable an agent -- it can claim tasks and be auto-restarted again."""
    require_admin(request)
    if agent not in DESIRED_AGENTS:
        raise HTTPException(404, f"Unknown agent: {agent}")
    DESIRED_AGENTS[agent]["enabled"] = True
    return {"agent": agent, "enabled": True}


@router.post("/registry/{agent}/disable")
async def disable_agent(agent: str, request: Request):
    """Disable an agent -- its pending/future tasks fail immediately at
    claim time, without deleting its registry entry."""
    require_admin(request)
    if agent not in DESIRED_AGENTS:
        raise HTTPException(404, f"Unknown agent: {agent}")
    DESIRED_AGENTS[agent]["enabled"] = False
    return {"agent": agent, "enabled": False}


@router.get("/history/{agent}")
async def get_agent_history(agent: str, request: Request):
    """Pure, read-only history of every task ever assigned to this agent.
    Fully DB-native. A DB failure surfaces as a real 500."""
    require_admin(request)
    if agent not in DESIRED_AGENTS:
        raise HTTPException(404, f"Unknown agent: {agent}")
    return {"agent": agent, "history": get_agent_history_db(agent)}


@router.get("/history-db")
async def get_history_db_route(request: Request, limit: int = 200):
    """Real, DB-backed history for the Task History UI panel."""
    require_admin(request)
    return get_history_db(limit)


@router.get("/throughput")
async def get_throughput(request: Request, bucket_minutes: int = 15, hours: int = 24):
    """Real throughput metrics -- computed retroactively from the tasks
    table's own real created_at/updated_at fields, NOT a new sampling/
    snapshot subsystem. For a terminal task (success/failed), updated_at
    IS its completion time -- that data already exists and is already
    retained for as long as the task row exists, so no new infrastructure
    is needed to compute a real time-bucketed throughput series from it.

    Extracted from backend/risk-surface-pipeline-20260806 (a real, but
    stale branch not safe to merge wholesale -- 1900+ commits behind
    current dev, would risk reintroducing already-patched issues). Only
    this one, verified-compatible function was taken; schema checked
    directly against the current tasks table (agent/status/updated_at
    columns match) before inserting.
    """
    require_admin(request)
    now = time.time()
    window_start = now - (hours * 3600)
    bucket_seconds = bucket_minutes * 60

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT agent, status, updated_at FROM tasks
               WHERE status IN ('success', 'failed') AND updated_at >= ?
               ORDER BY updated_at ASC""",
            (window_start,),
        ).fetchall()
    finally:
        conn.close()

    buckets: dict[int, dict] = {}
    for row in rows:
        bucket_ts = int((row["updated_at"] - window_start) // bucket_seconds) * bucket_seconds + int(window_start)
        if bucket_ts not in buckets:
            buckets[bucket_ts] = {"bucket_start": bucket_ts, "success": 0, "failed": 0, "by_agent": {}}
        buckets[bucket_ts][row["status"]] += 1
        agent = row["agent"]
        buckets[bucket_ts]["by_agent"][agent] = buckets[bucket_ts]["by_agent"].get(agent, 0) + 1

    series = sorted(buckets.values(), key=lambda b: b["bucket_start"])
    total_completed = sum(b["success"] + b["failed"] for b in series)
    real_hours = (now - window_start) / 3600

    return {
        "bucket_minutes": bucket_minutes,
        "hours": hours,
        "series": series,
        "total_completed": total_completed,
        "avg_per_hour": round(total_completed / real_hours, 2) if real_hours > 0 else 0,
    }


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request):
    """Look up a single task's current status/result."""
    require_admin(request)
    task = get_task_db_native(task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")
    return task


@router.post("/{task_id}/complete")
async def complete_task(task_id: str, body: TaskResult, request: Request):
    """Mark a task as successfully completed, with its result."""
    require_admin(request)
    return complete_task_db_native(task_id, body.result)


@router.post("/{task_id}/fail")
async def fail_task(task_id: str, body: TaskResult, request: Request):
    """Report a task failure. If retries remain, re-queues it instead of
    marking it permanently failed. Only lands on "failed" once retries
    are exhausted."""
    require_admin(request)
    return fail_task_db_native(task_id, body.result)


@router.get("/worker-logs/{agent}")
async def get_worker_logs(agent: str, request: Request, task_id: str | None = None,
                          phase: str | None = None, outcome: str | None = None, limit: int = 200):
    """Real structured worker logs (JSON-lines files written by
    agent_worker.py's real _log_event() calls -- tool_start/tool_end with
    duration_ms, arguments, outcome). Lives under /app/data/agent_worker_logs/
    (the existing bind-mounted data dir, no new docker-compose volume needed).
    Newest first. Optional filters for task_id/phase/outcome.
    """
    require_admin(request)
    if agent not in DESIRED_AGENTS:
        raise HTTPException(404, f"Unknown agent: {agent}")

    log_path = WORKER_LOG_DIR / f"{agent}.jsonl"
    if not log_path.exists():
        return {"agent": agent, "logs": [], "count": 0}

    entries = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # a partially-written line (rare, non-fatal) -- skip rather than fail the whole read
            if task_id is not None and entry.get("task_id") != task_id:
                continue
            if phase is not None and entry.get("phase") != phase:
                continue
            if outcome is not None and entry.get("outcome") != outcome:
                continue
            entries.append(entry)

    entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
    entries = entries[:limit]
    return {"agent": agent, "logs": entries, "count": len(entries)}


