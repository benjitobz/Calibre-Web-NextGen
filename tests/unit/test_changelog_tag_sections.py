# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep every canonical-era published tag represented in CHANGELOG.md."""

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
WHATS_NEW = ROOT / "frontend" / "src" / "data" / "whatsNew.ts"
CANONICAL_CHANGELOG_FIRST_RELEASE = "v4.0.147"

VERSION = re.compile(r"v\d+\.\d+\.\d+")
RELEASE_HEADING = re.compile(
    r"^## \[(v\d+\.\d+\.\d+)\]\s+[-–]\s+\d{4}-\d{2}-\d{2}\s*$",
    re.MULTILINE,
)
WHATS_NEW_VERSION = re.compile(
    r"^\s*version:\s*'(v\d+\.\d+\.\d+)',\s*$",
    re.MULTILINE,
)


def _version_tuple(version: str) -> tuple[int, int, int]:
    return tuple(map(int, version.removeprefix("v").split(".")))


def _missing_sections(published: set[str], headings: set[str]) -> list[str]:
    """Return canonical-era published versions absent from the changelog."""
    boundary = _version_tuple(CANONICAL_CHANGELOG_FIRST_RELEASE)
    covered_tags = {
        version for version in published if _version_tuple(version) >= boundary
    }
    return sorted(covered_tags - headings, key=_version_tuple)


def _published_versions() -> tuple[set[str], str]:
    """Use local tags, or the committed public-release ledger offline."""
    result = subprocess.run(
        ["git", "tag", "--list", "v*"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    tags = {tag for tag in result.stdout.splitlines() if VERSION.fullmatch(tag)}
    if tags:
        return tags, "local git tags"

    assert WHATS_NEW.is_file(), (
        "cannot determine published releases: no semver git tags are available "
        f"and the committed ledger is missing at {WHATS_NEW}"
    )
    versions = set(WHATS_NEW_VERSION.findall(WHATS_NEW.read_text(encoding="utf-8")))
    assert versions, (
        "cannot determine published releases: no semver git tags are available "
        "and the committed What's New ledger contains no release versions"
    )
    return versions, "committed What's New ledger (git tags unavailable)"


def test_every_published_version_has_a_changelog_section():
    text = CHANGELOG.read_text(encoding="utf-8")
    headings = set(RELEASE_HEADING.findall(text))
    assert headings, "CHANGELOG.md contains no dated semver release sections"
    published, source = _published_versions()
    missing = _missing_sections(published, headings)
    assert not missing, (
        f"published release(s) from {source}, at or after the canonical "
        f"CHANGELOG boundary {CANONICAL_CHANGELOG_FIRST_RELEASE}, have no "
        "matching dated section: " + ", ".join(missing)
    )


def test_detector_rejects_the_real_missing_tag_section_shape():
    """Pin the correspondence detector to the v4.1.27 regression."""
    headings_after_revert = {"v4.1.26", "v4.1.25"}
    assert _missing_sections({"v4.1.27"}, headings_after_revert) == ["v4.1.27"]


def test_tagless_checkout_uses_committed_release_ledger(monkeypatch):
    """A source archive or offline shallow clone remains strict, never skips."""
    class NoTags:
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: NoTags())
    versions, source = _published_versions()
    assert "v4.1.27" in versions
    assert source == "committed What's New ledger (git tags unavailable)"
