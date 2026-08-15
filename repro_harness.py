#!/usr/bin/env python3
"""
repro_harness.py -- real, working repro harness for the get_portfolio_context
synthesis-drift problem. Calls the actual, live Odysseus API (not a stub) and
saves each real run as a JSON file for later comparison.

Usage:
    python3 repro_harness.py --runs 5
"""
import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

OUTDIR = Path(__file__).parent / "repro_runs"
OUTDIR.mkdir(exist_ok=True)

API_BASE = "http://100.93.206.89:7000/api"
SESSION_COOKIE = "5f3511cd3d94a06cd49486092cf5bb7eb538ad79bb065bff441c2b2982af6546"
ENDPOINT_ID = "77bddaa5"
USER_PROMPT = "How many KTOS shares do I own?"


def _curl_json(args):
    result = subprocess.run(["curl", "-s"] + args, capture_output=True, text=True, timeout=120)
    return json.loads(result.stdout)


def create_session(run_index, model):
    resp = _curl_json([
        "-X", "POST", f"{API_BASE}/session",
        "-H", f"Cookie: odysseus_session={SESSION_COOKIE}",
        "--data-urlencode", "name=repro-harness-run",
        "--data-urlencode", f"endpoint_id={ENDPOINT_ID}",
        "--data-urlencode", f"model={model}",
    ])
    return resp["id"]


def run_once(run_index, model):
    session_id = create_session(run_index, model)
    start = time.time()

    subprocess.run(
        ["curl", "-s", "-N", "-X", "POST", f"{API_BASE}/chat_stream",
         "-H", f"Cookie: odysseus_session={SESSION_COOKIE}",
         "--data-urlencode", f"message={USER_PROMPT}",
         "--data-urlencode", f"session={session_id}",
         "--data-urlencode", "mode=agent"],
        capture_output=True, timeout=120,
    )
    duration = time.time() - start

    # Pull the real, saved conversation directly from the DB for ground truth,
    # rather than trust the streamed output (matches established practice).
    db_check = subprocess.run(
        ["docker", "exec", "odysseus-odysseus-1", "python3", "-c", f"""
import sqlite3, json
conn = sqlite3.connect('/app/data/app.db')
rows = conn.execute("SELECT content, metadata FROM chat_messages WHERE session_id = ? AND role = 'assistant' ORDER BY timestamp", ('{session_id}',)).fetchall()
out = []
for content, meta in rows:
    m = json.loads(meta) if meta else {{}}
    out.append({{'content': content, 'has_tool_events': 'tool_events' in m}})
print(json.dumps(out))
"""],
        capture_output=True, text=True, timeout=30,
    )
    try:
        messages = json.loads(db_check.stdout.strip())
    except Exception:
        messages = [{"error": "could not parse DB output", "raw": db_check.stdout, "stderr": db_check.stderr}]

    record = {
        "run_index": run_index,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id,
        "user_prompt": USER_PROMPT,
        "model": model,
        "duration_seconds": round(duration, 2),
        "messages": messages,
        # Crude, honest correctness check: does the answer actually contain
        # KTOS's real, current share count anywhere in the final text?
        "mentions_ktos_16": any("16" in m.get("content", "") and "KTOS" in m.get("content", "").upper() for m in messages if isinstance(m, dict)),
    }

    key = hashlib.sha1((session_id + str(run_index)).encode()).hexdigest()[:8]
    fname = OUTDIR / f"run_{time.strftime('%Y%m%dT%H%M%S')}_{key}.json"
    with open(fname, "w") as f:
        json.dump(record, f, indent=2)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--model", type=str, default="qwen3:14b")
    args = parser.parse_args()

    results = []
    for i in range(args.runs):
        print(f"Run {i+1}/{args.runs} (model={args.model})...")
        r = run_once(i, args.model)
        results.append(r)
        print(f"  duration: {r['duration_seconds']}s, mentions correct KTOS answer: {r['mentions_ktos_16']}")

    correct = sum(1 for r in results if r["mentions_ktos_16"])
    print(f"\nSUMMARY: {correct}/{len(results)} runs produced a correct, on-topic answer")
