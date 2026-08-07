# Transcript ingestion (experimental)

Ingests, chunks, redacts, and (optionally) summarizes transcript text.
Built and validated against a real transcript excerpt (see
`sample_data/README.md` for provenance), not synthetic data.

## Real, honest scope notes

- **No speaker diarization.** The real source data (a scraped YouTube
  transcript page) has no speaker labels, even for a two-person
  interview. `ingest.py` does not fabricate a `speaker` field.
- **Not meeting-specific.** Built against a podcast/interview transcript,
  not a Fireflies/Zoom business-meeting export. "Action items" and
  "decisions" framing was deliberately dropped from the summarizer
  prompt since it doesn't fit this kind of content -- it produces a
  factual summary and key topics instead.
- **Summarizer uses Herald's own already-validated `/api/v1/chat`
  endpoint**, not a new provider-agnostic adapter. No new LLM credential
  system was built.

## Pipeline

```
ingest_file(path) -> parse_transcript() -> chunk_segments() -> summarize_chunks()
```

1. `ingest.py` — parses the real messy format (timestamp + spelled-out
   duration + text, all concatenated with no separator; chapter markers
   as plain lines).
2. `chunker.py` — word-count-based chunking with overlap
   (`HERALD_TRANSCRIPT_CHUNK_WORDS`, default 500; overlap default 100).
3. `redact.py` — simple regex redaction (emails, long hex tokens).
4. `storage.py` — local JSON storage under `data/transcripts/<session_id>/`.
5. `summarizer.py` — real call to `/api/v1/chat`, requires
   `HERALD_TRANSCRIPT_SUMMARY_TOKEN` (a real chat-scoped API token).
   Fails closed (returns `None`) on missing token or any error.

## Real validation status

- Ingestion, chunking, redaction, storage: **fully validated** against
  the real sample transcript (63 real segments, 4 real chunks).
- Summarizer: **code correctness verified directly** (reaches the real
  endpoint with correct auth, correctly returns `None` on non-200
  responses) but a full clean AI-generated summary could not be
  demonstrated in this session -- the free model tier's rate limit was
  persistently exhausted (confirmed via multiple real retries at
  increasing wait intervals, including with a minimal test message that
  also failed, ruling out prompt size as the cause). See HOLD_NOTE.md.

## Running tests
```bash
cd experimental/connectors/transcripts
python3 -m pytest tests/ -v
```
