"""
ticker_lora_stability_harness.py

Real, reusable stability test harness for the ticker-lookup LoRA
(ticker-lookup-lora), replacing the ad-hoc curl + inline-Python-parsing
approach used repeatedly across a long series of manual investigations
the night this model's production integration was built and fixed
(2026-08-28: naming-collision fix, reasoning-misclassification fix,
content-path sanitizer, deduplication pass).

Runs a configurable set of real, independent multi-round trials
through the actual, live /api/session + /api/chat_stream endpoints
(the real production pipeline, not isolated Ollama API calls), and
reports a clear, structured pass/fail summary checking for every real
failure mode found and fixed that night:
  - no tool call made when one should have been (model reliability)
  - a leaked <tool_call>/</tool_call> tag fragment in visible content
  - an exact, immediate self-repeat of the final answer
  - a completely empty visible response

Usage:
    python3 ticker_lora_stability_harness.py [--tickers SOUN,IONQ,PL] [--endpoint-id 77bddaa5] [--model ticker-lookup-lora]

Must be run where it can reach the real Odysseus instance (e.g. via
`docker exec odysseus-odysseus-1 python3 /path/to/this/script.py`,
matching how it was run and verified during development) -- uses the
same internal-token loopback auth pattern already established and
proven for legitimate internal tooling, not a real user credential.
"""

import argparse
import json
import re
import time
import urllib.request
import urllib.parse

BASE_URL = "http://localhost:7000"
# Real, same internal-token pattern already established and fixed
# (set as a stable ODYSSEUS_INTERNAL_TOKEN env var, not regenerated
# per-process) earlier this same night. Not a secret meant to be kept
# out of this script -- it's a fixed, local, loopback-only value.
INTERNAL_TOKEN = "aeca20c89153b050260dfc3341ee17cf20d21ed8bb1882088f9d844baa692b0a"

DEFAULT_TICKERS = ["SOUN", "IONQ", "RGTI", "KTOS", "PL", "NVDA"]

_TOOL_CALL_TAG_RE = re.compile(r"<tool_call>|</tool_call>", re.IGNORECASE)


