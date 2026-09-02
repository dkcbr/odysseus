#!/usr/bin/env python3
"""
J.A.R.V.I.S -- YouTube Transcript MCP Server
==============================================
Real, added 2026-08-09: fetches a real YouTube video's transcript via
yt-dlp (auto or uploaded captions) and returns clean, timestamped text.
Built after two real, separate incidents where Claude's own built-in
web_search/web_fetch tools failed to retrieve a video's real content
(irrelevant search results, a 429 rate limit) -- this gives Jarvis (and
Claude working within it) a real, independent, working path to a
video's actual transcript, not dependent on those chat-level tools.

Exposes: get_transcript(url, lang="en")

Registration:
    fetch('/api/mcp/servers', {
      method: 'POST',
      credentials: 'same-origin',
      body: new URLSearchParams({
        name: 'youtube_transcript',
        transport: 'stdio',
        command: 'python3',
        args: '["/app/services/mcp_servers/youtube_transcript/youtube_transcript_mcp.py"]',
        env: '{}'
      })
    }).then(r => r.json()).then(console.log)
"""

import re
import subprocess
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="JARVIS YouTube Transcript",
    instructions=(
        "Fetches a real YouTube video's transcript (auto or uploaded captions) "
        "via yt-dlp and returns clean, timestamped, readable text. Use this "
        "whenever a YouTube URL needs its actual content reviewed and the "
        "person hasn't already pasted a transcript directly."
    ),
)


def _clean_vtt(vtt_text: str) -> str:
    """Real VTT cleaner: strips word-level timing tags, dedupes the
    rolling-caption overlap auto-captions produce (each cue repeats the
    prior line as context), returns clean timestamped lines."""
    lines = vtt_text.split("\n")
    cues = []
    current_time = None
    for line in lines:
        line = line.strip()
        if "-->" in line:
            current_time = line.split("-->")[0].strip().split(".")[0]
            continue
        if not line or line.upper().startswith(("WEBVTT", "KIND:", "LANGUAGE:")):
            continue
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if clean and current_time:
            cues.append((current_time, clean))

    deduped = []
    for i, (t, text) in enumerate(cues):
        if i + 1 < len(cues) and cues[i + 1][1].startswith(text):
            continue
        deduped.append((t, text))

    return "\n".join(f"{t}  {text}" for t, text in deduped)


@mcp.tool()
async def get_transcript(url: str, lang: str = "en") -> str:
    """Fetch a real YouTube video's transcript via yt-dlp. Returns clean,
    timestamped text, or a clear error message if no captions exist for
    this video/language (some videos genuinely have none)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            "python3", "-m", "yt_dlp",
            "--skip-download", "--write-sub", "--write-auto-sub",
            "--sub-format", "vtt",
            "--sub-lang", lang,
            "-o", str(Path(tmpdir) / "%(id)s.%(ext)s"),
            url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        vtt_files = list(Path(tmpdir).glob("*.vtt"))
        if not vtt_files:
            return (
                f"No transcript found for this video (lang={lang}). "
                f"yt-dlp output: {proc.stdout[-500:]}\n{proc.stderr[-500:]}"
            )
        vtt_text = vtt_files[0].read_text(encoding="utf-8", errors="ignore")
        cleaned = _clean_vtt(vtt_text)
        if not cleaned:
            return "Transcript file was empty after cleaning -- unexpected VTT format."
        return cleaned


if __name__ == "__main__":
    mcp.run()
