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

                print(f"[agent_worker] Executing task {task['id']}: {server}.{tool}({arguments})")
                try:
                    result = dash.call_tool(server, tool, arguments, agent=agent_name)
                    if result.get("allowed") is False:
                        # Enforcement blocked it -- record as a failure,
                        # not a crash, since this is an expected outcome.
                        dash.fail_task(task["id"], result)
                        print(f"[agent_worker] Task {task['id']} blocked by capability enforcement.")
                    elif result.get("exit_code", 0) != 0:
                        # The tool itself reported failure (non-zero exit
                        # code) -- this must be a real failure, not
                        # "success" just because enforcement didn't block
                        # it. Previously this branch didn't exist, so any
                        # non-blocked result was marked "success" even when
                        # exit_code was 1.
                        dash.fail_task(task["id"], result)
                        print(f"[agent_worker] Task {task['id']} failed (exit_code={result.get('exit_code')}).")
                    else:
                        dash.complete_task(task["id"], result)
                        print(f"[agent_worker] Task {task['id']} completed.")
                except Exception as e:
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
