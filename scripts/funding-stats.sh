#!/usr/bin/env bash
# Regenerate the project-scale figures used in funding copy.
#
# These numbers appear in three places that must agree:
#   1. README.md, between the <!-- funding-stats:start/end --> markers (rewritten by --apply)
#   2. The GitHub Sponsors profile introduction  (paste by hand)
#   3. The Ko-fi page "About" section            (paste by hand)
#
# Stale figures in a funding pitch read as dishonest, so this exists to make
# refreshing them a ten-second job rather than a research task.
#
# Usage:
#   scripts/funding-stats.sh            # print the numbers and the README line
#   scripts/funding-stats.sh --apply    # also rewrite the README block in place

set -euo pipefail

REPO="new-usemame/Calibre-Web-NextGen"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
README="$ROOT/README.md"
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

command -v gh >/dev/null || { echo "error: gh not found" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "error: gh not authenticated" >&2; exit 1; }

echo "Querying GitHub for $REPO ..." >&2

# Published releases (not tags — a tag without a release isn't something a user can pull).
releases=$(gh api --paginate "repos/$REPO/releases" --jq '.[].tag_name' | wc -l | tr -d ' ')

# Merged PRs and closed issues, all-time. The repo was created 2026-05-02, so
# all-time and "since May 2026" are the same window.
merged_prs=$(gh api -X GET search/issues -f q="repo:$REPO is:pr is:merged" --jq '.total_count')
closed_issues=$(gh api -X GET search/issues -f q="repo:$REPO is:issue is:closed" --jq '.total_count')

# Distinct upstream authors credited by handle in the backport ledger.
contributors=$(grep -oE '@[A-Za-z0-9][A-Za-z0-9-]*' "$ROOT/CHANGES-vs-upstream.md" \
  | sort -u | wc -l | tr -d ' ')

LINE="Since May 2026: **${releases} releases, ${merged_prs} merged pull requests, ${closed_issues} issues closed, and ${contributors} contributors credited by name.**"

cat <<EOF

  releases published .... $releases
  merged PRs ............ $merged_prs
  closed issues ......... $closed_issues
  credited contributors . $contributors

README line:
$LINE

EOF

if [[ $APPLY -eq 1 ]]; then
  awk -v line="$LINE" '
    /<!-- funding-stats:start/ { print; print line; skip = 1; next }
    /<!-- funding-stats:end/   { skip = 0 }
    !skip { print }
  ' "$README" > "$README.tmp"

  if ! grep -q 'funding-stats:start' "$README.tmp"; then
    rm -f "$README.tmp"
    echo "error: marker block not found in README.md — refusing to write" >&2
    exit 1
  fi

  mv "$README.tmp" "$README"
  echo "README.md updated." >&2
else
  echo "(re-run with --apply to rewrite the README block)" >&2
fi
