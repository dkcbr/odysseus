#!/usr/bin/env python3
"""
J.A.R.V.I.S -- gdrive_backup_manager MCP Server
=================================================
Real, added 2026-08-27: makes the gdrive half of the memory backup a
first-class, on-demand, inspectable MCP tool, rather than something
that only happens via a mysterious, not-yet-located nightly trigger
(oracle_nightly_backup.py -- confirmed real, correct, and working when
it runs, but its actual nightly trigger mechanism could not be found
anywhere checked: user/root crontab, systemd timers, /etc/cron.d,
anacron, at jobs, or Odysseus's own scheduled_tasks table, across all
three real machines in this ecosystem -- deliberately parked, not
solved, per DK's own explicit direction).

This server does NOT replace or depend on that script or its trigger.
It reimplements the same, already-proven-correct gdrive packaging
logic (SQLite online-backup for app.db, tarball, sha256 verification,
retention pruning) as a real, independent, directly-callable path --
so backups can be triggered, inspected, and verified deliberately,
without needing to first solve the trigger mystery.

Real, honest scope: gdrive only, per DK's own explicit direction
("setup the manager for gdrive" -- Oracle Object Storage is a
separate, deferred piece). If something about Oracle gets learned
along the way, that's a bonus, not a goal of this server.

Runs INSIDE the Odysseus container (same pattern as jarvis_shell,
desktop_sandbox, etc.), so all paths below are the real,
container-internal paths, not the host-side ones oracle_nightly_
backup.py uses -- confirmed directly via `ollama ps`-equivalent
container checks earlier tonight, not assumed.

Real, load-bearing mount, added specifically for this server
(docker-compose.yml, 2026-08-27): a NEW, separate, narrowly-scoped
read-write mount at /app/gdrive_backups_data, pointed at just the
real backup destination subdirectory on the host -- deliberately NOT
widening the existing, shared /app/vault_data mount (which is
read-only on purpose, so search_vault can never write into DK's
hand-maintained vault; widening that one would have silently removed
that protection for every consumer of it, not just this new tool).

Exposes: run_gdrive_backup(), list_backups(), get_backup_status(),
         verify_backup_integrity(filename)

Registration:
    fetch('/api/mcp/servers', {
      method: 'POST',
      credentials: 'same-origin',
      body: new URLSearchParams({
        name: 'gdrive_backup_manager',
        transport: 'stdio',
        command: 'python3',
        args: '["/app/services/mcp_servers/gdrive_backup_manager/gdrive_backup_manager_mcp.py"]',
        env: '{}'
      })
    }).then(r => r.json()).then(console.log)
"""

import hashlib
import json
import shutil
import sqlite3
import tarfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="gdrive Backup Manager",
    instructions=(
        "Real, on-demand backup of memory.json + memory_snapshots + app.db "
        "+ portfolio_context.md to DK's real, live gdrive mount. Independent "
        "of whatever mysterious process runs oracle_nightly_backup.py "
        "nightly -- this is a separate, deliberately-callable path, not a "
        "replacement for it."
    ),
)

# Real, container-internal paths -- confirmed directly, not assumed,
# via multiple direct `docker exec ... sqlite3.connect('/app/data/app.db')`
# calls already used successfully tonight for the MCP reconnect fix work.
REAL_MEMORY_JSON = Path("/app/data/memory.json")
SNAPSHOT_DIR = Path("/app/data/memory_snapshots")
REAL_APP_DB = Path("/app/data/app.db")
REAL_PORTFOLIO_CONTEXT = Path("/app/data/portfolio_context.md")

# Real, the new, dedicated, read-write mount added specifically for this
# server (docker-compose.yml, 2026-08-27) -- see this module's own
# docstring for why this is separate from /app/vault_data.
GDRIVE_BACKUP_DIR = Path("/app/gdrive_backups_data")

STAGING_DIR = Path("/tmp/gdrive_backup_manager_staging")
RETENTION_DAYS = 5


def _backup_sqlite_safely(source_path: Path, dest_path: Path) -> None:
    """Real, same approach as oracle_nightly_backup.py's own
    _backup_sqlite_safely -- SQLite's own online backup API, correct
    for a live, actively-written database regardless of journal mode."""
    source_conn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(dest_path)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@mcp.tool()
