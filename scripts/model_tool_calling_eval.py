#!/usr/bin/env python3
"""
Real, reusable local-model tool-calling evaluation framework.

Built 2026-09-25, directly from a real, live investigation into
xLAM-2-8b-fc-r's tool-calling reliability inside Odysseus. That
investigation found "tool-calling reliability" is not one property --
it is several separable axes that can each independently succeed or
fail, and a model can look "broken" for entirely different real
reasons (format mismatch vs. argument mishandling vs. tool-name
selection collapsing under a large catalog). This script tests each
axis distinctly so a candidate model's actual, specific weakness is
visible, not just a pass/fail verdict.

Designed to be run against ANY OpenAI-compatible local endpoint
(LM Studio, Ollama, etc.) -- not xLAM-specific. Uses Odysseus's own
real, actual tool descriptions (BUILTIN_TOOL_DESCRIPTIONS) so the
"scale" axis reflects genuine tool-catalog complexity, not synthetic
placeholders.

Usage:
    python3 model_tool_calling_eval.py --model llama-xlam-2-8b-fc-r \
        --endpoint http://localhost:1234/v1/chat/completions

Real, honest, current limitations (read before trusting a report):
  - The "format compatibility" axis checks for native tool_calls and
    a bare-JSON-array leak pattern specifically -- not the FULL real
    tool_parsing.py cascade (fenced blocks, [TOOL_REQUEST] markup,
    etc.). Re-sync from the real tool_parsing.py if that logic changes.
  - Argument-fidelity checks are pattern-based (type/shape), not a
    full semantic correctness check.
  - This measures the model's raw generation, not Odysseus's own
    narrowing logic's real output for a given request -- pair this
    with the real, live agent-debug log line (grep "[agent-debug]" in
    the actual container logs) to compare a model's own measured
    threshold against what Odysseus really sends in practice.
"""
import argparse
import json
import sys
import urllib.request
import urllib.error


def load_real_tool_descriptions(odysseus_src_path: str) -> dict:
    """Import BUILTIN_TOOL_DESCRIPTIONS directly from the real, live
    Odysseus source, so this framework always tests against the
    genuine, current tool catalog -- not a stale, hand-copied list."""
    sys.path.insert(0, odysseus_src_path)
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    return dict(BUILTIN_TOOL_DESCRIPTIONS)


def bare_json_array_parses_as_tool_call(raw_text: str) -> bool:
    """Simplified, standalone check for the specific "bare JSON array
    leaked into content" pattern this whole investigation was about --
    NOT the full real tool_parsing.py cascade."""
    text = (raw_text or "").strip()
    if not text.startswith("["):
        return False
    try:
        parsed = json.loads(text)
    except Exception:
        return False
    if not isinstance(parsed, list) or not parsed:
        return False
    return all(isinstance(c, dict) and "name" in c and "arguments" in c for c in parsed)


def call_model(endpoint: str, model: str, prompt: str, tools: list, timeout=90) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "tools": tools,
    }).encode()
    req = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        return {"ok": True, "content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or []}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def make_tool_schema(name: str, description: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
        },
    }


def test_format_compatibility(endpoint, model, descriptions, trials=3) -> dict:
    """Does this model's raw output, when it DOES produce a tool call,
    come out in a shape a real parser can recognize as one? Tests with
    a single, simple, unambiguous tool to isolate format from
    selection difficulty."""
    tool = make_tool_schema("bash", descriptions.get("bash", "Run a shell command."))
    results = []
    for _ in range(trials):
        r = call_model(endpoint, model, "Run the date command.", [tool])
        if not r["ok"]:
            results.append({"recognized_natively": False, "recognized_as_bare_array": False, "raw": r["error"]})
            continue
        native = bool(r["tool_calls"])
        bare = bare_json_array_parses_as_tool_call(r["content"])
        results.append({"recognized_natively": native, "recognized_as_bare_array": bare, "raw": r["content"]})
    return {"trials": results}


