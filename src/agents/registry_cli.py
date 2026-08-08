#!/usr/bin/env python3
"""
src/agents/registry_cli.py

Simple CLI dashboard over /api/agent-tasks/dashboard (registry + health +
queue merged). Real, minimal presentation layer -- no new backend logic,
no duplicated state; just formats what the server already returns.

Run:
    python3 src/agents/registry_cli.py
"""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agents.odysseus_auth import get_session
from src.agents.odysseus_dashboard import OdysseusDashboard


def format_table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [max(len(str(r[i])) for r in ([headers] + rows)) for i in range(len(headers))]
    lines = []
    lines.append("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    return "\n".join(lines)


def main():
    dash = OdysseusDashboard(get_session())
    snap = dash.get_queue_dashboard()

    registry = snap.get("registry", {})
    health = snap.get("agents", {})

    print("\n=== AGENTS ===")
    rows = []
    for agent, reg in registry.items():
        h = health.get(agent, {})
        rows.append([
            agent,
            "yes" if reg.get("enabled") else "no",
            h.get("status", "never seen"),
            f"{h.get('seconds_since_heartbeat', '-')}s ago" if h else "-",
            ",".join(reg.get("servers", [])),
        ])
    print(format_table(rows, ["Agent", "Enabled", "Status", "Last Seen", "Servers"]))

    print(f"\n=== QUEUE (total: {snap.get('total', 0)}) ===")
    print(f"Pending: {len(snap.get('pending', []))}  "
          f"Running: {len(snap.get('running', []))}  "
          f"Failed: {len(snap.get('failed', []))}  "
          f"Success: {len(snap.get('success', []))}")

    if snap.get("failed"):
        print("\n--- Recent failures ---")
        for t in snap["failed"][-5:]:
            err = (t.get("result") or {}).get("error", "?")
            print(f"  [{t['id']}] {t['agent']} -> {t['server']}.{t['tool']}: {err}")

    print()


if __name__ == "__main__":
    main()
