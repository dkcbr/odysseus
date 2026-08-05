"""
routes/tasks_history.py

Phase 1 of adding real persistence to Odysseus's task queue: a pure,
append-only event log mirroring every real lifecycle transition in
routes/tasks.py. Deliberately does NOT change queue behavior at all --
TASKS (the in-memory list) remains the actual source of truth for
pending/claim/complete/fail logic. This is purely a parallel write for
history/inspection, matching the task shape used throughout tonight
(agent/server/tool/arguments, not a single "command" string).

DB path: /app/data/agent_tasks.db -- reuses the EXISTING bind-mounted
volume already configured in docker-compose.yml (./data:/app/data:z), no
new mount needed.
"""

import json
import sqlite3
import time

DB_PATH = "/app/data/agent_tasks.db"


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                agent TEXT NOT NULL,
                server TEXT NOT NULL,
                tool TEXT NOT NULL,
                arguments TEXT NOT NULL,
                priority INTEGER,
                retry_count INTEGER,
                status TEXT NOT NULL,
                result TEXT
            )
        """)
        # Live per-task state table -- the durable source of truth as of
        # the Phase 4 cutover. No retry/status business logic is
        # implemented in SQL; that stays in Python (routes/tasks.py),
        # exactly as tested throughout the migration.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                agent TEXT NOT NULL,
                server TEXT NOT NULL,
                tool TEXT NOT NULL,
                arguments TEXT NOT NULL,
                priority INTEGER NOT NULL,
                retry_count INTEGER NOT NULL,
                max_retries INTEGER NOT NULL,
                schedule_at REAL,
                status TEXT NOT NULL,
                result TEXT
            )
        """)

        # Real migration: add optional "name" column to both real,
        # already-populated tables, plus "remember_on_success" to tasks
        # only (it's a task-creation-time flag, not something individual
        # events need). CREATE TABLE IF NOT EXISTS above does NOT alter an
        # existing table's columns, so this must be a separate, idempotent
        # ALTER TABLE -- checked against the real sqlite_master/PRAGMA
        # table_info first, since re-running ALTER TABLE ADD COLUMN on a
        # column that already exists raises "duplicate column name".
        for table in ("tasks", "task_events"):
            existing_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "name" not in existing_cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN name TEXT")

        tasks_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "remember_on_success" not in tasks_cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN remember_on_success INTEGER DEFAULT 0")

        conn.commit()
    finally:
        conn.close()


def get_history_db(limit: int = 200) -> dict:
    """Real read for the Task History UI panel -- pulls from both tables,
    matching the exact schema actually running (updated_at + result, no
    fabricated claimed_at/completed_at/error columns)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        tasks_rows = conn.execute(
            "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        events_rows = conn.execute(
            "SELECT * FROM task_events ORDER BY ts DESC LIMIT ?", (limit * 5,)
        ).fetchall()
    finally:
        conn.close()

    def _hydrate(row):
        d = dict(row)
        d["arguments"] = json.loads(d["arguments"]) if d.get("arguments") else {}
        d["result"] = json.loads(d["result"]) if d.get("result") else None
        return d

    return {
        "tasks": [_hydrate(r) for r in tasks_rows],
        "events": [_hydrate(r) for r in events_rows],
    }


def get_failed_tasks_db(agent: str | None = None) -> list[dict]:
    """Pure read migration -- no identity/mutation concerns here (unlike
    /pending), so this hydrates fresh dicts directly from DB rows. Nothing
    holds onto or continues mutating these after the response is sent, so
    there's no risk of the two-diverging-copies problem /pending had to
    guard against."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        if agent is not None:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = 'failed' AND agent = ? ORDER BY updated_at DESC",
                (agent,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = 'failed' ORDER BY updated_at DESC"
            ).fetchall()
        return [_hydrate_task_row(r) for r in rows]
    finally:
        conn.close()


def get_agent_history_db(agent: str) -> list[dict]:
    """Pure read migration for /history/{agent} -- same reasoning as
    get_failed_tasks_db above: no mutation, no identity concerns, safe to
    hydrate fresh dicts directly."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM tasks WHERE agent = ? ORDER BY created_at DESC", (agent,)
        ).fetchall()
        return [_hydrate_task_row(r) for r in rows]
    finally:
        conn.close()


def get_tasks_by_status_db(status: str) -> list[dict]:
    """Pure read for /queue and /dashboard -- returns FULL task objects
    (same shape as the real, current TASKS-based response), not counts.
    Real /queue response merges this with registry/AGENT_HEALTH, which
    are separate, non-DB-backed data and must stay exactly as they are."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC, created_at ASC",
            (status,),
        ).fetchall()
        return [_hydrate_task_row(r) for r in rows]
    finally:
        conn.close()


def get_total_task_count_db() -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    finally:
        conn.close()


def _hydrate_task_row(row) -> dict:
    d = dict(row)
    d["arguments"] = json.loads(d["arguments"]) if d.get("arguments") else {}
    d["result"] = json.loads(d["result"]) if d.get("result") else None
    if "remember_on_success" in d:
        d["remember_on_success"] = bool(d["remember_on_success"])
    return d


def log_event(task: dict, event_type: str) -> None:
    """Real, append-only write -- never raises into the caller. A history
    write failing must never break the actual queue operation it's
    mirroring; log and move on."""
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                """INSERT INTO task_events
                   (ts, task_id, event_type, agent, server, tool, arguments,
                    priority, retry_count, status, result, name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    time.time(),
                    task["id"],
                    event_type,
                    task["agent"],
                    task["server"],
                    task["tool"],
                    json.dumps(task.get("arguments", {})),
                    task.get("priority"),
                    task.get("retry_count"),
                    task["status"],
                    json.dumps(task.get("result")) if task.get("result") is not None else None,
                    task.get("name"),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        import logging
        logging.getLogger(__name__).warning("task_events write failed (non-fatal)", exc_info=True)
