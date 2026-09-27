import asyncio
import json
import os

ZEUS_AGENT_URL = os.environ.get("ZEUS_AGENT_URL", "http://100.99.67.105:9001")


def _parse_params(content: str) -> dict:
    """Parse tool-call content into a params dict. Handles either
    convention this codebase might use: JSON-encoded args, or
    human-readable 'key: value' lines (one per line). Falls back to
    treating a bare single value as 'entity_id' for the common case of
    a single required parameter."""
    content = (content or "").strip()
    if not content:
        return {}
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    params = {}
    has_kv = False
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            params[key.strip()] = value.strip()
            has_kv = True
    if has_kv:
        return params
    return {"entity_id": content}


async def _post_to_agent(path: str, payload: dict) -> dict:
    """Shared httpx POST to the Zeus agent, same auth/timeout/error
    handling shape as ZeusTool -- fails closed, never a silent no-op."""
    token = os.environ.get("ZEUS_AGENT_TOKEN")
    if not token:
        return {
            "error": "zeus: ZEUS_AGENT_TOKEN is not configured. Do not attempt this again this turn -- tell the user the Zeus agent isn't reachable right now.",
            "exit_code": 1,
        }

    loop = asyncio.get_running_loop()
    try:
        import httpx

        def _post():
            return httpx.post(
                f"{ZEUS_AGENT_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=15,
            )

        resp = await asyncio.wait_for(loop.run_in_executor(None, _post), timeout=20)
    except asyncio.TimeoutError:
        return {"error": f"zeus: timed out calling {path}", "exit_code": 1}
    except Exception as e:
        return {"error": f"zeus: request failed for {path}: {type(e).__name__}: {e}", "exit_code": 1}

    if resp.status_code == 401:
        return {"error": "zeus: agent rejected the token (unauthorized)", "exit_code": 1}
    if resp.status_code == 403:
        return {"error": f"zeus: agent forbids this request: {resp.text}", "exit_code": 1}
    if resp.status_code == 400:
        return {"error": f"zeus: agent rejected the request body: {resp.text}", "exit_code": 1}
    if resp.status_code != 200:
        return {"error": f"zeus: agent returned unexpected status {resp.status_code}: {resp.text}", "exit_code": 1}

    data = resp.json()
    if not data.get("ok"):
        return {"error": f"ha: {data.get('error', 'unknown error')}", "exit_code": 1}
    return {"output": data.get("result"), "exit_code": 0}


class HAStateTool:
    """Read the current state of one allowlisted Home Assistant entity,
    via the real Zeus host agent's /ha_state endpoint (never talks to
    Home Assistant directly). Read-only -- cannot change anything.
    The Zeus agent enforces its own entity_id allowlist independent of
    whatever this tool is told to request."""

    async def execute(self, content: str, ctx: dict) -> dict:
        params = _parse_params(content)
        entity_id = params.get("entity_id")
        if not entity_id:
            return {"error": "ha_state: entity_id is required", "exit_code": 1}
        return await _post_to_agent("/ha_state", {"entity_id": entity_id})


class HAControlTool:
    """Call one allowlisted Home Assistant service (e.g. turn a light or
    switch on/off) against one allowlisted entity, via the real Zeus
    host agent's /ha_control endpoint. Mutating -- never available in
    plan mode. The Zeus agent enforces its own entity_id AND
    domain+service allowlists independent of whatever this tool is
    told to request, so an unexpected instruction cannot reach an
    entity or action that hasn't been explicitly allowed."""

    async def execute(self, content: str, ctx: dict) -> dict:
        params = _parse_params(content)
        entity_id = params.get("entity_id")
        domain = params.get("domain")
        service = params.get("service")
        data = params.get("data")

        if not entity_id or not domain or not service:
            return {
                "error": "ha_control: entity_id, domain, and service are all required",
                "exit_code": 1,
            }

        if isinstance(data, str) and data.strip():
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return {"error": f"ha_control: 'data' is not valid JSON: {data!r}", "exit_code": 1}
        elif not data:
            data = {}

        payload = {"entity_id": entity_id, "domain": domain, "service": service, "data": data}
        return await _post_to_agent("/ha_control", payload)
