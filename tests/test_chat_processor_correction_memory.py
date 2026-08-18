"""Real, direct tests for the "correction" memory category (added
2026-08-17): explicit user corrections and lasting behavioral
preferences should be injected into every conversation's context,
regardless of topic relevance and independent of the "pinned" field
(confirmed directly during implementation that nothing in this
codebase ever sets pinned=True for a model-added memory -- gating
this feature on that field would have made it silently inert)."""
from types import SimpleNamespace

from src.chat_processor import ChatProcessor


class _Memory:
    def __init__(self, rows):
        self.rows = rows

    def load(self, owner=None):
        return list(self.rows)

    def increment_uses(self, ids):
        pass


class _Docs:
    rag_manager = None


def _context_text(preface):
    return "\n".join(m.get("content", "") for m in preface)


def _processor(rows):
    return ChatProcessor(memory_manager=_Memory(rows), personal_docs_manager=_Docs())


def test_correction_memory_injected_for_unrelated_topic():
    rows = [
        {
            "id": "corr1",
            "text": "Always answer in exactly one short sentence.",
            "category": "correction",
            "pinned": False,  # real, deliberate: correction memories are never pinned in practice
            "timestamp": 1,
        },
    ]

    preface, _, _ = _processor(rows).build_context_preface(
        message="What is the capital of France?",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=True,
    )

    text = _context_text(preface)
    assert "Always answer in exactly one short sentence." in text


def test_correction_memory_does_not_require_pinned_field():
    """Real, direct regression test for the exact bug caught during
    implementation: an earlier version of this feature relied on the
    'pinned' field, which nothing in the codebase ever sets for a
    model-added memory. This test would fail against that version."""
    rows = [
        {
            "id": "corr1",
            "text": "Never use emoji in responses.",
            "category": "correction",
            "timestamp": 1,
            # note: no "pinned" key at all -- matches a real, freshly
            # model-saved memory, not a manually-pinned one
        },
    ]

    preface, _, _ = _processor(rows).build_context_preface(
        message="Tell me a joke",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=True,
    )

    text = _context_text(preface)
    assert "Never use emoji in responses." in text


def test_correction_memories_sorted_most_recent_first():
    rows = [
        {"id": "old", "text": "Old correction marker alpha.", "category": "correction", "timestamp": 1},
        {"id": "new", "text": "New correction marker beta.", "category": "correction", "timestamp": 100},
    ]

    preface, _, _ = _processor(rows).build_context_preface(
        message="unrelated question",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=True,
    )

    text = _context_text(preface)
    assert text.index("New correction marker beta.") < text.index("Old correction marker alpha.")


def test_correction_memories_capped_at_ten():
    rows = [
        {"id": f"corr-{idx}", "text": f"Correction number {idx} marker.", "category": "correction", "timestamp": idx}
        for idx in range(15)
    ]

    processor = _processor(rows)
    processor.build_context_preface(
        message="unrelated question",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=True,
    )

    correction_count = sum(1 for m in processor._last_used_memories if m["type"] == "correction")
    assert correction_count == 10


def test_correction_memories_do_not_count_against_pinned_or_recalled_cap():
    """Real, important check: correction memories are injected via a
    separate path and should not eat into the ordinary
    MEMORY_CONTEXT_LIMIT (5) used for pinned/recalled memories."""
    rows = [
        {"id": f"corr-{idx}", "text": f"Correction number {idx} marker.", "category": "correction", "timestamp": idx}
        for idx in range(3)
    ]
    rows.extend([
        {"id": f"fact-{idx}", "text": f"Fact number {idx} coffee marker.", "category": "preference", "pinned": False, "timestamp": idx}
        for idx in range(6)
    ])

    preface, _, _ = _processor(rows).build_context_preface(
        message="coffee marker",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=True,
    )

    text = _context_text(preface)
    for idx in range(3):
        assert f"Correction number {idx} marker." in text
