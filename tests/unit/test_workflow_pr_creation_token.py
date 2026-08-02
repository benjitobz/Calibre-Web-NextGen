# SPDX-License-Identifier: GPL-3.0-or-later
"""A workflow that opens a pull request must not rely on the default token.

WHAT WENT WRONG (2026-08-02T06:54Z). `main` went red. `auto-revert.yml` — the
safety net whose entire job is to catch that — fired correctly, detected the red,
built the revert commit, and pushed `auto-revert/76ff555`. Then its last step died:

    pull request create failed: GraphQL: GitHub Actions is not permitted to
    create or approve pull requests (createPullRequest)

The branch was left on the remote, no PR was ever opened, nothing paged, and main
stayed red until a human noticed. The job did roughly 90% of its work and
abandoned the rest — the failure was recorded as "a workflow failed", never as
"a revert is stranded on a pushed branch".

The cause is a repo-level setting: *Allow GitHub Actions to create and approve
pull requests* is **off**, so `secrets.GITHUB_TOKEN` cannot call
`createPullRequest`. That setting is a genuine security control — it stops a
compromised workflow opening and self-approving its own PR — so the fix is not to
loosen it. It is to scope the capability to the one workflow that needs it, via
the `GH_PAT` repo secret.

WHY A TEST AND NOT JUST A COMMENT. The failure is invisible until the safety net
is actually needed, which is the worst possible time to discover it, and by
construction that is a moment when something else is already broken. Nothing in
CI exercises `auto-revert.yml`'s happy path, because doing so requires a red main.
A static check is the only thing that can catch this before it matters.

The rule generalises: any workflow that opens a PR has the same latent break, and
the next one added will not have this incident in living memory.
"""

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# `gh pr create`, or the GraphQL/REST mutation under any wrapper action.
CREATES_PR = re.compile(
    r"gh\s+pr\s+create|createPullRequest|peter-evans/create-pull-request",
    re.IGNORECASE,
)
DEFAULT_TOKEN = re.compile(r"secrets\.GITHUB_TOKEN")


def _workflow_files():
    assert WORKFLOWS.is_dir(), f"no workflows directory at {WORKFLOWS}"
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert files, "parsed zero workflow files"
    return files


def _steps(text):
    """Split a workflow into `- name:`-delimited step chunks.

    Crude on purpose: a real YAML parse would need a dependency, and the question
    here is only "which token is in scope where this command runs", which the
    surrounding text answers. A step that sets no token inherits the job/workflow
    `env`, so the whole file is the fallback scope.
    """
    chunks = re.split(r"\n(?=\s*- (?:name|uses):)", text)
    return [c for c in chunks if c.strip()]


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_pr_creating_steps_do_not_use_the_default_token(path):
    text = path.read_text(encoding="utf-8")
    if not CREATES_PR.search(text):
        return

    offenders = []
    for chunk in _steps(text):
        if not CREATES_PR.search(chunk):
            continue
        # The token in scope: this step's own env wins, else the file's.
        scope = chunk if DEFAULT_TOKEN.search(chunk) or "GH_TOKEN" in chunk else text
        if DEFAULT_TOKEN.search(scope) and "GH_PAT" not in scope:
            first = chunk.strip().split("\n")[0][:70]
            offenders.append(f"  {path.name}: {first}")

    assert not offenders, (
        "a workflow step opens a pull request using secrets.GITHUB_TOKEN. That "
        "token cannot call createPullRequest while 'Allow GitHub Actions to create "
        "and approve pull requests' is off at the repo level — the step will push a "
        "branch and then die, leaving the work stranded with no PR and no alert "
        "(observed 2026-08-02, auto-revert/76ff555).\n"
        "Use secrets.GH_PAT for that step. Do NOT turn the repo setting on: it "
        "exists to stop a compromised workflow self-approving a PR.\n"
        + "\n".join(offenders)
    )


def test_auto_revert_still_opens_a_pr_at_all():
    """The guard above is vacuous if the safety net stops creating PRs entirely."""
    path = WORKFLOWS / "auto-revert.yml"
    assert path.is_file(), "auto-revert.yml is missing — the red-main safety net is gone"
    text = path.read_text(encoding="utf-8")
    assert CREATES_PR.search(text), (
        "auto-revert.yml no longer opens a PR, so the token guard has nothing to "
        "protect. If this was deliberate, delete the guard too and say why."
    )
    assert "GH_PAT" in text, "auto-revert.yml must open its PR with secrets.GH_PAT"


def test_the_detector_catches_the_real_broken_shape():
    """Pin the detector against the exact pre-fix text so it cannot rot into a no-op."""
    broken = (
        "      - name: Open revert PR for the head commit\n"
        "        env:\n"
        "          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
        "        run: |\n"
        "          git push origin \"$BRANCH\"\n"
        "          gh pr create --base main --head \"$BRANCH\" --title x --body y\n"
    )
    chunks = [c for c in _steps(broken) if CREATES_PR.search(c)]
    assert chunks, "the step splitter no longer isolates the PR-creating step"
    assert DEFAULT_TOKEN.search(chunks[0]), "the default-token pattern no longer matches"
    assert "GH_PAT" not in chunks[0]

    fixed = broken.replace("secrets.GITHUB_TOKEN", "secrets.GH_PAT")
    fixed_chunks = [c for c in _steps(fixed) if CREATES_PR.search(c)]
    assert "GH_PAT" in fixed_chunks[0] and not DEFAULT_TOKEN.search(fixed_chunks[0]), (
        "the repaired shape must pass, or the guard would block its own fix"
    )
