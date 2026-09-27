#!/usr/bin/env python3
"""
J.A.R.V.I.S -- Local Coding MCP Server
=======================================
Real, added 2026-09-27 at DK's request ("wire into jarvis"), after
DK evaluated qwen3:14b directly (a real coding trial: correct merge-
intervals implementation, all 3 assertions passed when actually run;
a real code-review trial against buy_rung_ladder_agent.py: decent
structural understanding but too generic to trust unsupervised for
real bug-hunting -- see the trial results DK reviewed before deciding
to wire this in). This gives Jarvis (and Claude working within it) a
real, callable local-model coding tool -- fast, free, fully GPU-
resident (confirmed 100% GPU, ~9.6GB of the box's 16GB VRAM, no CPU
offload) -- for lower-stakes coding tasks: boilerplate, draft unit
tests, docstrings, quick explanations. NOT positioned as a substitute
for real review on anything that touches the live trading agents or
other high-stakes code; that judgment stays with whoever's using this
tool, this server just exposes the model.

Talks directly to the real, already-running Ollama server on the HOST
box (not inside this MCP server's own container) via its HTTP
/api/generate endpoint -- not the `ollama run` CLI, which interleaves
ANSI cursor-control codes into a live terminal and isn't safe to parse
programmatically. The API cleanly separates the model's <think>
reasoning trace (returned as its own "thinking" field) from the real
final answer ("response") -- confirmed directly against a real
/api/generate call before this server was written.

Real, confirmed directly (2026-09-27): this MCP server runs inside the
odysseus-odysseus-1 container on the odysseus_default bridge network,
where plain "localhost" refers to the container itself, NOT the host
running Ollama -- a real curl from inside the container to
localhost:11434 returned nothing, while host.docker.internal:11434
returned Ollama's real /api/tags model list. So this deliberately
targets host.docker.internal, not localhost.

Exposes: local_code_task(prompt, model="qwen3:14b", include_thinking=False)

Registration:
    fetch('/api/mcp/servers', {
      method: 'POST',
      credentials: 'same-origin',
      body: new URLSearchParams({
        name: 'local_coding',
        transport: 'stdio',
        command: 'python3',
        args: '["/app/services/mcp_servers/local_coding/local_coding_mcp.py"]',
        env: '{}'
      })
    }).then(r => r.json()).then(console.log)
"""

import requests
from mcp.server.fastmcp import FastMCP

OLLAMA_URL = "http://host.docker.internal:11434/api/generate"

# Real, deliberate default: confirmed directly (2026-09-27) to run
# 100% GPU-resident on this box's RTX 4070 Ti SUPER (16GB VRAM) at
# ~9.6GB -- the strongest Qwen coding model that fits fully in VRAM
# without CPU-offload slowdown. qwen3-coder:30b and qwen3.6:35b-a3b
# are both too large to fit and were deliberately excluded for that
# reason (DK: "I don't want to use it if it can[not] run in VRAM").
DEFAULT_MODEL = "qwen3:14b"

# Real, generous but bounded -- a real local-model call on this box's
# hardware was timed directly at 45-55s for a real, substantive prompt
# (a full-file code review). 300s leaves real headroom for a bigger
# prompt without hanging the calling agent indefinitely on a stuck
# request.
REQUEST_TIMEOUT_SECONDS = 300

mcp = FastMCP(
    name="JARVIS Local Coding",
    instructions=(
        "Runs a coding-related prompt against a real, local, GPU-resident "
        "Ollama model on this box (default qwen3:14b) instead of a hosted "
        "model -- fast and free, good for boilerplate, draft unit tests, "
        "docstrings, explaining a snippet, or a quick first-pass summary "
        "of what a file does. Confirmed (2026-09-27) to give correct, "
        "runnable code on a real test task, but its code-review judgment "
        "was found too generic to trust unsupervised on real bugs in "
        "live, high-stakes code (e.g. the trading rung agents) -- treat "
        "its output here the same way: a fast first pass, not a "
        "substitute for real review on anything that matters."
    ),
)


@mcp.tool()
async def local_code_task(
    prompt: str,
    model: str = DEFAULT_MODEL,
    include_thinking: bool = False,
) -> str:
    """Run a coding-related prompt against a real, local Ollama model on
    this box and return its real answer.

    Args:
        prompt: The real task -- write code, explain a snippet, review a
            file, draft tests, etc. Paste real file contents inline when
            relevant; there's no separate file-reading here.
        model: Which real, already-pulled Ollama model to use. Defaults
            to qwen3:14b (confirmed 100% GPU-resident on this box). Pick
            a smaller one (qwen3:4b, qwen2.5:7b) for a faster, weaker
            pass, or a different pulled model by its real Ollama tag.
        include_thinking: If true, prefixes the real answer with the
            model's own reasoning trace (its <think> content) as a
            separate labelled section. Off by default -- most callers
            just want the final answer.

    Returns a clear error message (never raises) if Ollama isn't
    reachable, the model isn't pulled, or the real request times out --
    a local-model outage should never look like a silent empty answer.
    """
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.ConnectionError:
        return (
            f"Local coding tool error: could not reach the real Ollama "
            f"server at {OLLAMA_URL} -- is it running on this box?"
        )
    except requests.exceptions.Timeout:
        return (
            f"Local coding tool error: real request to model {model!r} "
            f"did not finish within {REQUEST_TIMEOUT_SECONDS}s -- the "
            f"prompt may be too large, or the model is under heavy load."
        )

    if resp.status_code == 404:
        return (
            f"Local coding tool error: model {model!r} is not pulled on "
            f"this box's real Ollama instance (404 from /api/generate). "
            f"Run `ollama pull {model}` first, or use an already-pulled "
            f"model (see `ollama list`)."
        )
    if not resp.ok:
        return (
            f"Local coding tool error: real Ollama server returned "
            f"HTTP {resp.status_code}: {resp.text[:500]}"
        )

    data = resp.json()
    answer = data.get("response", "").strip()
    if not answer:
        return "Local coding tool error: model returned an empty response."

    if include_thinking:
        thinking = data.get("thinking", "").strip()
        if thinking:
            return f"--- Reasoning trace ---\n{thinking}\n\n--- Answer ---\n{answer}"

    return answer


if __name__ == "__main__":
    mcp.run()
