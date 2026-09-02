# Post-mortem: silent loss of uncommitted edits (2026-08-13/14)

## Summary

Twice in one working session, real, substantial code changes were
silently lost because they existed only as uncommitted edits in a
working tree that multiple concurrent sessions were editing at once.
Both losses went undetected for hours after they happened, and were
only found by directly verifying live system state rather than
trusting that earlier work was still present.

## What was lost

1. **The full `get_portfolio_context` tool implementation** — a
   real feature spanning 6 registries (`tool_schemas.py`,
   `tool_execution.py`, `agent_tools/__init__.py`, `tool_index.py`
   x2, `agent_loop.py`), built and verified working earlier in the
   session, never committed to git.
2. **A `requirements.txt` addition** (`python-docx`, `python-pptx`,
   `openpyxl`, `reportlab`) needed for a document-creation feature,
   added and verified installed earlier in the same session, also
   never committed.

Both were wiped by the same real mechanism: another concurrent
session ran `git checkout <branch> -- .`, which silently discards
any uncommitted changes to tracked files in the working tree. Git
gave no warning, because this is normal, expected `git checkout`
behavior — it is not a bug in git.

One of the two losses was partially noticed by the session that
caused it, which left a TODO comment (commit `703c5b7c`) describing
an "11-line delta" as lost. The real scope of what had actually been
lost was much larger, and the second, separate loss (the
`requirements.txt` change) was not caught by that same review at
all — it was found independently, hours later, when a real end-to-end
test failed with `No module named 'docx'` despite the packages having
been verified installed earlier in the same session.

## Why this happened

- **Multiple sessions work on this repo concurrently.** Uncommitted
  edits in a shared working tree are visible to, and can be
  overwritten by, any other session's git operations — there is no
  isolation between them.
- **`git checkout <branch> -- .`, and `git checkout` in general, has
  no built-in safety check for uncommitted changes to the paths it
  touches.** This is standard, documented git behavior, not a
  misconfiguration.
- **Real work sat uncommitted for an extended period** in both
  cases, widening the window in which it was vulnerable to being
  overwritten by someone else's concurrent operation.

## How each loss was actually found

Not through any monitoring or alert — both were found by chance,
during otherwise-unrelated work, when live system behavior didn't
match what earlier verification had confirmed:

- The `get_portfolio_context` loss was found while investigating why
  a *different*, newer tool (`create_document_office`) wasn't being
  offered to the model — a live check of `ALWAYS_AVAILABLE` inside
  the running container showed neither tool present, despite both
  having been verified working earlier.
- The `requirements.txt` loss was found immediately after fixing an
  unrelated tool-selection bug, when a real end-to-end test produced
  a genuine, unexpected `No module named 'docx'` error rather than
  the expected success.

In both cases, the fix was the same: re-apply the change, then
commit and push it immediately rather than leaving it as a raw edit
again.

## What changed as a result

1. **Discipline: commit and push real work immediately**, per file,
   as soon as it's verified working — not batched up for later. This
   is the primary, load-bearing mitigation; everything else here is
   secondary.
2. **`tests/test_recovered_tools_present.py`** — asserts the
   specific tools lost in this incident are present across every
   real registry each one needs. Written test-first: confirmed
   failing before reconstruction, passing after.
3. **`tests/test_agent_loop_workspace_override.py`** — a related,
   separate regression guard for the tool-selection bug found while
   investigating this incident (a destructive tool-set assignment
   that silently dropped `ALWAYS_AVAILABLE` tools on certain
   requests). Checks the real source text directly for the correct,
   non-destructive union pattern.
4. **`scripts/safe-checkout.sh`, wired as a `git safe-checkout`
   alias** — an opt-in guard that refuses to run `checkout` if the
   working tree has uncommitted changes, unless `--force` is passed.
   Note on scope: git does not support a real `pre-checkout` hook
   (verified directly — only `post-checkout` exists, which fires
   too late to prevent anything), and git also refuses to let the
   real `checkout` command itself be aliased or overridden (also
   verified directly). So this is a separate, opt-in command, not
   automatic interception — a real, tested mitigation, but not a
   guarantee.
5. **CI: a new, blocking `regression-guards` job** runs the two
   tests above (plus the pre-existing `test_tool_index_schema_parity.py`,
   the same class of guard) on every PR, always blocking on failure —
   unlike the broader `python-tests` job, which is deliberately
   `continue-on-error` due to known-flaky tests elsewhere in the
   suite.

## What this does not fully solve

`git safe-checkout` only helps if it's actually used instead of
plain `git checkout` — it cannot prevent another session's own,
separate `git checkout` call from causing the same kind of loss
again. The real, durable fix is the discipline change (commit early,
commit often, push immediately), with the tests and CI job serving
as a safety net that catches *this specific* class of regression
after the fact, not a way to prevent the underlying race condition
from recurring in some other form.
