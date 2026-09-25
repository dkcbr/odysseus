"""
Real integration tests against the actual live ClickUp API -- not mocked,
matching the same discipline used for calendar_client.py. Requires real
CLICKUP_API_TOKEN and CLICKUP_LIST_ID env vars to exercise the live
paths; those specific tests skip cleanly if unset rather than failing.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from clickup_client import fetch_open_tasks, build_task_summary

requires_real_creds = pytest.mark.skipif(
    not (os.environ.get("CLICKUP_API_TOKEN") and os.environ.get("CLICKUP_LIST_ID")),
    reason="CLICKUP_API_TOKEN / CLICKUP_LIST_ID not set for this real test run",
)


def test_no_token_skips(monkeypatch):
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    monkeypatch.setenv("CLICKUP_LIST_ID", "some-list")
    assert fetch_open_tasks() is None


def test_no_list_id_skips(monkeypatch):
    monkeypatch.setenv("CLICKUP_API_TOKEN", "fake-token")
    monkeypatch.delenv("CLICKUP_LIST_ID", raising=False)
    assert fetch_open_tasks() is None


def test_invalid_token_fails_closed_real(monkeypatch):
    """Real test against the real ClickUp API with a garbage token."""
    monkeypatch.setenv("CLICKUP_API_TOKEN", "definitely-not-a-real-clickup-token")
    monkeypatch.setenv("CLICKUP_LIST_ID", "901418883702")
    assert fetch_open_tasks() is None


@requires_real_creds
def test_valid_creds_return_real_tasks():
    """Real test against the real workspace with the real test task
    created for this validation (id 86bb9yw14, 'Herald connector
    validation test')."""
    tasks = fetch_open_tasks()
    assert tasks is not None
    names = [t["name"] for t in tasks]
    assert "Herald connector validation test" in names


def test_build_summary_empty_list():
    assert build_task_summary([]) is None


def test_build_summary_real_field_names():
    """Uses the exact real task shape confirmed from the actual API response."""
    tasks = [{"id": "86bb9yw14", "name": "Herald connector validation test",
              "status": {"status": "to do", "type": "open"}}]
    summary = build_task_summary(tasks)
    assert summary is not None
    assert "Herald connector validation test (to do)" in summary


def test_build_summary_excludes_closed_handled_upstream():
    """fetch_open_tasks filters closed tasks; build_task_summary itself
    just formats whatever list it's given -- verify that formatting
    doesn't special-case status type itself (filtering is fetch's job)."""
    tasks = [{"name": "Done task", "status": {"status": "complete", "type": "closed"}}]
    summary = build_task_summary(tasks)
    assert "Done task (complete)" in summary  # formatting doesn't filter; fetch does


def test_build_summary_truncation(monkeypatch):
    monkeypatch.setattr("clickup_client.SUMMARY_MAX_CHARS", 30)
    tasks = [{"name": "A" * 100, "status": {"status": "to do"}}]
    summary = build_task_summary(tasks)
    assert len(summary) <= 34
    assert summary.endswith("...")
