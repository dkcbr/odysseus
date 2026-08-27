"""
src/agents/agent_worker.py

Real, standing agent worker process -- polls /api/agent-tasks/pending for
its own agent role only, executes tools through the enforced /api/mcp/call
path, and reports completion/failure back to the queue.

Run:
    python3 src/agents/agent_worker.py [agent_name]

Defaults to AGENT_NAME below if no argument given. Wrap in systemd/tmux/a
Docker container to keep it running continuously.
"""

import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agents.odysseus_auth import get_session
from src.agents.odysseus_dashboard import OdysseusDashboard

DEFAULT_AGENT_NAME = "browser_agent"
POLL_INTERVAL_SECONDS = 2
SESSION_RETRY_SECONDS = 5

# Real, added 2026-08-26: backoff delay for tasks that have already
# failed at least once (retry_count > 0). Directly motivated by a real,
# actual incident earlier tonight: a real, live Odysseus session asked
# "What's the price of SPCX?" and the model repeatedly reached for the
# wrong tool, retrying variations of the same failing approach across
# several turns. Confirmed directly (not assumed) that the real,
# existing server-side retry mechanism (routes/tasks.py's
# fail_task_db_native) already re-queues a failed task as "pending"
# immediately, with no delay -- combined with this worker's own 2s poll
# interval, a failing task gets retried almost instantly, with no real
# gap for the model to reconsider its approach. This is a small,
# deterministic backoff, not exponential/adaptive -- proportionate to
# the real, small number of retries (max_retries defaults to 3
# server-side) rather than a general-purpose backoff library.
RETRY_BACKOFF_SECONDS = {1: 2, 2: 5, 3: 10, 4: 20}

# Real, added 2026-08-26: per-(server, tool) cooldown, kept entirely
# in-memory in this one worker process -- deliberately NOT
# supervisor_state.json, confirmed directly that file is already, really
# owned by a separate process-supervision subsystem (tracks per-agent
# alive/restart_count/locked_until, not tool failures at all) -- adding
# an unrelated "tool_failures" key to it risked a real, genuine conflict
# with whatever process legitimately writes it. Keyed by (server, tool),
# not tool name alone, since two different, real MCP servers could
# expose a tool with the same name but unrelated behavior.
#
# Real, honest tradeoff worth naming: this is a genuinely coarser
# safeguard than the per-task backoff above -- a single hard failure
# blocks ALL tasks using that (server, tool) pair for the cooldown
# window, including unrelated, would-have-succeeded ones. Chosen
# anyway because a tool/server that's genuinely broken shouldn't get
# hammered by every task that happens to reach for it while it's down.
# Real, added 2026-08-26 (later same session): adaptive scaling on top
# of the fixed 30s cooldown above. A tool failing for the first time
# and a tool that's failed 5 times in a row recently are genuinely
# different situations -- the latter deserves a real, longer break,
# not the same flat window. Scaled by real, cumulative failure count
# (not just the most recent failure's timestamp, which the original
# design tracked). Capped, not unbounded -- a tool shouldn't end up
# effectively disabled forever from a real, historical failure streak.
ADAPTIVE_COOLDOWN_SCHEDULE = {1: 30, 2: 60, 3: 120, 4: 300}
ADAPTIVE_COOLDOWN_MAX_SECONDS = 300

# (server, tool) -> {"last_failure": float, "failure_count": int}
_tool_failures: dict[tuple[str, str], dict] = {}


def _record_tool_failure(server: str, tool: str) -> None:
    entry = _tool_failures.setdefault((server, tool), {"last_failure": 0.0, "failure_count": 0})
    entry["last_failure"] = time.time()
    entry["failure_count"] += 1


def _record_tool_success(server: str, tool: str) -> None:
    """Real, added 2026-08-26: a genuine success is the clearest real
    signal a tool has recovered -- resets its failure count entirely,
    rather than requiring it to just time out on its own. Without this,
    a tool that failed once, long ago, then succeeded many times since
    would still be treated identically to one currently, repeatedly
    failing, the next time it happens to fail again."""
    if (server, tool) in _tool_failures:
        del _tool_failures[(server, tool)]


def _tool_on_cooldown(server: str, tool: str) -> float | None:
    """Returns the real, remaining cooldown in seconds if this (server,
    tool) pair recently failed, or None if it's clear to use. Cooldown
    window scales with the real, cumulative failure count -- see
    ADAPTIVE_COOLDOWN_SCHEDULE's own comment above."""
    entry = _tool_failures.get((server, tool))
    if entry is None:
        return None
    count = entry["failure_count"]
    window = ADAPTIVE_COOLDOWN_SCHEDULE.get(count, ADAPTIVE_COOLDOWN_MAX_SECONDS)
    remaining = window - (time.time() - entry["last_failure"])
    return remaining if remaining > 0 else None


