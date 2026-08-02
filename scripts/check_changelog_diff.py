#!/usr/bin/env python3
"""Reject pull-request changelog diffs that swallow released structure."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys


# The canonical file used an en dash through much of v4.0 and a hyphen later.
# This guard cares about heading identity, not date typography.
RELEASE_HEADING = re.compile(r"^## \[(v\d+\.\d+\.\d+)\]", re.MULTILINE)
ENTRY_LEAD = re.compile(r"^- \*\*", re.MULTILINE)


def structural_regressions(base: str, proposed: str) -> list[str]:
    """Return user-facing errors for structure lost from ``base``."""
    errors: list[str] = []

    base_entries = len(ENTRY_LEAD.findall(base))
    proposed_entries = len(ENTRY_LEAD.findall(proposed))
    if proposed_entries < base_entries:
        errors.append(
            "CHANGELOG.md loses "
            f"{base_entries - proposed_entries} top-level '- **' release-note "
            "bullet(s). Rewrite or move entries instead of deleting them."
        )

    base_releases = set(RELEASE_HEADING.findall(base))
    proposed_releases = set(RELEASE_HEADING.findall(proposed))
    missing_releases = sorted(base_releases - proposed_releases)
    if missing_releases:
        errors.append(
            "CHANGELOG.md removes existing release heading(s): "
            + ", ".join(missing_releases)
            + ". A stale branch may have overwritten a release section; rebase "
            "or merge the current target branch before proceeding."
        )

    return errors


def pull_request_regressions(
    target: str, branch_point: str, proposed: str
) -> list[str]:
    """Check a PR only when that branch actually edited ``CHANGELOG.md``."""
    if branch_point == proposed:
        return []
    return structural_regressions(target, proposed)


def _file_at(ref: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{ref}:CHANGELOG.md"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or "git show failed"
        raise RuntimeError(f"cannot read CHANGELOG.md at {ref}: {detail}")
    return result.stdout


def _merge_base(base_ref: str, head_ref: str) -> str:
    result = subprocess.run(
        ["git", "merge-base", base_ref, head_ref],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode or not result.stdout.strip():
        detail = result.stderr.strip() or "git merge-base returned no commit"
        raise RuntimeError(
            f"cannot find merge base for {base_ref} and {head_ref}: {detail}"
        )
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reject structural CHANGELOG.md loss between two git refs."
    )
    parser.add_argument("base_ref")
    parser.add_argument("head_ref")
    args = parser.parse_args(argv)

    try:
        branch_point = _merge_base(args.base_ref, args.head_ref)
        errors = pull_request_regressions(
            _file_at(args.base_ref),
            _file_at(branch_point),
            _file_at(args.head_ref),
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if errors:
        print("CHANGELOG integrity guard failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(
        "CHANGELOG integrity guard passed: "
        "no PR-authored release structure was lost."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
