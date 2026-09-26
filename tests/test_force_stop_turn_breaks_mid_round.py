"""Real, added 2026-09-26: regression test for the real, root-cause bug
behind qwen2.5:7b's confirmed-broken force-stop-turn mechanism.

Both _ody_force_stop_turn set-sites in stream_agent_loop live inside
the for-loop over a single round's tool_blocks, but the only check for
it used to sit AFTER that loop -- so setting the flag partway through
a round's own batch never stopped the REST of that same round's blocks
from still executing; it only prevented a next round from starting.
This is exactly why real, live testing kept showing 11-16+ tool calls
despite the flag correctly being set: qwen2.5:7b's real over-cap
batches arrived as many tool_blocks within a single round.

This test exercises the mechanism via the consecutive-failures trigger
specifically (_ODY_MAX_CONSECUTIVE_FAILURES=5) rather than the
per-tool-type cap trigger, since _ody_CAPPED_TOOLS is a local variable
inside stream_agent_loop and not easily overridable from a test -- but
both triggers set the exact same _ody_force_stop_turn flag and share
the exact same (fixed) break check, so this is a genuine, direct test
of the real fix regardless of which trigger sets the flag. Simulates 6
tool_blocks in one round, all of which fail (a disabled tool), and
confirms the loop stops after exactly 5 (matching the real, exact
threshold) rather than processing the full batch of 6.
"""
import json

import pytest

import src.agent_loop as agent_loop


@pytest.mark.asyncio
async def test_consecutive_failures_break_mid_round_not_after_full_batch(monkeypatch):
    async def fake_stream(*args, **kwargs):
        # bash is disabled by policy in this test context, so every one
        # of these 6 calls fails -- a real, simple way to drive
        # _ody_consecutive_failures up without needing a real tool.
        parts = [f"```bash\necho call_{i}\n```" for i in range(1, 7)]
        content = "\n\n".join(parts)
        yield f'data: {json.dumps({"delta": content})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    tool_output_events = []
    saw_stop_message = False
    async for chunk in agent_loop.stream_agent_loop(
        endpoint_url="http://fake/v1/chat/completions",
        model="fake-model",
        messages=[{"role": "user", "content": "test"}],
        max_rounds=2,
        relevant_tools={"bash"},
    ):
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                data = json.loads(chunk[6:])
            except Exception:
                continue
            if data.get("type") == "tool_output":
                tool_output_events.append(data)
            delta = data.get("delta", "")
            if isinstance(delta, str) and "Stopped: reached the per-turn limit" in delta:
                saw_stop_message = True

    # The real, core assertion: the loop must NOT process all 6 blocks
    # in this single round -- that was the exact, confirmed bug. Exactly
    # 5 (matching the real _ODY_MAX_CONSECUTIVE_FAILURES=5 threshold)
    # confirms the mid-round break fires at precisely the right point,
    # not just eventually.
    assert len(tool_output_events) == 5, (
        f"Expected exactly 5 tool attempts before the consecutive-failure "
        f"threshold force-stops the round, got {len(tool_output_events)} -- "
        f"before this fix, all 6 would have been processed."
    )
    assert saw_stop_message, "Expected the real, user-facing stop message to appear."