def run_agent(agent_name: str = DEFAULT_AGENT_NAME):
    print(f"[agent_worker] Starting as '{agent_name}', polling every {POLL_INTERVAL_SECONDS}s...")

    session = None
    dash = None

    while True:
        # Establish (or re-establish) the session if we don't have one yet
        # -- e.g. first startup, or a previous iteration's connection error
        # forced us to drop it. Odysseus being briefly unavailable (a
        # container rebuild/restart, a network blip) should never crash
        # this whole process -- just wait and retry.
        if dash is None:
            try:
                session = get_session()
                dash = OdysseusDashboard(session)
                print("[agent_worker] Session established.")
            except Exception as e:
                print(f"[agent_worker] Could not reach Odysseus yet ({e}); retrying in {SESSION_RETRY_SECONDS}s...")
                time.sleep(SESSION_RETRY_SECONDS)
                continue

        try:
            # Heartbeat first, every loop, so health reflects reality even
            # if this iteration finds no tasks at all.
            try:
                dash.agent_heartbeat(agent_name)
            except Exception as e:
                print(f"[agent_worker] Heartbeat failed (non-fatal): {e}")

            # IMPORTANT: pass agent= here so the SERVER only returns and
            # claims (marks "running") tasks belonging to THIS agent.
            # Calling get_pending_tasks() with no filter would claim every
            # agent's pending tasks system-wide, silently stranding any
            # that don't match this worker in "running" forever.
            tasks = dash.get_pending_tasks(agent=agent_name)

            for task in tasks:
                server = task["server"]
                tool = task["tool"]
                arguments = task["arguments"]

                # Real, fixed 2026-08-26: /pending itself already rejects
                # capability violations server-side (routes/tasks.py's own
                # get_pending_tasks_db_native) and returns them in this
                # SAME list with status already set to "failed" -- this
                # worker was never checking that, so it went on to call
                # call_tool() (which correctly re-rejected it) and then
                # fail_task() on an already-failed task, which correctly
                # 409'd since the task was no longer "running". Confirmed
                # live, directly, via the task's own real event log
                # (a single "rejected_tool" event, status already
                # "failed", before the worker ever touched it) -- not a
                # network/connection issue as first suspected. Skipping
                # here (not calling fail_task again) avoids the redundant
                # call_tool + guaranteed-409 fail_task entirely.
                if task.get("status") == "failed":
                    print(f"[agent_worker] Task {task['id']} already rejected "
                          f"server-side (capability enforcement); skipping.")
                    continue

                # Real, added 2026-08-26: back off before re-attempting a
                # task that's already failed at least once -- see
                # RETRY_BACKOFF_SECONDS's own comment for the real, full
                # reasoning. A fresh task (retry_count == 0) is
                # unaffected -- this only slows down repeats of an
                # already-failing plan, not first attempts.
                retry_count = task.get("retry_count", 0)
                if retry_count > 0:
                    delay = RETRY_BACKOFF_SECONDS.get(retry_count, max(RETRY_BACKOFF_SECONDS.values()))
                    print(f"[agent_worker] Task {task['id']} is a retry (attempt {retry_count}); "
                          f"backing off {delay}s before re-attempting.")
                    time.sleep(delay)

                # Real, added 2026-08-26: skip execution entirely if this
                # (server, tool) pair recently hard-failed -- see
                # TOOL_COOLDOWN_SECONDS's own comment for the full, real
                # reasoning and honest tradeoff.
                cooldown_remaining = _tool_on_cooldown(server, tool)
                if cooldown_remaining is not None:
                    fail_count = _tool_failures[(server, tool)]["failure_count"]
                    dash.fail_task(task["id"], {
                        "error": f"Tool '{tool}' on server '{server}' is on cooldown "
                                 f"({cooldown_remaining:.0f}s remaining, {fail_count} recent failures) "
                                 f"due to a recent, real failure."
                    })
                    print(f"[agent_worker] Task {task['id']} skipped -- {server}.{tool} "
                          f"on cooldown ({cooldown_remaining:.0f}s remaining, {fail_count} recent failures).")
                    continue

                print(f"[agent_worker] Executing task {task['id']}: {server}.{tool}({arguments})")
                try:
                    result = dash.call_tool(server, tool, arguments, agent=agent_name)
                    if result.get("allowed") is False:
                        # Real, fixed 2026-08-26: _record_tool_failure now
                        # runs BEFORE dash.fail_task -- confirmed live,
                        # directly, that the old ordering had a genuine bug:
                        # if fail_task() itself raised (e.g. a real 409 from
                        # Odysseus, seen live during cooldown testing), the
                        # whole block got caught by the outer session-level
                        # except below, and _record_tool_failure never ran
                        # at all -- so a failure that couldn't even be
                        # *reported* silently never armed the cooldown
                        # either. This ordering makes cooldown tracking
                        # correct regardless of whether reporting back to
                        # Odysseus itself succeeds.
                        _record_tool_failure(server, tool)
                        dash.fail_task(task["id"], result)
                        print(f"[agent_worker] Task {task['id']} blocked by capability enforcement.")
                    elif result.get("exit_code", 0) != 0:
                        # The tool itself reported failure (non-zero exit
                        # code) -- this must be a real failure, not
                        # "success" just because enforcement didn't block
                        # it. Previously this branch didn't exist, so any
                        # non-blocked result was marked "success" even when
                        # exit_code was 1.
                        _record_tool_failure(server, tool)
                        dash.fail_task(task["id"], result)
                        print(f"[agent_worker] Task {task['id']} failed (exit_code={result.get('exit_code')}).")
                    else:
                        _record_tool_success(server, tool)
                        dash.complete_task(task["id"], result)
                        print(f"[agent_worker] Task {task['id']} completed.")
                except Exception as e:
                    _record_tool_failure(server, tool)
                    dash.fail_task(task["id"], {"error": str(e)})
                    print(f"[agent_worker] Task {task['id']} failed: {e}")

        except Exception as e:
            # A real connection-level failure (Odysseus restarting, network
            # blip, etc.) -- log it, drop the session so it's rebuilt next
            # loop, and keep the process alive instead of crashing.
            print(f"[agent_worker] Lost connection to Odysseus ({e}); will retry.")
            dash = None
            session = None
            time.sleep(SESSION_RETRY_SECONDS)
            continue

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    agent_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_AGENT_NAME
    run_agent(agent_name)
