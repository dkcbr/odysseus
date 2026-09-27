"""Real local storage for ingested transcripts, under data/transcripts/.
Fully local, never committed (see .gitignore note in README)."""
import json
import os

STORAGE_DIR = os.environ.get(
    "HERALD_TRANSCRIPT_STORAGE_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "transcripts"),
)


def save_session(session_id: str, parsed: dict, chunks: list) -> str:
    session_dir = os.path.join(STORAGE_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    with open(os.path.join(session_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in parsed.items() if k != "segments"}, f, indent=2)
    with open(os.path.join(session_dir, "chunks.json"), "w", encoding="utf-8") as f:
        json.dump(chunks, f, indent=2)
    return session_dir


def load_session(session_id: str) -> dict:
    session_dir = os.path.join(STORAGE_DIR, session_id)
    with open(os.path.join(session_dir, "chunks.json"), "r", encoding="utf-8") as f:
        chunks = json.load(f)
    return {"chunks": chunks}


def delete_session(session_id: str):
    import shutil
    session_dir = os.path.join(STORAGE_DIR, session_id)
    if os.path.exists(session_dir):
        shutil.rmtree(session_dir)
