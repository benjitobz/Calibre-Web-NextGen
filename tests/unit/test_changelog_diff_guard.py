# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin the PR-level guard against structural CHANGELOG loss."""

from pathlib import Path

from scripts.check_changelog_diff import (
    pull_request_regressions,
    structural_regressions,
)


ROOT = Path(__file__).resolve().parents[2]


def test_missing_release_heading_is_rejected():
    base = """## [Unreleased]\n\n## [v4.1.27] - 2026-08-02\n\n### Fixed\n- **A fix.**\n"""
    stale_branch = """## [Unreleased]\n\n### Fixed\n- **A new fix.**\n- **A fix.**\n"""
    errors = structural_regressions(base, stale_branch)
    assert any("v4.1.27" in error for error in errors)


def test_missing_historical_en_dash_release_heading_is_rejected():
    base = """## [v4.0.147] – 2026-06-05\n"""
    errors = structural_regressions(base, "")
    assert any("v4.0.147" in error for error in errors)


def test_net_release_bullet_loss_is_rejected():
    base = """### Fixed\n- **First fix.**\n- **Second fix.**\n"""
    swallowed = """### Fixed\n- **First fix now contains both fixes.**\n"""
    errors = structural_regressions(base, swallowed)
    assert any("loses 1" in error for error in errors)


def test_wording_edits_reordering_and_release_sectioning_are_allowed():
    base = """## [Unreleased]\n\n### Fixed\n- **First wording.**\n- **Second wording.**\n"""
    proposed = """## [Unreleased]\n\n## [v4.1.28] - 2026-08-03\n\n### Fixed\n- **Rewritten second wording.**\n- **Rewritten first wording.**\n"""
    assert structural_regressions(base, proposed) == []


def test_stale_pr_that_did_not_edit_changelog_is_allowed():
    branch_point = """## [Unreleased]\n\n### Fixed\n- **Old fix.**\n"""
    current_target = branch_point + "\n## [v4.1.27] - 2026-08-02\n"
    assert pull_request_regressions(current_target, branch_point, branch_point) == []


def test_stale_pr_that_did_edit_changelog_must_preserve_current_releases():
    branch_point = """## [Unreleased]\n\n### Fixed\n- **Old fix.**\n"""
    current_target = branch_point + "\n## [v4.1.27] - 2026-08-02\n"
    proposed = branch_point.replace("- **Old fix.**", "- **PR fix.**\n- **Old fix.**")
    errors = pull_request_regressions(current_target, branch_point, proposed)
    assert any("v4.1.27" in error for error in errors)


def test_pr_ci_invokes_guard_with_complete_git_history():
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    fast_tests = workflow.split("  fast-tests:", 1)[1].split("\n  #", 1)[0]
    changed_paths = workflow.split("  changed_paths:", 1)[1].split("\n  #", 1)[0]
    assert "fetch-depth: 0" in fast_tests
    assert "fetch-depth: 0" in changed_paths
    assert 'python3 scripts/check_changelog_diff.py "$BASE_SHA" "$HEAD_SHA"' in changed_paths
