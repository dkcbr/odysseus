#!/usr/bin/env python3
"""
Real, direct caller for Odysseus's agent loop, bypassing the chat UI
entirely. Uses the exact verified pattern from tonight's manual test
(form-data POST to /api/chat_stream, mode=agent) -- see
HARNESS_NOTES.md for the full, honest account of how this was found
and verified, including a proposed JSON-body pattern that was checked
and found NOT to match this route's own real parsing logic.

Run from inside the odysseus-odysseus-1 container, e.g.:
  docker exec odysseus-odysseus-1 python3 /app/mcp_servers/gods-eye-view-mcp/call_agent_loop.py --help
"""
import argparse
import json
import re
import sys
import urllib.request
import urllib.error

CHAT_STREAM_URL = "http://localhost:7000/api/chat_stream"


def call_agent_loop(token: str, session_id: str, message: str, timeout: int = 45) -> dict:
    """
    POST one message to the real chat_stream endpoint in agent mode,
    and parse its real SSE response stream for the one 'metrics' event
    that carries tool_events -- the actual, real signal this harness
    cares about. Returns a dict with tool_events (list), round_texts
    (list), and raw_sse (the full real response text, for debugging
    anything this parser doesn't handle).
    """
    boundary = "----gevharness"
    fields = {"message": message, "session": session_id, "mode": "agent"}
    body_parts = []
    for name, value in fields.items():
        body_parts.append(f"--{boundary}\r\n")
        body_parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
        body_parts.append(f"{value}\r\n")
    body_parts.append(f"--{boundary}--\r\n")
    body = "".join(body_parts).encode("utf-8")

    req = urllib.request.Request(
        CHAT_STREAM_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode("utf-8", errors="replace")}
    except Exception as e:
        return {"error": str(e)}

    # Real, direct parse of the SSE stream: find every `data: {...}` line,
    # keep the one whose JSON has type == "metrics" (that's the one
    # carrying tool_events in the real, observed response shape).
    tool_events, round_texts = [], []
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):].strip()
        if payload == "[DONE]":
            continue
        try:
            evt = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if evt.get("type") == "metrics":
            data = evt.get("data", {})
            tool_events = data.get("tool_events", [])
            round_texts = data.get("round_texts", [])

    return {"tool_events": tool_events, "round_texts": round_texts, "raw_sse": raw}


def run_corpus(token: str, session_id: str, corpus_path: str, out_path: str, limit: int | None = None):
    import datetime

    with open(corpus_path) as f:
        prompts = [json.loads(line) for line in f if line.strip()]
    if limit:
        prompts = prompts[:limit]

    results = []
    with open(out_path, "a") as out:
        for i, item in enumerate(prompts, 1):
            print(f"[{i}/{len(prompts)}] {item['id']}: {item['prompt']!r}", file=sys.stderr)
            result = call_agent_loop(token, session_id, item["prompt"])
            record = {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "prompt_id": item["id"],
                "prompt": item["prompt"],
                "expected_tool": item["expected_tool"],
                "tool_events": result.get("tool_events", []),
                "round_texts": result.get("round_texts", []),
                "error": result.get("error"),
            }
            out.write(json.dumps(record) + "\n")
            out.flush()
            results.append(record)
    return results


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--token", required=True, help="A real, valid ody_ bearer token")
    p.add_argument("--session", required=True, help="A real, existing session id")
    p.add_argument("--corpus", default="test_prompts.jsonl")
    p.add_argument("--out", default="trial_results.jsonl")
    p.add_argument("--limit", type=int, default=None, help="Only run the first N prompts")
    args = p.parse_args()

    run_corpus(args.token, args.session, args.corpus, args.out, args.limit)
    print(f"Done. Results appended to {args.out}", file=sys.stderr)
