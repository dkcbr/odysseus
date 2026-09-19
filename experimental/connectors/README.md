# Experimental connectors

Real, tested receivers that translate external events into calls to
Jarvis's real `POST /v1/chat` endpoint (`routes/webhook/webhook_routes.py`).

## herald

Handles real GitHub `issues` and `issue_comment` webhook events. Verifies
the real `X-Hub-Signature-256` HMAC scheme, deduplicates via
`X-GitHub-Delivery`, and defaults to dry-run (returns the mapped payload,
does not forward) until explicitly disabled.

**Status**: built and tested locally (6/6 real tests passing, in
`tests/test_receiver.py`, using FastAPI's real `TestClient` -- no mocked
HTTP layer). Not yet deployed or exercised against a real live GitHub
webhook delivery, and not yet forwarded a real message through
`/v1/chat` end-to-end (would need a real, live `JARVIS_API_TOKEN`
scoped to "chat" to test that final hop safely).

Run tests:
```bash
cd experimental/connectors/herald
python3 -m pytest tests/test_receiver.py -v
```
