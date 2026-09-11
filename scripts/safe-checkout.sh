#!/usr/bin/env bash
# Real safeguard against the exact incident from 2026-08-13/14: uncommitted
# work (get_portfolio_context's full implementation, then a requirements.txt
# change) was silently wiped twice by `git checkout <branch> -- .` runs from
# other concurrent sessions working on this same repo.
#
# git has no real "pre-checkout" hook (verified -- only post-checkout exists,
# which runs too late to prevent anything). Instead, this is wired in as a
# real git ALIAS overriding `checkout` itself, so `git checkout ...` is
# intercepted automatically -- no need to remember a different command name.
set -euo pipefail

FORCE=0
ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--force" ]]; then
    FORCE=1
  else
    ARGS+=("$arg")
  fi
done

if [[ $FORCE -ne 1 ]] && [[ -n "$(git status --porcelain)" ]]; then
  echo "REFUSED: uncommitted changes present, and this checkout could silently"
  echo "discard them (this exact thing happened twice already tonight)."
  echo
  git status --short
  echo
  echo "Commit or stash your changes first, or re-run with --force if you"
  echo "genuinely intend to discard them:"
  echo "  git checkout --force ${ARGS[*]}"
  exit 1
fi

exec git -c alias.checkout= checkout "${ARGS[@]}"
