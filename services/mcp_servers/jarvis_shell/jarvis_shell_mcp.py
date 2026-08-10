#!/usr/bin/env python3
"""
J.A.R.V.I.S -- Shell MCP Server
=================================
Real, added 2026-08-09: exposes shell command execution via MCP, so it's
reachable the same way as any other MCP tool call (/api/mcp/call), not
just from Jarvis's own internal agent loop (which already has an
unrestricted BashTool at src/agent_tools/subprocess_tools.py -- this is
a separate, additional surface, not a replacement).

Real, explicit safety note: like the existing internal bash tool, this
does NOT sandbox commands -- it can read, write, or reach anything the
underlying OS user can. Given this is reachable via MCP (a broader
surface than internal-agent-loop-only), the real, deliberate mitigation
is registering this server with run_command in approval_required_tools
by default -- a specific command must be reviewed and approved before
it executes, rather than running immediately on call. See
routes/mcp_routes.py's approval endpoints (built 2026-08-09,
commit f12ed0b5) to change this.

Exposes: run_command(command, cwd=None, timeout=60)

Registration:
    fetch('/api/mcp/servers', {
      method: 'POST',
      credentials: 'same-origin',
      body: new URLSearchParams({
        name: 'jarvis_shell',
        transport: 'stdio',
        command: 'python3',
        args: '["/app/services/mcp_servers/jarvis_shell/jarvis_shell_mcp.py"]',
        env: '{}'
      })
    }).then(r => r.json()).then(console.log)
"""

import asyncio

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="JARVIS Shell",
    instructions=(
        "Runs a real shell command and returns its stdout/stderr/exit code. "
        "NOT sandboxed -- has the same real filesystem/network access as the "
        "host process. This server is expected to run with run_command in "
        "approval_required_tools, so calls are staged for human review "
        "rather than executing immediately."
    ),
)

DEFAULT_TIMEOUT = 60


@mcp.tool()
async def run_command(command: str, cwd: str = "", timeout: int = DEFAULT_TIMEOUT) -> str:
    """Run a real shell command. Returns combined stdout/stderr and the
    real exit code. cwd defaults to this server's own working directory
    if not given. timeout is in seconds (default 60); the process is
    killed if it exceeds this."""
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or None,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"Command timed out after {timeout}s and was killed."

    out = stdout.decode(errors="replace").rstrip()
    err = stderr.decode(errors="replace").rstrip()
    result = f"exit_code: {proc.returncode}\n"
    if out:
        result += f"stdout:\n{out}\n"
    if err:
        result += f"stderr:\n{err}\n"
    return result


if __name__ == "__main__":
    mcp.run()
