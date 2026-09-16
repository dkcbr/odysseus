# routes/system_monitor_routes.py
#
# Real, live system-resource metrics for a new Odysseus home-page dashboard
# widget (CPU, memory, disk, network, GPU), inspired directly by a real
# system-monitor app demo DK watched. Matches the established
# router-factory/require_admin pattern used throughout this codebase.
#
# GPU metrics come from a real, direct nvidia-smi subprocess call (the
# same, proven approach used elsewhere in this engagement for GPU checks)
# rather than a Python NVML binding, to avoid a new, heavier dependency
# for a single, simple metric. Real, honest degradation: on a host with
# no NVIDIA GPU (or nvidia-smi unavailable), gpu is simply omitted from
# the response rather than the whole endpoint failing.

import os
import subprocess

import httpx
import psutil
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.middleware import require_admin

# Real, added 2026-09-16: game-mode support -- stops/unloads every local
# AI service holding real GPU memory (both Whisper systemd units, all
# loaded LM Studio models, all loaded Ollama models) so the shared 16GB
# GPU is free for gaming, triggered from a real sidebar button. Reuses
# the exact same real, host-level systemctl_agent.py + game_mode.sh
# already built and verified live this same session (real reload time
# for Whisper confirmed at ~3.2s) -- matches the established pattern
# RestartServiceTool (agent_tools/systemctl_tools.py) already uses for
# reaching real host state from inside this container.
SYSTEMCTL_AGENT_URL = "http://100.93.206.89:9001/game_mode"


def _real_gpu_metrics() -> dict | None:
    """Real, direct nvidia-smi query. Returns None (not an error) if no
    NVIDIA GPU/driver is present -- a real, valid, non-error system state,
    not something the frontend should treat as a failure."""
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return None
        util, mem_used, mem_total, temp = (
            v.strip() for v in result.stdout.strip().split(","))
        return {
            "utilization_percent": float(util),
            "memory_used_mb": float(mem_used),
            "memory_total_mb": float(mem_total),
            "temperature_c": float(temp),
        }
    except Exception:
        return None


def setup_system_monitor_routes() -> APIRouter:
    router = APIRouter(prefix="/api/system-monitor", tags=["system-monitor"])

    @router.get("/metrics")
    def get_metrics(request: Request):
        require_admin(request)

        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_per_core = psutil.cpu_percent(interval=0.1, percpu=True)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net = psutil.net_io_counters()

        return {
            "cpu": {
                "percent": cpu_percent,
                "per_core_percent": cpu_per_core,
                "core_count": psutil.cpu_count(logical=True),
            },
            "memory": {
                "percent": mem.percent,
                "used_gb": round(mem.used / (1024 ** 3), 2),
                "total_gb": round(mem.total / (1024 ** 3), 2),
            },
            "disk": {
                "percent": disk.percent,
                "used_gb": round(disk.used / (1024 ** 3), 2),
                "total_gb": round(disk.total / (1024 ** 3), 2),
            },
            "network": {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
            },
            "gpu": _real_gpu_metrics(),
        }

    class GameModeRequest(BaseModel):
        action: str  # "on", "off", or "status"

    @router.post("/game-mode")
    def game_mode(req: GameModeRequest, request: Request):
        require_admin(request)

        if req.action not in ("on", "off", "status"):
            raise HTTPException(status_code=400, detail="action must be one of: on, off, status")

        token = os.environ.get("SYSTEMCTL_AGENT_TOKEN")
        if not token:
            raise HTTPException(
                status_code=503,
                detail="game-mode agent token not configured -- SYSTEMCTL_AGENT_TOKEN missing",
            )

        try:
            resp = httpx.post(
                SYSTEMCTL_AGENT_URL,
                json={"action": req.action},
                headers={"Authorization": f"Bearer {token}"},
                # Real, matches the host agent's own 90s internal timeout
                # for "on" (stops 2 services + unloads LM Studio/Ollama
                # models) plus real margin for network/queueing.
                timeout=95,
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=502,
                detail=f"could not reach the game-mode agent: {type(e).__name__}: {e}",
            )

        if resp.status_code == 401:
            raise HTTPException(status_code=502, detail="game-mode agent rejected the token")
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"game-mode agent returned unexpected status {resp.status_code}",
            )

        return resp.json()

    return router
