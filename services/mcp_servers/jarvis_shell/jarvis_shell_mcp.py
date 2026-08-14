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

Exposes: run_command(command, cwd=None, timeout=60),
         write_file(path, content, mode="w"),
         git_read(repo_path, action, limit=20),
         http_probe(url, timeout=10)

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
import os
import tempfile

import httpx
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
    # Real safety net, added 2026-08-13: models kept defaulting to this
    # generic tool to "create" .docx/.pptx/.xlsx/.pdf files via echo/cat
    # redirection, producing invalid files with the right extension but
    # plain-text content -- they silently fail to open, and the model
    # confidently reported false success on them (confirmed via a real,
    # direct test tonight). This can't literally redirect to
    # create_document_office (a different tool in a different process's
    # own registry) -- the achievable fix is to detect the pattern and
    # refuse, telling the model to call the correct tool instead. Only
    # matches a real write (>/>>), not a read+pipe like `cat x.docx | grep`.
    import re as _re
    _office_write_pattern = _re.compile(
        r">{1,2}\s*[\w./-]*\.(docx|pptx|xlsx|pdf)\b", _re.IGNORECASE
    )
    if _office_write_pattern.search(command):
        return (
            "REFUSED: this command appears to write a .docx/.pptx/.xlsx/.pdf "
            "file directly via shell redirection. That produces an invalid, "
            "corrupt file that has the right extension but cannot actually be "
            "opened by Word/PowerPoint/Excel/a PDF reader -- do NOT retry this "
            "with a different shell command. Call the create_document_office "
            "tool instead (format, filename, title, sections/slides/rows) -- "
            "it uses real document libraries and produces a genuinely valid file."
        )

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


@mcp.tool()
async def write_file(path: str, content: str, mode: str = "w") -> str:
    """Write or append to a real file, atomically (write-to-temp-then-
    rename, so a crash mid-write can't leave a partial file). mode: 'w'
    to overwrite, 'a' to append. Creates parent directories if needed.
    NOT sandboxed -- can write anywhere the host process can."""
    if mode not in ("w", "a"):
        return f"Error: mode must be 'w' or 'a', got {mode!r}"

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if mode == "a":
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)
            return f"Appended {len(content)} chars to {path}"

        dir_ = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        return f"Wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


@mcp.tool()
async def git_read(repo_path: str, action: str, limit: int = 20) -> str:
    """Real, read-only git info -- status, current branch, or recent log
    -- without needing a full shell git call. action: 'status' | 'log' |
    'branch'. limit: max commits for 'log' (default 20)."""
    if action not in ("status", "log", "branch"):
        return f"Error: action must be 'status', 'log', or 'branch', got {action!r}"

    if action == "status":
        cmd = ["git", "-C", repo_path, "status", "--porcelain=v1", "-b"]
    elif action == "branch":
        cmd = ["git", "-C", repo_path, "branch", "--show-current"]
    else:
        cmd = ["git", "-C", repo_path, "log", f"-n{limit}", "--pretty=format:%H|%an|%ad|%s", "--date=iso"]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return f"Error: {stderr.decode(errors='replace').strip()}"

    out = stdout.decode(errors="replace").strip()
    if action == "log":
        commits = []
        for line in out.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({"sha": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
        import json as _json
        return _json.dumps(commits, indent=2)
    return out or "(clean / no output)"


@mcp.tool()
async def http_probe(url: str, timeout: int = 10) -> str:
    """Real, direct HTTP GET to any reachable URL. Returns status code,
    headers, and a truncated body snippet. NOT restricted to a
    whitelist -- can reach anywhere this host's network can."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"

    import json as _json
    return _json.dumps({
        "status": resp.status_code,
        "headers": dict(resp.headers),
        "body_snippet": resp.text[:1000],
    }, indent=2)


if __name__ == "__main__":
    mcp.run()
