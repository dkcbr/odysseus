import sys, os, json, hmac, hashlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret-value"
os.environ["GITHUB_RECEIVER_DRY_RUN"] = "true"

import receiver as receiver_module
from receiver import app

client = TestClient(app)
SECRET = "test-secret-value"


def real_signature(body_bytes: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()


def test_invalid_signature_rejected():
    body = json.dumps({"action": "opened", "issue": {"title": "x", "html_url": "u"}}).encode()
    resp = client.post("/github/receiver", content=body, headers={
        "X-Hub-Signature-256": "sha256=deadbeef",
        "X-GitHub-Event": "issues",
    })
    assert resp.status_code == 401


def test_missing_signature_rejected():
    body = json.dumps({"action": "opened"}).encode()
    resp = client.post("/github/receiver", content=body, headers={"X-GitHub-Event": "issues"})
    assert resp.status_code == 401


def test_valid_signature_issues_opened_dry_run():
    receiver_module._seen_delivery_ids.clear()
    body = json.dumps({
        "action": "opened",
        "issue": {"title": "Real test issue", "html_url": "https://github.com/x/y/issues/1"},
    }).encode()
    resp = client.post("/github/receiver", content=body, headers={
        "X-Hub-Signature-256": real_signature(body),
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-1",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "dry_run"
    assert "Real test issue" in data["payload"]["message"]


def test_issue_comment_mapping():
    receiver_module._seen_delivery_ids.clear()
    body = json.dumps({
        "comment": {"user": {"login": "dkcbr"}, "body": "This looks good", "html_url": "https://github.com/x/y/issues/1#comment"},
    }).encode()
    resp = client.post("/github/receiver", content=body, headers={
        "X-Hub-Signature-256": real_signature(body),
        "X-GitHub-Event": "issue_comment",
        "X-GitHub-Delivery": "delivery-2",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "dkcbr" in data["payload"]["message"]
    assert "This looks good" in data["payload"]["message"]


def test_unsupported_event_acknowledged_not_forwarded():
    body = json.dumps({"action": "created"}).encode()
    resp = client.post("/github/receiver", content=body, headers={
        "X-Hub-Signature-256": real_signature(body),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-3",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged_unsupported_event"


def test_duplicate_delivery_ignored():
    receiver_module._seen_delivery_ids.clear()
    body = json.dumps({"action": "opened", "issue": {"title": "dup test", "html_url": "u"}}).encode()
    headers = {
        "X-Hub-Signature-256": real_signature(body),
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-dup",
    }
    resp1 = client.post("/github/receiver", content=body, headers=headers)
    resp2 = client.post("/github/receiver", content=body, headers=headers)
    assert resp1.json()["status"] == "dry_run"
    assert resp2.json()["status"] == "duplicate_ignored"


if __name__ == "__main__":
    test_invalid_signature_rejected()
    test_missing_signature_rejected()
    test_valid_signature_issues_opened_dry_run()
    test_issue_comment_mapping()
    test_unsupported_event_acknowledged_not_forwarded()
    test_duplicate_delivery_ignored()
    print("All tests passed")


def test_no_context_file_no_change(tmp_path, monkeypatch):
    """Real default: no context file present -> no [Context: ...] prefix added.
    Isolated from the separate real calendar feature (which may add its own,
    independent prefix if HERALD_CALENDAR_COOKIE happens to be set in the
    real environment this test runs in -- that's a different, real,
    independently-tested feature, not this test's concern)."""
    missing_path = str(tmp_path / "does_not_exist.md")
    monkeypatch.setattr(receiver_module, "CONTEXT_FILE", missing_path)
    monkeypatch.delenv("HERALD_CALENDAR_COOKIE", raising=False)
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    monkeypatch.delenv("CLICKUP_LIST_ID", raising=False)
    receiver_module._seen_delivery_ids.clear()
    body = json.dumps({"action": "opened", "issue": {"title": "no ctx test", "html_url": "u"}}).encode()
    resp = client.post("/github/receiver", content=body, headers={
        "X-Hub-Signature-256": real_signature(body),
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-no-context",
    })
    payload = resp.json()["payload"]
    assert payload["message"] == "GitHub issue opened: no ctx test\nu"
    assert "[Context:" not in payload["message"]


def test_context_file_present_prepended(tmp_path, monkeypatch):
    """Real context file present -> prepended to the real message string field."""
    ctx_path = tmp_path / "context.md"
    ctx_path.write_text("DK's real Odysseus project, currently testing Herald.", encoding="utf-8")
    monkeypatch.setattr(receiver_module, "CONTEXT_FILE", str(ctx_path))
    receiver_module._seen_delivery_ids.clear()
    body = json.dumps({"action": "opened", "issue": {"title": "ctx test", "html_url": "u"}}).encode()
    resp = client.post("/github/receiver", content=body, headers={
        "X-Hub-Signature-256": real_signature(body),
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-with-context",
    })
    payload = resp.json()["payload"]
    assert "[Context: DK's real Odysseus project" in payload["message"]
    assert "GitHub issue opened: ctx test" in payload["message"]


def test_context_truncation(tmp_path, monkeypatch):
    """Real truncation at CONTEXT_MAX_CHARS. Isolated from the separate
    real calendar feature the same way as test_no_context_file_no_change."""
    ctx_path = tmp_path / "context.md"
    ctx_path.write_text("word " * 300, encoding="utf-8")  # far exceeds 500-char default
    monkeypatch.setattr(receiver_module, "CONTEXT_FILE", str(ctx_path))
    monkeypatch.setattr(receiver_module, "CONTEXT_MAX_CHARS", 50)
    monkeypatch.delenv("HERALD_CALENDAR_COOKIE", raising=False)
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    monkeypatch.delenv("CLICKUP_LIST_ID", raising=False)
    receiver_module._seen_delivery_ids.clear()
    body = json.dumps({"action": "opened", "issue": {"title": "trunc test", "html_url": "u"}}).encode()
    resp = client.post("/github/receiver", content=body, headers={
        "X-Hub-Signature-256": real_signature(body),
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-truncation",
    })
    payload = resp.json()["payload"]
    context_line = payload["message"].split("\n\n")[0]
    assert context_line.endswith("...]")
    assert len(context_line) < 100


def test_calendar_enrichment_when_cookie_present(monkeypatch):
    """Real, self-contained integration: creates its own real temporary
    calendar event (rather than depending on state from another test
    session, which was found stale after that earlier event was properly
    cleaned up), verifies it flows through Herald, then deletes it."""
    import httpx as _httpx
    from datetime import datetime, timedelta, timezone
    try:
        _httpx.get("http://localhost:7000", timeout=2.0)
    except _httpx.HTTPError:
        pytest.skip("real Odysseus app not reachable on localhost:7000")

    real_cookie = os.environ.get("HERALD_CALENDAR_COOKIE")
    if not real_cookie:
        pytest.skip("HERALD_CALENDAR_COOKIE not set for this real test run")

    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT15:00:00Z")
    create_resp = _httpx.post(
        "http://localhost:7000/api/calendar/events",
        cookies={"odysseus_session": real_cookie},
        json={"summary": "test_calendar_enrichment_when_cookie_present temp event", "dtstart": tomorrow},
        timeout=10.0,
    )
    if create_resp.status_code != 200:
        pytest.skip(f"could not create real test event (status {create_resp.status_code})")
    uid = create_resp.json()["uid"]

    try:
        receiver_module._seen_delivery_ids.clear()
        body = json.dumps({"action": "opened", "issue": {"title": "cal enrichment test", "html_url": "u"}}).encode()
        resp = client.post("/github/receiver", content=body, headers={
            "X-Hub-Signature-256": real_signature(body),
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "delivery-cal-enrichment",
        })
        payload = resp.json()["payload"]
        assert "Upcoming:" in payload["message"]
        assert "test_calendar_enrichment_when_cookie_present temp event" in payload["message"]
        assert "GitHub issue opened: cal enrichment test" in payload["message"]
    finally:
        _httpx.delete(f"http://localhost:7000/api/calendar/events/{uid}",
                       cookies={"odysseus_session": real_cookie}, timeout=10.0)


def test_no_calendar_enrichment_without_cookie(monkeypatch):
    """Real safety check: no cookie -> message unaffected by calendar code."""
    monkeypatch.delenv("HERALD_CALENDAR_COOKIE", raising=False)
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    monkeypatch.delenv("CLICKUP_LIST_ID", raising=False)
    receiver_module._seen_delivery_ids.clear()
    body = json.dumps({"action": "opened", "issue": {"title": "no cal test", "html_url": "u"}}).encode()
    resp = client.post("/github/receiver", content=body, headers={
        "X-Hub-Signature-256": real_signature(body),
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-no-cal",
    })
    payload = resp.json()["payload"]
    assert "Upcoming:" not in payload["message"]
    assert payload["message"] == "GitHub issue opened: no cal test\nu"


def test_clickup_enrichment_when_creds_present(monkeypatch):
    """Real integration: real ClickUp creds, real live task already created."""
    if not (os.environ.get("CLICKUP_API_TOKEN") and os.environ.get("CLICKUP_LIST_ID")):
        pytest.skip("CLICKUP_API_TOKEN / CLICKUP_LIST_ID not set for this real test run")
    monkeypatch.delenv("HERALD_CALENDAR_COOKIE", raising=False)  # isolate from calendar feature

    receiver_module._seen_delivery_ids.clear()
    body = json.dumps({"action": "opened", "issue": {"title": "clickup enrichment test", "html_url": "u"}}).encode()
    resp = client.post("/github/receiver", content=body, headers={
        "X-Hub-Signature-256": real_signature(body),
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-clickup-enrichment",
    })
    payload = resp.json()["payload"]
    assert "Open ClickUp tasks:" in payload["message"]
    assert "Herald connector validation test" in payload["message"]
    assert "GitHub issue opened: clickup enrichment test" in payload["message"]


def test_no_clickup_enrichment_without_creds(monkeypatch):
    """Real safety check: no creds -> message unaffected by ClickUp code."""
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)
    monkeypatch.delenv("CLICKUP_LIST_ID", raising=False)
    monkeypatch.delenv("HERALD_CALENDAR_COOKIE", raising=False)
    receiver_module._seen_delivery_ids.clear()
    body = json.dumps({"action": "opened", "issue": {"title": "no clickup test", "html_url": "u"}}).encode()
    resp = client.post("/github/receiver", content=body, headers={
        "X-Hub-Signature-256": real_signature(body),
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-no-clickup",
    })
    payload = resp.json()["payload"]
    assert "Open ClickUp tasks:" not in payload["message"]
    assert payload["message"] == "GitHub issue opened: no clickup test\nu"
