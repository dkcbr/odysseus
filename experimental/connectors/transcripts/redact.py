"""Real, minimal redaction -- same conservative approach as the rest of
Herald (no external dependency, simple regex-based)."""
import re

_HEX_TOKEN = re.compile(r"\b[A-Fa-f0-9]{20,}\b")
_EMAIL = re.compile(r"\b[\w.-]+@[\w.-]+\.\w+\b")


def redact(text: str) -> str:
    text = _HEX_TOKEN.sub("[REDACTED_TOKEN]", text)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    return text
