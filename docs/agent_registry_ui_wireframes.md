# Agent Registry UI — Wireframes (planning artifact, not implementation)

Conceptual layouts for a future frontend surface over the real, verified
agent backend (routes/tasks.py, src/agents/*). These are sketches to plan
against -- no HTML/JS/CSS here, and building them requires real SPA work
in static/index.html + app.js, which hasn't been started.

All three views are read-only from the UI's perspective and consume only:
- GET /api/agent-tasks/registry
- GET /api/agent-tasks/dashboard   (registry + health + queue, merged)
- GET /api/agent-tasks/history/{agent}   (pure read-only, no claiming)

Deliberately never calls GET /api/agent-tasks/pending from a UI context --
that endpoint claims tasks (flips them to "running") as a side effect of
being read, which would be a real bug if wired to a passive "view" action.

## 1. Registry List -- /ui/registry

```
+---------------------------------------------------------------+
|                      Agent Registry                           |
+---------------------------------------------------------------+
| Agent Name        | Enabled | Status | Last Seen | Servers    |
|---------------------------------------------------------------|
| browser_agent     | [ON]    | ALIVE  | 1.8s      | jarvis_browser, filesystem
| filesystem_agent  | [ON]    | ALIVE  | 1.8s      | filesystem
|---------------------------------------------------------------|
| [View Details]    |         |        |           |            |
+---------------------------------------------------------------+
```

- Enabled toggle -> POST /registry/{agent}/enable | /disable
- Status badge -> alive (green) / stale (yellow) / disabled (gray)
- Last Seen -> seconds_since_heartbeat
- Servers -> from the merged registry (sourced from AGENT_CAPABILITIES)
- View Details -> navigates to /ui/registry/{agent}

## 2. Agent Detail -- /ui/registry/{agent}

```
+---------------------------------------------------------------+
|                     browser_agent                             |
+---------------------------------------------------------------+
| Description: Controls Playwright browser automation           |
| Enabled: [ON]                                                 |
| Restart Agent: [Restart]                                      |
+---------------------------------------------------------------+
| Health                                                        |
|---------------------------------------------------------------|
| Status: ALIVE                                                 |
| Last Seen: 1.8s ago                                           |
| Heartbeat Timestamp: 1784165494.004583                        |
+---------------------------------------------------------------+
| Capabilities                                                  |
|---------------------------------------------------------------|
| Servers: jarvis_browser, filesystem                           |
| Allowed Tools: open, search, click, type, run_js, close, ...  |
| Forbidden Tools: delete_file                                  |
+---------------------------------------------------------------+
| Task History                                                  |
|---------------------------------------------------------------|
| [View Task History]                                           |
+---------------------------------------------------------------+
```

- Restart button -> POST /restart/{agent}
- Task history link -> /ui/tasks?agent={agent}

## 3. Agent Task History -- /ui/tasks?agent={agent}

Backend: GET /history/{agent} (pure read-only, confirmed via real test
that repeated reads never change a task's status)

```
+---------------------------------------------------------------+
|                  Task History: browser_agent                  |
+---------------------------------------------------------------+
| ID        | Status   | Tool           | Created At | Updated At |
|---------------------------------------------------------------|
| bff27b83  | pending  | list_directory | 12:33:12   | 12:33:12   |
| 6424cbc0  | failed   | open           | 12:30:01   | 12:30:02   |
| d5f26b18  | success  | list_directory | 12:28:44   | 12:28:45   |
+---------------------------------------------------------------+
| [Back to Agent]                                              |
+---------------------------------------------------------------+
```

## Navigation flow

/ui/registry -> /ui/registry/{agent} -> /ui/tasks?agent={agent}
(back button returns to the detail view)

## Status: not started

This document defines the target shape only. Implementing it means real
frontend work in Odysseus's existing SPA (static/index.html, app.js) --
new routes, components, fetch calls, rendering/refresh logic. Not
attempted yet; the backend endpoints above are the only things built and
verified so far.
