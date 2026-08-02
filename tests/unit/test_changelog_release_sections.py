# SPDX-License-Identifier: GPL-3.0-or-later
"""Guard the release-section structure of CHANGELOG.md.

Release notes and the in-app What's New page are both generated from this file, so
its section structure is shipped content, not bookkeeping.

WHAT WENT WRONG (2026-08-02). At release time a commit moves the ``[Unreleased]``
entries under a new ``## [vX.Y.Z] - DATE`` heading and the tag is cut from that
commit. Tag ``v4.1.27`` was cut from ``851536e52``, which has the heading. The very
next commit on main -- ``76ff555a3``, a squash-merge of a PR whose branch was cut
*before* the sectioning commit -- carried the older CHANGELOG.md wholesale and
silently deleted the heading again. Git did not conflict: nothing else touched
those bytes, so the merge was clean and the whole released section simply reverted
to ``[Unreleased]``.

Nobody noticed for 21 hours. The next release would then have re-announced every
v4.1.27 fix -- the duplicates popup, the unread dates, the edit-metadata stall --
to users who already had them, and a published release can never be retracted,
only superseded.

WHY THIS TEST AND NOT A CONTENT CHECK. The reverted state is still valid markdown
and every individual entry is still present and correct, so no spell-check,
lint or link-check can see it. The only visible trace is *structural*: the
released section's ``### Changed`` / ``### Fixed`` blocks land back inside
``[Unreleased]``, which already had its own, leaving two headings of the same kind
in one section. That duplicate is the fingerprint, it is unambiguous, and it needs
no network or git history to detect.

Deliberately offline. ``sibling test_changelog_entry_integrity.py`` covers the
single-bullet variant of the same swallow class; the tag-to-section correspondence
is a separate, wider guard.
"""

import re
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[2] / "CHANGELOG.md"

VERSION_HEADING = re.compile(r"^## \[(?:v)?(\d+\.\d+\.\d+)\]")
UNRELEASED_HEADING = re.compile(r"^## \[Unreleased\]", re.IGNORECASE)
KIND_HEADING = re.compile(r"^### (\w[\w ]*)")


def _sections(text):
    """Split into (heading, [kind, ...]) pairs, one per top-level ``## `` heading."""
    sections = []
    current = None
    for line in text.split("\n"):
        if line.startswith("## "):
            if current:
                sections.append(current)
            current = (line.rstrip(), [])
        elif current is not None:
            match = KIND_HEADING.match(line)
            if match:
                current[1].append(match.group(1).strip())
    if current:
        sections.append(current)
    return sections


def _text():
    assert CHANGELOG.is_file(), f"CHANGELOG.md not found at {CHANGELOG}"
    return CHANGELOG.read_text(encoding="utf-8")


def test_no_section_repeats_a_kind_heading():
    """Two '### Fixed' blocks in one section means a released section was reverted.

    This is the exact fingerprint of the 2026-08-02 regression: the released
    section's blocks land back inside ``[Unreleased]``, which already had its own.
    """
    offenders = []
    for heading, kinds in _sections(_text()):
        duplicates = {k for k in kinds if kinds.count(k) > 1}
        if duplicates:
            offenders.append(f"  {heading} repeats: {', '.join(sorted(duplicates))}")
    assert not offenders, (
        "a CHANGELOG section repeats a '### Kind' heading. This is what a reverted "
        "release section looks like: a merge carried an older CHANGELOG.md and put "
        "already-shipped entries back under [Unreleased].\n"
        "Re-add the missing '## [vX.Y.Z] - DATE' heading above the second block; the "
        "released tag's copy of the file is the source of truth.\n" + "\n".join(offenders)
    )


def test_version_headings_are_unique():
    """One heading per version. A duplicate means a section was pasted, not moved."""
    versions = [
        VERSION_HEADING.match(h).group(1)
        for h, _ in _sections(_text())
        if VERSION_HEADING.match(h)
    ]
    duplicates = sorted({v for v in versions if versions.count(v) > 1})
    assert not duplicates, f"duplicate version heading(s) in CHANGELOG.md: {duplicates}"


def test_version_headings_descend():
    """Newest first. An out-of-order heading means one was inserted at the wrong depth."""
    versions = [
        tuple(int(p) for p in VERSION_HEADING.match(h).group(1).split("."))
        for h, _ in _sections(_text())
        if VERSION_HEADING.match(h)
    ]
    assert versions, "parsed zero version headings out of CHANGELOG.md"
    out_of_order = [
        f"{a} appears before {b}"
        for a, b in zip(versions, versions[1:])
        if a < b
    ]
    assert not out_of_order, (
        "CHANGELOG version headings must run newest-first:\n  "
        + "\n  ".join(out_of_order)
    )


def test_unreleased_is_the_first_section_when_present():
    """[Unreleased] leads the file, so a released section can never be above it."""
    headings = [h for h, _ in _sections(_text())]
    unreleased = [i for i, h in enumerate(headings) if UNRELEASED_HEADING.match(h)]
    if not unreleased:
        return  # allowed immediately after a release, before the next entry lands
    assert unreleased == [0], (
        "[Unreleased] must be the first '## ' section; found it at index "
        f"{unreleased} of {headings[:4]}"
    )


def test_the_2026_08_02_regression_is_detected():
    """Pin the detector against the real reverted file so it cannot rot into a no-op.

    Reconstructed from the actual bug: [Unreleased] holding its own '### Fixed'
    plus the v4.1.27 blocks that lost their heading.
    """
    reverted = "\n".join(
        [
            "## [Unreleased]",
            "",
            "### Fixed",
            "",
            "- **Todays fix.** Body.",
            "",
            "### Changed",
            "",
            "- **A shipped change.** Body.",
            "",
            "### Fixed",
            "",
            "- **A shipped fix.** Body.",
            "",
            "## [v4.1.26] - 2026-07-31",
            "",
            "### Fixed",
            "",
            "- **Older fix.** Body.",
            "",
        ]
    )
    unreleased_kinds = _sections(reverted)[0][1]
    assert unreleased_kinds.count("Fixed") == 2, (
        "the parser no longer sees the duplicate kind heading, so "
        "test_no_section_repeats_a_kind_heading would pass vacuously"
    )

    repaired = reverted.replace(
        "### Changed", "## [v4.1.27] - 2026-08-02\n\n### Changed", 1
    )
    for _, kinds in _sections(repaired):
        assert len(kinds) == len(set(kinds)), (
            "the repaired shape must be clean, or the guard would block the fix"
        )
