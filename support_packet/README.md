# Support packet: qwen3:14b synthesis-drift failure

## Summary

`get_portfolio_context` (a real, custom tool returning a ~19KB markdown
document) is invoked correctly 100% of the time when relevant, but the
model's final answer, synthesized after the tool result is added back
into the conversation, fails to address the user's actual question
100% of the time across all 10 real trials captured here.

## Environment

- Model: `qwen3:14b`
- Inference: Ollama (local), endpoint context window: 40,960 tokens
  (confirmed correctly applied via runtime metrics, not the model's
  native 131,072 max -- capped for this deployment's hardware)
- Test question (all 10 runs): "How many KTOS shares do I own?"
- Real, correct answer: 16 shares (present verbatim in the tool
  output every single run)

## Tool description in use

```
Fetch DK's real, current portfolio context (holdings, strategy, rules,
thesis notes) from data/portfolio_context.md. ALWAYS call this for
any question about a specific position, balance, holding, or stored
trading rule -- never assume you already know the answer, since this
file updates over time and you do not have it pre-loaded. This
returns a large, complete reference document -- after calling it,
find and state the SPECIFIC fact the user actually asked about (e.g.
one ticker's share count), not a general summary of everything in the
document.
```

## Two real batches, two real conditions

### Batch A (runs 1-5, timestamps 03:09:26-03:10:44): baseline

No modification to the agent loop. Tool called successfully in every
run. Final answers were varied, unrelated non-answers -- a generic
restatement of persona rules, a fabricated "check-in summary," an
infrastructure status report -- but none were outright fabricated,
unrelated financial data.

### Batch B (runs 6-10, timestamps 03:19:31-03:20:46): reminder-message experiment (reverted)

One real, targeted change was tried: a short reminder message was
injected into the conversation immediately after the tool result,
before the next round:

```python
if any(len(t) > 2000 for t in tool_result_texts):
    messages.append({
        "role": "user",
        "content": "(Reminder: use the tool result above to directly
        answer the original question. Do not produce a general
        summary or status report.)",
    })
```

This made things measurably worse, not better: tool call rate
remained 100%, but final answers shifted from generic non-answers to
outright hallucination of unrelated financial content -- one run
fabricated a detailed EGX30 (Egyptian stock index) analysis with
specific, invented prices and percentages, entirely disconnected from
both the real question and the real tool data. Confirmed via direct
DB query that the correct tool was still called in every Batch B run
(ruling out session/context mixing as the cause). This change was
reverted immediately after this batch was captured; it is not live in
production.

## Aggregate metrics (all 10 runs combined)

- `tool_call_rate`: 1.0
- `correct_answer_rate`: 0.0
- Average response duration: ~20s

## How to reproduce

```
python3 repro_harness.py --runs N
python3 tools/aggregate_repro_runs.py
```

(Both scripts live in the main repo, not included in this packet --
they call the real, live application directly and require access to
this specific deployment.)

## Files in this packet

10 JSON files, one per run, each containing: the exact user prompt,
model name, real session id, full assistant message content, and
whether a tool call was recorded for that message
(`has_tool_events`). Files `run_20260814T2309*` and
`run_20260814T2310*` are Batch A (baseline); files
`run_20260814T2319*` and `run_20260814T2320*` are Batch B (reminder
experiment, reverted).

## Update: model-swap comparison

A second model, `gemma4:e2b`, was tested against the identical
question for comparison. Results (5 real runs, not included as
individual files in this packet -- see the main repo's `repro_runs/`
if needed):

| Model | tool_call_rate | correct_answer_rate | Failure mode |
|---|---|---|---|
| qwen3:14b | 1.0 | 0.0 | Calls the tool correctly, then fails during synthesis (generic non-answers, in one tested variant: hallucinated unrelated content) |
| gemma4:e2b | 0.0 | 0.0 | Never attempts the tool call; returns the identical, generic "I do not have access to your portfolio information" every time |

Both models fail completely on this task, but via genuinely different
mechanisms -- suggestive of a real, model-capability-level reliability
problem rather than a bug specific to one model or to the tool/prompt
infrastructure shared between them.