def _request(method: str, path: str, fields: dict) -> dict:
    """Real, minimal multipart form POST (matching what /api/session and
    /api/chat_stream expect) using only the standard library -- no
    external dependencies needed to run this."""
    boundary = "----ticker-lora-harness-boundary"
    body_parts = []
    for key, value in fields.items():
        body_parts.append(f"--{boundary}\r\n")
        body_parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n')
        body_parts.append(f"{value}\r\n")
    body_parts.append(f"--{boundary}--\r\n")
    body = "".join(body_parts).encode()

    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={
            "X-Odysseus-Internal-Token": INTERNAL_TOKEN,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()
    return raw


def create_session(endpoint_id: str, model: str, name: str) -> str:
    raw = _request("POST", "/api/session", {
        "name": name,
        "endpoint_id": endpoint_id,
        "model": model,
        "skip_validation": "true",
    })
    return json.loads(raw)["id"]


def send_message(session_id: str, message: str, model: str, mode: str = "agent") -> list:
    """Send a real chat message, return the parsed list of SSE event dicts."""
    raw = _request("POST", "/api/chat_stream", {
        "message": message,
        "session": session_id,
        "selected_model": model,
        "mode": mode,
    })
    events = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


def check_trial(events: list) -> dict:
    """Real checks matching every failure mode found and fixed the
    night this harness was built."""
    content_parts = []
    tool_calls = []
    for e in events:
        if "delta" in e and not e.get("thinking"):
            content_parts.append(e["delta"])
        elif e.get("type") == "tool_start":
            tool_calls.append(e.get("tool"))

    full_content = "".join(content_parts)

    has_leaked_tag = bool(_TOOL_CALL_TAG_RE.search(full_content))

    # Exact, immediate self-repeat check (same logic as the real,
    # shipped _dedupe_full_text fix -- if this ever finds one, the
    # live fix has a real gap worth investigating).
    n = len(full_content)
    has_repeat = False
    if n >= 40:
        for half in range(n // 2, 19, -1):
            first, second = full_content[:half], full_content[half:half * 2]
            if first.strip() and first == second:
                has_repeat = True
                break

    is_empty = not full_content.strip()

    return {
        "tool_calls": tool_calls,
        "content_preview": full_content[:150],
        "has_leaked_tag": has_leaked_tag,
        "has_repeat": has_repeat,
        "is_empty": is_empty,
        "made_tool_call": bool(tool_calls),
    }


def run_trial(ticker: str, endpoint_id: str, model: str) -> dict:
    session_id = create_session(endpoint_id, model, f"stability_harness_{ticker}_{int(time.time())}")
    # Real, first message establishes conversation history, deliberately
    # bypassing the real "direct_low_signal" fast-path (which passes no
    # tools at all) -- confirmed necessary during development, since a
    # single, first-turn ticker question can otherwise skip real agent
    # tool-calling entirely.
    send_message(session_id, "Hi", model)
    events = send_message(session_id, f"Whats {ticker} trading at right now?", model)
    result = check_trial(events)
    result["ticker"] = ticker
    result["session_id"] = session_id
    return result


# Real, added 2026-08-28: a genuinely different test dimension from
# run_trial() above. Every trial so far (including the ones that found
# both real bugs fixed the same night) used a FRESH session per ticker
# -- exactly two turns each ("Hi", then one real question). That never
# tests what a real, longer, multi-topic conversation looks like: many
# real manual investigations earlier the same night reused one session
# across many different questions, and incidentally discovered that
# doing so lets a model's own earlier bad response contaminate later
# turns (the model sees its own prior malformed output as recent
# conversation history and can imitate it). A real suite for this
# model should deliberately test that condition too, not just isolated
# single-question trials.
DEFAULT_SEQUENCE = [
    "SOUN",       # real DK holding -- triggers the holdings-correction path
    "IONQ",       # not a real DK holding -- plain lookup
    "KTOS",       # real DK holding -- triggers the holdings-correction path again
    "__FOLLOWUP__",  # a follow-up question referencing the PRIOR answer, not a new ticker
    "RGTI",       # a final, fresh ticker after several prior turns
]


# Real, added 2026-08-28 after a false-positive was found and confirmed
# during development: the real, deterministic holdings-correction note
# (a fixed template in src/agent_loop.py's own holdings-verification
# block, "(Note: the stored reference document lists ... shares of
# TICKER...)") is EXPECTED, by design, to repeat near-identical
# boilerplate phrasing across different tickers in the same
# conversation -- that is correct, template-generated behavior, not
# the model echoing its own prior free-text answer. Stripped out
# before the contamination check runs, so only genuine, organic
# model-generated overlap can trigger a finding.
_HOLDINGS_NOTE_RE = re.compile(
    r"\(Note: the stored reference document lists.*?\)",
    re.DOTALL,
)


def _cross_turn_contamination(turn_contents: list) -> list:
    """Real, multi-round-specific check: does any later turn's visible
    content contain a large, exact, verbatim chunk of an EARLIER turn's
    own content? This is a different failure shape from within-turn
    repetition (already checked by check_trial's has_repeat) -- it
    would indicate the model echoing its own prior answer into a new,
    unrelated turn, the real contamination pattern observed during
    manual, same-session testing earlier the same night. Returns a list
    of (later_turn_index, earlier_turn_index) pairs where this was
    found; empty list means clean.
    """
    cleaned = [_HOLDINGS_NOTE_RE.sub("", t) for t in turn_contents]
    findings = []
    for i, later in enumerate(cleaned):
        if len(later.strip()) < 40:
            continue
        for j in range(i):
            earlier = cleaned[j]
            if len(earlier.strip()) < 40:
                continue
            # A meaningful, real overlap check: does a substantial
            # (40+ char) chunk of the earlier turn appear verbatim in
            # this later one? Checked in fixed-size windows rather than
            # the whole string, since an exact full-string containment
            # check would miss a partial echo.
            window = 40
            for start in range(0, len(earlier) - window, window):
                chunk = earlier[start:start + window]
                if chunk.strip() and chunk in later:
                    findings.append((i, j))
                    break
            else:
                continue
            break
    return findings


def run_sequence(endpoint_id: str, model: str, sequence: list = None) -> dict:
    """Real, multi-round conversation test: one session, several real
    messages in a row (mixing real DK holdings, plain tickers, and a
    genuine follow-up question), checking each turn individually AND
    checking for cross-turn contamination across the whole sequence.
    """
    sequence = sequence or DEFAULT_SEQUENCE
    session_id = create_session(endpoint_id, model, f"stability_sequence_{int(time.time())}")
    send_message(session_id, "Hi", model)

    turn_results = []
    turn_contents = []
    for item in sequence:
        if item == "__FOLLOWUP__":
            message = "Is that price up or down from what you just told me?"
        else:
            message = f"Whats {item} trading at right now?"
        events = send_message(session_id, message, model)
        r = check_trial(events)
        r["prompt"] = message
        turn_results.append(r)
        content_parts = [e["delta"] for e in events if "delta" in e and not e.get("thinking")]
        turn_contents.append("".join(content_parts))

    contamination = _cross_turn_contamination(turn_contents)

    return {
        "session_id": session_id,
        "turn_results": turn_results,
        "cross_turn_contamination": contamination,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--endpoint-id", default="77bddaa5")
    parser.add_argument("--model", default="ticker-lookup-lora")
    parser.add_argument("--sequence", action="store_true",
                         help="Run the multi-round conversation sequence test "
                              "instead of independent single-question trials.")
    parser.add_argument("--sequence-runs", type=int, default=1,
                         help="Number of independent sequence runs (each its "
                              "own fresh session) when --sequence is used.")
    args = parser.parse_args()

    if args.sequence:
        print(f"Running {args.sequence_runs} real multi-round conversation "
              f"sequence(s) against {args.model} (endpoint {args.endpoint_id})...\n")
        all_clean = True
        for run_i in range(args.sequence_runs):
            result = run_sequence(args.endpoint_id, args.model)
            print(f"--- Sequence run {run_i + 1} (session {result['session_id']}) ---")
            for i, r in enumerate(result["turn_results"]):
                flags = []
                if r["has_leaked_tag"]:
                    flags.append("LEAKED_TAG")
                if r["has_repeat"]:
                    flags.append("REPEATED")
                if r["is_empty"]:
                    flags.append("EMPTY")
                if not r["made_tool_call"] and "__FOLLOWUP__" not in r.get("prompt", ""):
                    flags.append("NO_TOOL_CALL")
                status = "CLEAN" if not flags else " ".join(flags)
                if flags:
                    all_clean = False
                print(f"  turn {i+1} [{r['prompt'][:50]}]: {status}")
            if result["cross_turn_contamination"]:
                all_clean = False
                print(f"  CROSS-TURN CONTAMINATION found: {result['cross_turn_contamination']}")
            else:
                print("  cross-turn contamination check: clean")
            print()
        print("=== Sequence Summary ===")
        print("All runs completely clean:" , all_clean)
        return

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    print(f"Running {len(tickers)} real, independent trials against {args.model} "
          f"(endpoint {args.endpoint_id})...\n")

    results = []
    for ticker in tickers:
        print(f"  {ticker}...", end=" ", flush=True)
        try:
            r = run_trial(ticker, args.endpoint_id, args.model)
        except Exception as e:
            r = {"ticker": ticker, "error": str(e)}
        results.append(r)
        if "error" in r:
            print(f"ERROR: {r['error']}")
        else:
            flags = []
            if r["has_leaked_tag"]:
                flags.append("LEAKED_TAG")
            if r["has_repeat"]:
                flags.append("REPEATED")
            if r["is_empty"]:
                flags.append("EMPTY")
            if not r["made_tool_call"]:
                flags.append("NO_TOOL_CALL")
            status = "CLEAN" if not flags else " ".join(flags)
            print(status)

    print("\n=== Summary ===")
    n = len(results)
    clean = sum(1 for r in results if "error" not in r and not any([
        r["has_leaked_tag"], r["has_repeat"], r["is_empty"], not r["made_tool_call"]
    ]))
    leaked = sum(1 for r in results if r.get("has_leaked_tag"))
    repeated = sum(1 for r in results if r.get("has_repeat"))
    empty = sum(1 for r in results if r.get("is_empty"))
    no_tool = sum(1 for r in results if "error" not in r and not r.get("made_tool_call"))
    errors = sum(1 for r in results if "error" in r)

    print(f"Total trials: {n}")
    print(f"Completely clean: {clean}/{n}")
    print(f"Leaked tag fragments: {leaked}")
    print(f"Repeated/duplicated content: {repeated}")
    print(f"Empty responses: {empty}")
    print(f"No tool call made: {no_tool}")
    print(f"Request errors: {errors}")

    return results


if __name__ == "__main__":
    main()
