"""Atomic JSON file writes.

Use this everywhere a JSON config file is persisted. A plain `open("w") +
json.dump` truncates the file on first write and only fills it with new
content afterwards — a kill -9 / power loss / OOM in between produces a
truncated or empty file. For password DBs (`auth.json`) and live state
(`sessions.json`, `settings.json`, `integrations.json`, `cookbook_state.json`),
that's a data-loss event.

`atomic_write_json` writes to a sibling tmp file, fsyncs, then `os.replace`s
into place. On POSIX `os.replace` is atomic on the same filesystem.

FIXED 2026-09-02: the tmp filename used to be `{path}.tmp.{os.getpid()}` --
unique per process, but Python threads share a PID, and several real callers
(core/auth.py's `_save_sessions`, run from FastAPI's threadpool) call this
concurrently from multiple threads in the same process. Two threads racing
on the identical tmp path both opened it in "w" mode (each truncating
whatever the other had just written) and both wrote their own full content
into it; whichever thread's shorter write landed after the other's longer
one left a file that parsed as one complete, valid JSON object immediately
followed by leftover trailing bytes -- confirmed live in production as a
real "Extra data" JSONDecodeError on the next load. Separately, whichever
thread's `os.replace()` ran second failed with ENOENT, because the first
thread's replace had already consumed the shared tmp file -- confirmed live
as a real "No such file or directory: ...tmp.1 -> sessions.json" error.
Both symptoms trace to the same root cause: the tmp path was unique per
*process*, not per *call*. Adding a uuid4 suffix makes every call's tmp path
unique regardless of which thread or process it runs in, so concurrent
callers can no longer share -- and therefore can no longer corrupt or race
on -- the same tmp file. See `core/auth.py`'s `_save_sessions` for the
matching fix that also widens its lock to cover the write itself, not just
the in-memory snapshot.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Optional


def _unique_tmp_path(path: str) -> str:
    """A tmp path unique to this specific call. Includes the pid for the
    same debuggability the old name had (which process wrote this), plus a
    uuid4 suffix so two calls -- even concurrent threads sharing a pid --
    never collide on the same tmp file."""
    return f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"


def atomic_write_json(path: str, data: Any, *, indent: Optional[int] = None) -> None:
    """Atomically persist `data` as JSON at `path`.

    The tmp file name is unique per call, so concurrent callers -- even
    multiple threads in the same process -- never share a tmp path and can
    never race on the same file or on whose `os.replace()` runs first.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = _unique_tmp_path(path)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_text(path: str, text: str) -> None:
    if not isinstance(text, str):
        raise TypeError("atomic_write_text expects a string")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = _unique_tmp_path(path)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