def test_argument_fidelity(endpoint, model, descriptions, trials=5) -> dict:
    """With tool selection made easy (one obvious tool), are the
    argument VALUES/TYPES correct? Distinguishes "picked the right
    tool but mangled the args" from genuine selection failure."""
    tool = make_tool_schema("bash", descriptions.get("bash", "Run a shell command."))
    results = []
    for _ in range(trials):
        r = call_model(endpoint, model, "Run the exact command 'date' in bash.", [tool])
        if not r["ok"]:
            results.append({"correct": False, "raw": r["error"]})
            continue
        raw = r["content"]
        correct = False
        try:
            parsed = json.loads(raw) if raw.strip().startswith("[") else None
            if parsed and isinstance(parsed, list):
                args = parsed[0].get("arguments", {})
                cmd = args.get("command") if isinstance(args, dict) else None
                correct = isinstance(cmd, str) and cmd.strip() == "date"
        except Exception:
            pass
        results.append({"correct": correct, "raw": raw})
    return {"trials": results, "correct_rate": sum(1 for t in results if t["correct"]) / len(results)}


def test_scale_curve(endpoint, model, descriptions, scales, trials_per_scale=5) -> dict:
    """The real, direct question: as the tool list grows, at what
    size does this model stop reliably picking the right tool? Uses
    Odysseus's own real tool names/descriptions at each scale, always
    including the real target tool (bash) plus N-1 real distractors."""
    all_names = [n for n in descriptions if n != "bash"]
    curve = {}
    for n in scales:
        distractors = all_names[: max(0, n - 1)]
        tool_names = ["bash"] + distractors
        tools = [make_tool_schema(t, descriptions[t]) for t in tool_names]
        correct = 0
        raws = []
        for _ in range(trials_per_scale):
            r = call_model(endpoint, model, "Run the date command and tell me the current date.", tools)
            raw = r.get("content", "") if r["ok"] else f"ERROR: {r.get('error')}"
            raws.append(raw)
            if r["ok"] and ('"bash"' in raw or any(
                tc.get("function", {}).get("name") == "bash" for tc in r.get("tool_calls", [])
            )):
                correct += 1
        curve[len(tools)] = {"correct_rate": correct / trials_per_scale, "raw_samples": raws}
    return curve


REALISTIC_SYSTEM_PROMPT = """You are Odysseus, DK's personal AI assistant.
- After a tool succeeds, do not second-guess it; reply with one short confirmation unless more work remains.
- After a tool fails, retry with a concrete fix or state what is blocking you.
- Finish only when the user's concrete request is actually done, or clearly state that you are blocked.
- Do not take actions beyond what the user explicitly asked for.
- User identity facts/preferences use manage_memory, not contacts.
- Only save a memory via manage_memory when the user directly states a fact or preference.
- When using tools, make calls in a single JSON array: [{"name": "tool_call_name", "arguments": {"arg1": "value1"}}, ...]
"""  # A real, representative chunk of Odysseus's own actual _AGENT_RULES.


