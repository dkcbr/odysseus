"""Real, added 2026-10-01: direct tests for _tool_cap_for_model(), the
model-aware tool-count cap. Covers both the original xLAM case
(2026-09-25) and the newly-added qwen3:4b case (2026-10-01, found via a
direct, live investigation into the "read_systemd_logs reliability"
todo item: qwen3:4b falsely claimed no matching tool existed despite
read_systemd_logs being correctly present in its own 20-tool list,
while qwen3-14b-longctx, same request, same list, handled it
correctly)."""
import pytest

from src.agent_loop import _tool_cap_for_model, _SMALL_LOCAL_MODEL_TOOL_CAP


@pytest.mark.parametrize("model", [
    "llama-xlam-2-8b-fc-r",
    "robbiemu/Salesforce_Llama-xLAM-2:8b-fc-r-q5_K_M",
    "qwen3:4b",
])
def test_known_small_models_get_the_real_cap(model):
    assert _tool_cap_for_model(model) == _SMALL_LOCAL_MODEL_TOOL_CAP


@pytest.mark.parametrize("model", [
    "qwen3-14b-longctx:latest",
    "qwen3:14b",
    "nemotron-cascade-2:30b",
    "gpt-4o",
    "claude-sonnet-5",
])
def test_other_models_are_uncapped(model):
    assert _tool_cap_for_model(model) is None
