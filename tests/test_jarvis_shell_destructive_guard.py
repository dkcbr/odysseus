"""Real, deliberately safe tests for the destructive-command guard in
jarvis_shell_mcp.py (added 2026-08-21). Tests ONLY the regex pattern
list directly -- never imports or calls run_command itself, and never
executes a real shell command. This exists specifically because of a
real incident: an earlier round of testing exercised these same
patterns against a live, unguarded run_command and caused real,
genuine damage (a source file replaced by a 26GB file of zeros, a
directory's permissions zeroed). This file is the safe alternative --
pure string/regex matching only, zero risk of actual execution."""
import sys
import os

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "services", "mcp_servers", "jarvis_shell"
))
from jarvis_shell_mcp import DESTRUCTIVE_COMMAND_PATTERNS


def _matches(command: str) -> bool:
    return any(pattern.search(command) for pattern, _description in DESTRUCTIVE_COMMAND_PATTERNS)


def test_rm_rf_dot_blocked():
    assert _matches("rm -rf .")


def test_rm_rf_star_blocked():
    assert _matches("rm -rf *")


def test_rm_rf_tilde_blocked():
    assert _matches("rm -rf ~")


def test_rm_rf_root_blocked():
    assert _matches("rm -rf /")


def test_git_clean_xfd_blocked():
    assert _matches("git clean -xfd")


def test_find_delete_blocked():
    assert _matches("find . -delete")


def test_shred_blocked():
    assert _matches("shred -u file.txt")


def test_dd_zero_blocked():
    assert _matches("dd if=/dev/zero of=file.txt")


def test_dd_random_blocked():
    assert _matches("dd if=/dev/random of=file.txt")


def test_chmod_000_blocked():
    assert _matches("chmod -R 000 .")


def test_truncate_wildcard_blocked():
    assert _matches("truncate -s 0 *.md")


def test_mv_devnull_blocked():
    assert _matches("mv file.txt /dev/null")


def test_ordinary_ls_not_blocked():
    assert not _matches("ls -la")


def test_specific_narrow_rm_not_blocked():
    """Real, deliberate negative test: a specific, named path is
    legitimate, ordinary cleanup work and should NOT be flagged --
    this is the real, intended distinction from a broad/wildcard
    target."""
    assert not _matches("rm -rf /tmp/some_real_subdir")


def test_echo_not_blocked():
    assert not _matches("echo hello world")


def test_ordinary_truncate_not_blocked():
    """Real, deliberate negative test: truncating a single, specific,
    named file is ordinary, legitimate work -- only the wildcard form
    is flagged."""
    assert not _matches("truncate -s 0 specific_file.txt")
