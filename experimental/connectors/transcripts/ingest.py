"""
Real transcript ingestion for Herald.

Parses the actual, messy scraped-YouTube-transcript-page format
encountered with real data this session: each real content line has a
compact timestamp (M:SS) immediately followed by a redundant spelled-out
duration ("7 seconds", "1 minute, 8 seconds", etc.) with NO separator,
then the actual spoken text, also with no separator. Chapter markers are
plain "Chapter N: Title" lines.

Honest design note: this format has NO real speaker diarization at all,
even for a two-person interview. An earlier proposal's canonical schema
assumed a 'speaker' field -- that field is deliberately omitted here
rather than fabricated, since the real source data doesn't contain it.
"""
import re
import json
import os
from datetime import datetime, timezone
from typing import List, Dict, Optional

# Matches: "1:08" + "1 minute, 8 seconds" (or "7 seconds", "1 minute", etc.) + text
# Group 1: minutes, Group 2: seconds (the compact M:SS), Group 3: the text after
# the redundant spelled-out duration is discarded (not captured as separate group).
LINE_PATTERN = re.compile(
    r"^(\d+):(\d{2})"
    r"(?:\d+ (?:minutes?|seconds?)(?:, \d+ seconds?)?)"
    r"(.*)$"
)
CHAPTER_PATTERN = re.compile(r"^Chapter \d+:\s*(.+)$")


def parse_transcript(raw_text: str) -> Dict:
    """Real parser for the real format. Returns canonical structure with
    chapters and segments -- no fabricated speaker field."""
    segments = []
    current_chapter = None
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        chapter_match = CHAPTER_PATTERN.match(line)
        if chapter_match:
            current_chapter = chapter_match.group(1)
            continue
        line_match = LINE_PATTERN.match(line)
        if not line_match:
            # Real, honest handling: a line that doesn't match either
            # pattern is kept as continuation text of the previous
            # segment rather than silently dropped -- this happens for
            # genuinely multi-line spoken segments in the real data.
            if segments:
                segments[-1]["text"] += " " + line
            continue
        minutes, seconds, text = line_match.groups()
        total_seconds = int(minutes) * 60 + int(seconds)
        segments.append({
            "chapter": current_chapter,
            "timestamp_seconds": total_seconds,
            "text": text.strip(),
        })
    return {"segments": segments}


def ingest_file(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    parsed = parse_transcript(raw)
    parsed["source_file"] = os.path.basename(path)
    parsed["ingested_at"] = datetime.now(timezone.utc).isoformat()
    return parsed
