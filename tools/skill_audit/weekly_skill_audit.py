#!/usr/bin/env python3
"""
Weekly skill usage audit -- real idea from Nate Herk's AIOS video (session
2026-08-XX vault note: jarvis-integration-ideas-nate-herk-aios-video.md).

Distinct from the real, already-existing POST /audit-all endpoint
(routes/skills_routes.py), which tests whether each skill's *content*
still works correctly. This audit is about *usage*: which skills get
used often enough to be candidates for full automation instead of
on-demand invocation, and which are never used at all (candidates for
deletion or rework) -- confirmed via search this session that no such
usage-based review previously existed.

Reads real, already-existing data -- no new tracking infrastructure:
- data/skills/_usage.json (uses count + last_used timestamp per skill,
  confirmed real and populated when this was built)
- The real skill directory list (data/skills/<category>/<name>/)

Real, honest handling of a known real quirk found while building this:
_usage.json contains both bare skill-name keys and "owner::name"-prefixed
keys for what appears to be the same underlying skill (per-owner
tracking) -- this script reports both real, distinct entries rather than
silently merging them, since merging would be an assumption this session
didn't verify.
"""
import json
import time
from pathlib import Path

SKILLS_DIR = Path("/home/dk/jarvis/projects/odysseus/data/skills")
USAGE_FILE = SKILLS_DIR / "_usage.json"

# Real, simple thresholds -- not tuned against any real data yet, since
# there's only 4 real usage records to work with; adjust once more real
# usage accumulates.
HIGH_USE_THRESHOLD = 5  # uses -- candidate for full automation
STALE_DAYS = 30  # no use in this many days -- flagged for review


def load_real_skill_names():
    """Real skill names from the actual directory structure, not a guess."""
    names = set()
    for category_dir in SKILLS_DIR.iterdir():
        if not category_dir.is_dir() or category_dir.name.startswith("_"):
            continue
        for skill_dir in category_dir.iterdir():
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                names.add(skill_dir.name)
    return names


def load_real_usage():
    if not USAGE_FILE.exists():
        return {}
    return json.loads(USAGE_FILE.read_text())


def run_audit():
    real_skills = load_real_skill_names()
    usage = load_real_usage()
    now = time.time()

    print(f"Weekly Skill Usage Audit -- {time.strftime('%Y-%m-%d')}")
    print(f"Real skills found: {len(real_skills)}")
    print(f"Real usage records: {len(usage)}\n")

    # Real, honest matching: strip "owner::" prefix if present, but report
    # both the raw key and the resolved skill name -- don't silently merge.
    high_use = []
    used_but_low = []
    never_used = []
    stale_usage_entries = []  # usage records that don't match any real current skill

    accounted_for = set()
    for raw_key, record in usage.items():
        skill_name = raw_key.split("::", 1)[-1] if "::" in raw_key else raw_key
        uses = record.get("uses", 0)
        last_used = record.get("last_used")
        age_days = (now - last_used) / 86400 if last_used else None

        if skill_name not in real_skills:
            stale_usage_entries.append((raw_key, uses, age_days))
            continue

        accounted_for.add(skill_name)
        entry = (raw_key, skill_name, uses, age_days)
        if uses >= HIGH_USE_THRESHOLD:
            high_use.append(entry)
        else:
            used_but_low.append(entry)

    for skill_name in real_skills - accounted_for:
        never_used.append(skill_name)

    print("=== High use (candidates for full automation) ===")
    if not high_use:
        print("  (none yet -- real usage counts are all below threshold)")
    for raw_key, skill_name, uses, age_days in sorted(high_use, key=lambda x: -x[2]):
        age_str = f"{age_days:.0f}d ago" if age_days is not None else "unknown"
        print(f"  {skill_name}: {uses} uses, last used {age_str} (record: {raw_key})")

    print("\n=== Used, but below automation threshold ===")
    for raw_key, skill_name, uses, age_days in sorted(used_but_low, key=lambda x: -x[2]):
        age_str = f"{age_days:.0f}d ago" if age_days is not None else "unknown"
        stale_flag = " [STALE >30d]" if age_days and age_days > STALE_DAYS else ""
        print(f"  {skill_name}: {uses} uses, last used {age_str} (record: {raw_key}){stale_flag}")

    print("\n=== Never used (candidates for deletion or rework) ===")
    if not never_used:
        print("  (none -- every real skill has at least one usage record)")
    for skill_name in sorted(never_used):
        print(f"  {skill_name}")

    if stale_usage_entries:
        print("\n=== Usage records with no matching real skill (stale data) ===")
        for raw_key, uses, age_days in stale_usage_entries:
            print(f"  {raw_key}: {uses} uses (skill no longer exists on disk)")

    print(f"\nSummary: {len(high_use)} high-use, {len(used_but_low)} low-use, "
          f"{len(never_used)} never-used, {len(stale_usage_entries)} stale usage record(s).")


if __name__ == "__main__":
    run_audit()
