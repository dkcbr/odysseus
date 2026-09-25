"""Tests for ``core.atomic_io`` durability and crash-safety behavior.

``core.atomic_io`` provides ``atomic_write_json`` and ``atomic_write_text``.
Both write to a sibling ``.tmp.<pid>.<uuid4>`` file, ``fsync`` it, then
``os.replace`` into place so a crash mid-write leaves the previous good copy
untouched rather than a truncated/empty file. The uuid4 suffix (added
2026-09-02) makes the tmp path unique per *call*, not just per process --
see the concurrency section below for the real bug this closes.

These tests cover the happy path (round-trip, indent, parent-dir creation,
full overwrite, no leftover tmp), the two failure paths the implementation
guarantees (target file preserved when serialization fails before the
replace, and when ``os.replace`` itself fails), and a concurrency
regression test for a real, observed production bug.
"""
import importlib.util
import json
import threading
from pathlib import Path

import pytest

# Load core/atomic_io.py directly by file path so this stays a pure unit test:
# importing the ``core`` package would pull in core/__init__.py and the
# database/session modules, making the test depend on data/app.db existing.
ROOT = Path(__file__).resolve().parents[1]
ATOMIC_IO_PATH = ROOT / "core" / "atomic_io.py"
_spec = importlib.util.spec_from_file_location("_atomic_io_under_test", ATOMIC_IO_PATH)
atomic_io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atomic_io)

atomic_write_json = atomic_io.atomic_write_json
atomic_write_text = atomic_io.atomic_write_text


def _tmp_siblings(directory: Path, name: str) -> list:
    """Return any ``<name>.tmp.*`` files the helpers may have left behind."""
    return list(directory.glob(f"{name}.tmp.*"))


# ---------------------------------------------------------------------------
# atomic_write_json — happy path.
# ---------------------------------------------------------------------------
def test_atomic_write_json_round_trips_object(tmp_path):
    target = tmp_path / "data.json"
    original = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}, "s": "héllo"}

    atomic_write_json(str(target), original)

    assert json.loads(target.read_text(encoding="utf-8")) == original


def test_atomic_write_json_honors_indent(tmp_path):
    target = tmp_path / "indented.json"

    atomic_write_json(str(target), {"a": 1}, indent=2)

    text = target.read_text(encoding="utf-8")
    assert "\n" in text
    assert text == json.dumps({"a": 1}, indent=2)


def test_atomic_write_json_creates_missing_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "nested" / "data.json"

    atomic_write_json(str(target), {"ok": True})

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def test_atomic_write_json_fully_overwrites_longer_content(tmp_path):
    target = tmp_path / "data.json"
    atomic_write_json(str(target), {"k": "x" * 500})

    atomic_write_json(str(target), {"k": "short"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"k": "short"}
    # No trailing bytes from the previous, longer write.
    assert target.read_text(encoding="utf-8") == json.dumps({"k": "short"})


def test_atomic_write_json_leaves_no_tmp_file(tmp_path):
    target = tmp_path / "data.json"

    atomic_write_json(str(target), {"a": 1})

    assert _tmp_siblings(tmp_path, "data.json") == []


# ---------------------------------------------------------------------------
# atomic_write_json — failure path: target preserved on serialization error.
# ---------------------------------------------------------------------------
def test_atomic_write_json_preserves_target_when_serialization_fails(tmp_path):
    target = tmp_path / "data.json"
    atomic_write_json(str(target), {"existing": "value"})
    before = target.read_text(encoding="utf-8")

    # A set is not JSON-serializable, so json.dump raises after the tmp file
    # is opened but before os.replace runs.
    with pytest.raises(TypeError):
        atomic_write_json(str(target), {"bad": {1, 2, 3}})

    assert target.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# atomic_write_text — happy path.
# ---------------------------------------------------------------------------
def test_atomic_write_text_round_trips(tmp_path):
    target = tmp_path / "note.txt"
    text = "line one\nline two\nunicode: héllo\n"

    atomic_write_text(str(target), text)

    assert target.read_text(encoding="utf-8") == text


def test_atomic_write_text_creates_missing_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "nested" / "note.txt"

    atomic_write_text(str(target), "content")

    assert target.exists()
    assert target.read_text(encoding="utf-8") == "content"


def test_atomic_write_text_fully_overwrites_longer_content(tmp_path):
    target = tmp_path / "note.txt"
    atomic_write_text(str(target), "x" * 500)

    atomic_write_text(str(target), "short")

    assert target.read_text(encoding="utf-8") == "short"


def test_atomic_write_text_leaves_no_tmp_file(tmp_path):
    target = tmp_path / "note.txt"

    atomic_write_text(str(target), "content")

    assert _tmp_siblings(tmp_path, "note.txt") == []


def test_atomic_write_text_rejects_non_string_before_tmp_file(tmp_path):
    target = tmp_path / "note.txt"

    with pytest.raises(TypeError):
        atomic_write_text(str(target), 123)

    assert not target.exists()
    assert _tmp_siblings(tmp_path, "note.txt") == []


# ---------------------------------------------------------------------------
# atomic_write_text — failure path: target preserved when replace fails.
# ---------------------------------------------------------------------------
def test_atomic_write_text_preserves_target_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    atomic_write_text(str(target), "original content")
    before = target.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(atomic_io.os, "replace", boom)

    with pytest.raises(OSError):
        atomic_write_text(str(target), "new content that never lands")

    assert target.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Concurrency regression: real production bug, 2026-09-02.
#
# core/auth.py's _save_sessions() calls atomic_write_json concurrently from
# FastAPI's threadpool. When the tmp path was `{path}.tmp.{pid}` -- unique
# per process, not per call -- two threads sharing a pid raced on the same
# tmp file: one thread's os.replace() failed with ENOENT after the other's
# had already consumed the shared tmp file, and interleaved writes into the
# same tmp file produced a target that parsed as one complete JSON object
# followed by leftover trailing bytes (a real "Extra data" JSONDecodeError
# on next load). Both symptoms were observed live in production the same
# night this test was added.
# ---------------------------------------------------------------------------
def test_atomic_write_json_survives_concurrent_writers_same_process(tmp_path):
    """Many threads (same process, same pid -- the exact condition that
    broke the old per-pid tmp naming) hammer the same target path
    concurrently. Every writer's payload is large enough that an
    interleaved/truncated write would be obviously invalid JSON. After all
    threads finish: the target must contain exactly one writer's complete,
    valid payload (never a mix, never truncated, never trailing garbage),
    and no tmp siblings may be left behind."""
    target = tmp_path / "concurrent.json"
    n_threads = 16
    errors = []

    def writer(i):
        try:
            # Large, distinct, easy-to-validate payload per thread -- big
            # enough that a corrupt interleave would not coincidentally
            # parse as valid JSON.
            payload = {"writer": i, "padding": str(i) * 5000}
            atomic_write_json(str(target), payload)
        except Exception as e:  # pragma: no cover - surfaced via errors list
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"writer thread(s) raised: {errors}"

    # The critical assertion: whatever ended up on disk must be exactly one
    # complete, valid JSON object -- not a mix of two writers, not
    # truncated, no trailing bytes (json.loads is strict about trailing
    # content, so this alone would catch the original "Extra data" bug).
    text = target.read_text(encoding="utf-8")
    data = json.loads(text)
    assert set(data.keys()) == {"writer", "padding"}
    assert data["padding"] == str(data["writer"]) * 5000

    # No leftover tmp files from any of the 16 concurrent calls.
    assert _tmp_siblings(tmp_path, "concurrent.json") == []
