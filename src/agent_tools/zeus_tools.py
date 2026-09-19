import asyncio
import os

ZEUS_AGENT_URL = os.environ.get("ZEUS_AGENT_URL", "http://100.99.67.105:9001")
ALLOWED_ACTIONS = frozenset({"uname", "uptime", "df", "health", "ps", "journal_tail"})


class ZeusTool:
    """Call an allowlisted, read-only action on the Zeus host agent
    (zeus_agent.py, running directly as a real, live process on Zeus --
    confirmed via a direct HTTP request before this tool was wired in).
    Fails closed: no token configured, disallowed action, or any
    request error returns a clear error -- never a silent no-op."""

    async def execute(self, content: str, ctx: dict) -> dict:
        action = content.strip().split("\n")[0].strip() or "uname"

        if action not in ALLOWED_ACTIONS:
            return {
                "error": f"zeus: '{action}' is not an allowed action. Allowed: {', '.join(sorted(ALLOWED_ACTIONS))}",
                "exit_code": 1,
            }

        # Real, deliberate exception: "health" is checked before the token
        # requirement below and uses a plain, unauthenticated GET -- its
        # entire purpose is to distinguish "the agent process is down" from
        # "my token is missing/wrong", which the normal POST+token path
        # below cannot do (it would report an auth failure either way).
        if action == "health":
            loop = asyncio.get_running_loop()
            try:
                import httpx

                def _get():
                    return httpx.get(f"{ZEUS_AGENT_URL}/health", timeout=10)

                resp = await asyncio.wait_for(loop.run_in_executor(None, _get), timeout=15)
            except asyncio.TimeoutError:
                return {"error": "zeus: health check timed out -- agent is likely down or unreachable", "exit_code": 1}
            except Exception as e:
                return {"error": f"zeus: health check failed: {type(e).__name__}: {e}", "exit_code": 1}
            if resp.status_code != 200:
                return {"error": f"zeus: health check returned unexpected status {resp.status_code}", "exit_code": 1}
            return {"output": resp.json().get("result"), "exit_code": 0}

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
                    f"{ZEUS_AGENT_URL}/{action}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15,
                )

            resp = await asyncio.wait_for(loop.run_in_executor(None, _post), timeout=20)
        except asyncio.TimeoutError:
            return {"error": f"zeus: timed out calling {action}", "exit_code": 1}
        except Exception as e:
            return {"error": f"zeus: request failed for {action}: {type(e).__name__}: {e}", "exit_code": 1}

        if resp.status_code == 401:
            return {"error": "zeus: agent rejected the token (unauthorized)", "exit_code": 1}
        if resp.status_code == 403:
            return {"error": f"zeus: agent forbids action '{action}'", "exit_code": 1}
        if resp.status_code != 200:
            return {"error": f"zeus: agent returned unexpected status {resp.status_code}: {resp.text}", "exit_code": 1}

        data = resp.json()
        if not data.get("ok"):
            return {"error": f"zeus: {action} failed: {data.get('error', 'unknown error')}", "exit_code": 1}
        return {"output": data.get("result"), "exit_code": 0}
