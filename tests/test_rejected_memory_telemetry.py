"""Real, added 2026-09-25: tests for durable telemetry on rejected
memory-add attempts (RejectedMemory table + _record_rejected_memory).

Supports the "measure recurrence" step of a staged hardening plan for
the inferred-pattern guard in ai_interaction.py: container logs alone
are ephemeral, so this is the durable, queryable record of what got
rejected, when, and why.
"""
import asyncio
import tempfile

import pytest

from src.ai_interaction import do_manage_memory, set_memory_manager
from src.database import SessionLocal, RejectedMemory
from src.memory import MemoryManager


@pytest.fixture
def isolated_memory_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = MemoryManager(tmpdir)
        set_memory_manager(mgr)
        yield mgr


def _cleanup(session_id):
    db = SessionLocal()
    try:
        db.query(RejectedMemory).filter(RejectedMemory.session_id == session_id).delete()
        db.commit()
    finally:
        db.close()


def test_rejected_memory_writes_a_durable_row(isolated_memory_manager):
    session_id = "test-telemetry-reject-row"
    try:
        result = asyncio.run(do_manage_memory(
            "add\nThe user targets latitude 40.7128, longitude -74.0060",
            session_id=session_id,
            owner="test-owner",
        ))
        assert "error" in result

        db = SessionLocal()
        try:
            rows = db.query(RejectedMemory).filter(RejectedMemory.session_id == session_id).all()
        finally:
            db.close()

        assert len(rows) == 1
        assert rows[0].text == "The user targets latitude 40.7128, longitude -74.0060"
        # Real, updated 2026-09-25: the coordinate regex was broadened
        # (stress-test follow-up) to also recognize explicit "latitude
        # X ... longitude Y" phrasing, so this exact text is now caught
        # by the coordinate check first (checked before the frequency
        # check) -- an equally correct reason for the same rejection.
        assert rows[0].reason == "raw coordinate pattern"
        assert rows[0].owner == "test-owner"
        assert rows[0].timestamp is not None
    finally:
        _cleanup(session_id)


def test_legitimate_save_writes_no_telemetry_row(isolated_memory_manager):
    session_id = "test-telemetry-legitimate-save"
    try:
        result = asyncio.run(do_manage_memory(
            "add\nUser prefers concise replies",
            session_id=session_id,
            owner="test-owner",
        ))
        assert "error" not in result

        db = SessionLocal()
        try:
            rows = db.query(RejectedMemory).filter(RejectedMemory.session_id == session_id).all()
        finally:
            db.close()

        assert len(rows) == 0
    finally:
        _cleanup(session_id)


def test_telemetry_failure_does_not_break_the_reject_response(monkeypatch):
    """A telemetry write failing must never turn a correct rejection
    into a hard error for the caller -- confirmed directly by forcing
    the real DB session constructor to raise. _record_rejected_memory
    does `from src.database import SessionLocal` as a LOCAL import
    inside the function, so patching the real src.database.SessionLocal
    (not a module-level attribute on ai_interaction) is what the lazy
    import will actually pick up at call time."""
    import src.database as database_module

    def _boom_session():
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(database_module, "SessionLocal", _boom_session)

    result = asyncio.run(do_manage_memory(
        "add\nThe user frequently uses gods_eye_view tools",
        session_id="test-telemetry-failure-resilience",
    ))
    # The rejection itself must still succeed cleanly despite the
    # telemetry write failing.
    assert "error" in result
    assert "inferred pattern" in result["error"]
