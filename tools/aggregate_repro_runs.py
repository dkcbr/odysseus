#!/usr/bin/env python3
"""
aggregate_repro_runs.py -- reads the real JSON files produced by
repro_harness.py and computes tool_call_rate and correct_answer_rate.

Matches repro_harness.py's actual, real output schema (run_index,
timestamp, session_id, user_prompt, model, duration_seconds, messages,
mentions_ktos_16) -- not a generic/assumed schema.
"""
import json
from pathlib import Path

RUN_DIR = Path(__file__).parent.parent / "repro_runs"
OUT_CSV = Path(__file__).parent.parent / "repro_summary.csv"


def load_runs(model=None):
    runs = []
    for p in sorted(RUN_DIR.glob("run_*.json")):
        try:
            r = json.load(p.open(encoding="utf-8"))
            if model is None or r.get("model") == model:
                runs.append(r)
        except Exception as e:
            print(f"Skipping {p}: {e}")
    return runs


def summarize(runs):
    total = len(runs)
    if total == 0:
        return None

    tool_called = sum(
        1 for r in runs
        if any(m.get("has_tool_events") for m in r.get("messages", []) if isinstance(m, dict))
    )
    correct = sum(1 for r in runs if r.get("mentions_ktos_16"))
    tool_but_wrong = sum(
        1 for r in runs
        if any(m.get("has_tool_events") for m in r.get("messages", []) if isinstance(m, dict))
        and not r.get("mentions_ktos_16")
    )
    avg_duration = sum(r.get("duration_seconds", 0) for r in runs) / total

    return {
        "total": total,
        "tool_call_rate": round(tool_called / total, 3),
        "correct_answer_rate": round(correct / total, 3),
        "tool_called_but_still_wrong": tool_but_wrong,
        "avg_duration_seconds": round(avg_duration, 2),
    }


def main():
    all_runs = load_runs()
    models = sorted(set(r.get("model", "unknown") for r in all_runs))

    with OUT_CSV.open("w") as f:
        f.write("model,total,tool_call_rate,correct_answer_rate,tool_called_but_still_wrong,avg_duration_seconds\n")
        for model in models:
            runs = [r for r in all_runs if r.get("model") == model]
            summary = summarize(runs)
            if summary is None:
                continue
            print(f"Model: {model} ({summary['total']} real runs)")
            print(f"  tool_call_rate: {summary['tool_call_rate']}")
            print(f"  correct_answer_rate: {summary['correct_answer_rate']}")
            print(f"  tool called but still wrong: {summary['tool_called_but_still_wrong']}")
            print(f"  avg duration: {summary['avg_duration_seconds']}s")
            print()
            f.write(f"{model},{summary['total']},{summary['tool_call_rate']},{summary['correct_answer_rate']},{summary['tool_called_but_still_wrong']},{summary['avg_duration_seconds']}\n")
    print(f"Wrote {OUT_CSV}")


if __name__ == "__main__":
    main()
