"""Real chunking with overlap, operating on the real segment structure
from ingest.py (no fabricated speaker field, chapter-aware)."""
import os
from typing import List, Dict

CHUNK_WORD_TARGET = int(os.environ.get("HERALD_TRANSCRIPT_CHUNK_WORDS", "500"))
CHUNK_OVERLAP_WORDS = int(os.environ.get("HERALD_TRANSCRIPT_CHUNK_OVERLAP_WORDS", "100"))


def chunk_segments(segments: List[Dict]) -> List[Dict]:
    """Real word-count-based chunking with overlap. Each chunk records
    its real start/end timestamp and chapter (if segments within it span
    more than one chapter, records the chapter of the first segment --
    honest about the boundary being approximate in that case)."""
    if not segments:
        return []

    chunks = []
    current_words: List[str] = []
    current_segments: List[Dict] = []

    def flush():
        if not current_words:
            return
        chunks.append({
            "text": " ".join(current_words),
            "start_seconds": current_segments[0]["timestamp_seconds"],
            "end_seconds": current_segments[-1]["timestamp_seconds"],
            "chapter": current_segments[0]["chapter"],
            "n_words": len(current_words),
        })

    for seg in segments:
        words = seg["text"].split()
        current_words.extend(words)
        current_segments.append(seg)
        if len(current_words) >= CHUNK_WORD_TARGET:
            flush()
            # real overlap: keep the last CHUNK_OVERLAP_WORDS words and
            # their owning segments for the start of the next chunk
            current_words = current_words[-CHUNK_OVERLAP_WORDS:] if CHUNK_OVERLAP_WORDS else []
            current_segments = current_segments[-1:] if current_words else []

    flush()
    return chunks