async def run_gdrive_backup() -> dict:
    """Real, on-demand backup: packages memory.json, memory_snapshots,
    app.db (via SQLite's own safe online-backup API), and
    portfolio_context.md into a dated tarball, copies it to the real
    gdrive mount, verifies via sha256, and prunes anything older than
    RETENTION_DAYS. Returns a dict with what actually happened --
    never silently swallows a failure."""
    if not REAL_MEMORY_JSON.exists():
        return {"success": False, "error": "memory.json not found -- cannot proceed"}

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    object_name = f"memory-backup-{stamp}.tar.gz"
    tarball_path = STAGING_DIR / object_name

    app_db_staged = STAGING_DIR / "app.db"
    app_db_ok = True
    app_db_note = ""
    if REAL_APP_DB.exists():
        try:
            _backup_sqlite_safely(REAL_APP_DB, app_db_staged)
        except Exception as e:
            app_db_ok = False
            app_db_note = f"app.db safe-copy failed (non-fatal): {type(e).__name__}: {e}"
    else:
        app_db_ok = False
        app_db_note = "app.db not found -- skipping (non-fatal)"

    try:
        with tarfile.open(tarball_path, "w:gz") as tar:
            tar.add(REAL_MEMORY_JSON, arcname="memory.json")
            if SNAPSHOT_DIR.exists():
                tar.add(SNAPSHOT_DIR, arcname="memory_snapshots")
            if app_db_ok:
                tar.add(app_db_staged, arcname="app.db")
            portfolio_ok = REAL_PORTFOLIO_CONTEXT.exists()
            if portfolio_ok:
                tar.add(REAL_PORTFOLIO_CONTEXT, arcname="portfolio_context.md")
    except Exception as e:
        return {"success": False, "error": f"packaging failed: {type(e).__name__}: {e}"}
    finally:
        app_db_staged.unlink(missing_ok=True)

    local_hash = _sha256(tarball_path)

    try:
        GDRIVE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        dest_path = GDRIVE_BACKUP_DIR / object_name
        shutil.copy2(tarball_path, dest_path)
        dest_hash = _sha256(dest_path)
        if dest_hash != local_hash:
            return {
                "success": False,
                "error": f"hash mismatch after copy (local {local_hash[:12]}..., "
                         f"gdrive {dest_hash[:12]}...)",
            }
    except Exception as e:
        return {"success": False, "error": f"gdrive copy failed: {type(e).__name__}: {e}"}
    finally:
        tarball_path.unlink(missing_ok=True)

    pruned = 0
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    for old_file in GDRIVE_BACKUP_DIR.glob("memory-backup-*.tar.gz"):
        try:
            date_str = old_file.stem[len("memory-backup-"):-len(".tar")]
            obj_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if obj_date < cutoff:
            old_file.unlink(missing_ok=True)
            pruned += 1

    return {
        "success": True,
        "object_name": object_name,
        "sha256": local_hash,
        "app_db_included": app_db_ok,
        "app_db_note": app_db_note if not app_db_ok else None,
        "portfolio_context_included": portfolio_ok,
        "pruned_old_backups": pruned,
    }


@mcp.tool()
async def list_backups() -> list[dict]:
    """Real, direct listing of every backup archive currently on the
    gdrive mount, with size and modification time -- not cached,
    reads the real directory each call."""
    if not GDRIVE_BACKUP_DIR.exists():
        return []
    results = []
    for f in sorted(GDRIVE_BACKUP_DIR.glob("memory-backup-*.tar.gz")):
        stat = f.stat()
        results.append({
            "name": f.name,
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return results


@mcp.tool()
async def verify_backup_integrity(filename: str) -> dict:
    """Real, direct verification that a named backup archive is a
    genuinely valid, readable tar.gz (not corrupted), and reports its
    real sha256 and the files it actually contains."""
    target = GDRIVE_BACKUP_DIR / filename
    if not target.exists():
        return {"success": False, "error": f"{filename} not found in gdrive backup dir"}

    try:
        with tarfile.open(target, "r:gz") as tar:
            names = tar.getnames()
    except Exception as e:
        return {"success": False, "error": f"archive is not valid/readable: {type(e).__name__}: {e}"}

    return {
        "success": True,
        "filename": filename,
        "sha256": _sha256(target),
        "size_bytes": target.stat().st_size,
        "contains": names,
        "includes_app_db": "app.db" in names,
        "includes_portfolio_context": "portfolio_context.md" in names,
    }


@mcp.tool()
async def get_backup_status() -> dict:
    """Real, direct status summary: the most recent backup's own name,
    age, and whether it includes app.db/portfolio_context.md -- the
    exact two files this server was built to make sure aren't silently
    missing again."""
    backups = await list_backups()
    if not backups:
        return {"has_backups": False}

    latest = backups[-1]
    verify = await verify_backup_integrity(latest["name"])
    age_hours = (time.time() - datetime.fromisoformat(latest["modified"]).timestamp()) / 3600

    return {
        "has_backups": True,
        "latest_backup": latest["name"],
        "age_hours": round(age_hours, 1),
        "total_backups": len(backups),
        "latest_includes_app_db": verify.get("includes_app_db", False),
        "latest_includes_portfolio_context": verify.get("includes_portfolio_context", False),
    }


if __name__ == "__main__":
    mcp.run()