def call_model_with_system(endpoint: str, model: str, prompt: str, tools: list,
                            system: str = None, timeout=90) -> dict:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = json.dumps({"model": model, "messages": messages, "tools": tools}).encode()
    req = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        return {"ok": True, "content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or []}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def test_context_complexity_sensitivity(endpoint, model, descriptions, trials=5) -> dict:
    """Real, direct finding, 2026-09-25: a realistic system prompt alone
    (same tool count, same task) measurably degrades reliability --
    roughly tripled the empty-output failure rate in a real, live test.
    This isolates context complexity as a SEPARATE axis from tool count;
    testing tool-count scaling alone (Axis 3) can understate real
    degradation if the target system also carries a substantial system
    prompt, injected memory, or conversation history."""
    tools = [make_tool_schema(n, descriptions[n]) for n in
             ["bash", "python", "web_search", "web_fetch", "read_file",
              "write_file", "grep", "glob", "ls", "get_workspace"] if n in descriptions]
    prompt = "Run the date command and tell me the current date."

    def _rate(system):
        correct = 0
        for _ in range(trials):
            r = call_model_with_system(endpoint, model, prompt, tools, system=system)
            raw = r.get("content", "") if r["ok"] else ""
            if r["ok"] and '"bash"' in raw:
                correct += 1
        return correct / trials

    without = _rate(None)
    with_prompt = _rate(REALISTIC_SYSTEM_PROMPT)
    return {"without_system_prompt": without, "with_realistic_system_prompt": with_prompt,
            "degradation": without - with_prompt}


def derive_reliable_threshold(curve: dict, bar=0.9):
    """The largest tested tool-count where accuracy stayed >= bar.
    Returns None if even the smallest tested scale failed the bar."""
    best = None
    for n in sorted(curve.keys()):
        if curve[n]["correct_rate"] >= bar:
            best = n
        else:
            break
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True, help="Model identifier as the endpoint expects it")
    ap.add_argument("--endpoint", required=True, help="Full /v1/chat/completions URL")
    ap.add_argument("--odysseus-src", default="/home/dk/jarvis/projects/odysseus",
                     help="Path to the Odysseus repo root, to load its real tool descriptions")
    ap.add_argument("--scales", default="1,5,10,25,50",
                     help="Comma-separated tool-list sizes to test the selection-accuracy curve at")
    ap.add_argument("--trials", type=int, default=5, help="Trials per scale/axis")
    ap.add_argument("--real-tools-sent", type=int, default=None,
                     help="If known (from a real [agent-debug] log line), compare the derived "
                          "reliable threshold directly against what Odysseus actually sends")
    args = ap.parse_args()

    descriptions = load_real_tool_descriptions(args.odysseus_src)
    scales = [int(s) for s in args.scales.split(",")]

    print(f"=== Evaluating {args.model} ===\n")

    print("--- Axis 1: Format compatibility ---")
    fmt = test_format_compatibility(args.endpoint, args.model, descriptions, trials=args.trials)
    native_rate = sum(1 for t in fmt["trials"] if t["recognized_natively"]) / len(fmt["trials"])
    bare_rate = sum(1 for t in fmt["trials"] if t["recognized_as_bare_array"]) / len(fmt["trials"])
    print(f"  Native tool_calls recognized: {native_rate:.0%}")
    print(f"  Bare-JSON-array leak pattern: {bare_rate:.0%}")
    if native_rate == 0 and bare_rate == 0:
        print("  WARNING: neither format recognized -- check raw output manually, "
              "this model may use a third, unhandled format.")
    print()

    print("--- Axis 2: Argument fidelity ---")
    args_result = test_argument_fidelity(args.endpoint, args.model, descriptions, trials=args.trials)
    print(f"  Correct argument rate: {args_result['correct_rate']:.0%}\n")

    print("--- Axis 3b: Context-complexity sensitivity (same tools, +/- realistic system prompt) ---")
    ctx = test_context_complexity_sensitivity(args.endpoint, args.model, descriptions, trials=args.trials)
    print(f"  Without system prompt: {ctx['without_system_prompt']:.0%} correct")
    print(f"  With realistic system prompt: {ctx['with_realistic_system_prompt']:.0%} correct")
    if ctx["degradation"] > 0.15:
        print(f"  WARNING: real degradation ({ctx['degradation']:.0%}) from system-prompt "
              f"complexity alone -- tool-count testing alone will understate real failure rate.\n")
    print()

    print("--- Axis 3: Tool-selection accuracy at scale ---")
    curve = test_scale_curve(args.endpoint, args.model, descriptions, scales, trials_per_scale=args.trials)
    for n in sorted(curve.keys()):
        print(f"  {n:>3} tools: {curve[n]['correct_rate']:.0%} correct")
    threshold = derive_reliable_threshold(curve)
    print(f"\n  Reliable threshold (>=90% correct): {threshold if threshold else 'none tested passed'}")

    if args.real_tools_sent:
        print(f"\n  Real Odysseus request in practice sends: {args.real_tools_sent} tools")
        if threshold is None:
            print("  --> Model's reliable threshold is below even the smallest tested scale. "
                  "NOT currently viable without narrowing tools far below what's tested here.")
        elif threshold >= args.real_tools_sent:
            print("  --> Model's reliable threshold covers real usage. Likely viable as-is.")
        else:
            print(f"  --> Real usage ({args.real_tools_sent}) exceeds this model's reliable "
                  f"threshold ({threshold}). Not viable until Odysseus's own narrowing is "
                  f"tightened to stay at or under {threshold} tools for this model.")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
