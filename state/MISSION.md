# Mission: cwngsync storage, deletion, and device collections

Updated: 2026-08-28   Phase: delivery   Status: 9/9 outcomes verified

## Definition of done

- [x] Phase 2 accepted head is clean, pushed, and is the implementation base.
- [x] Phase 3 stores fresh per-device free/total space without changing `Device`.
- [x] Phase 3 refuses an oversized delivery at claim time and reports the refusal cleanly.
- [x] Phase 3 names server-requested deletions, lets the device confirm them, and never derives deletion from inventory omission.
- [x] Phase 3 has behavioral red-before-green evidence and a user-facing changelog fragment.
- [x] Phase 4 publishes shelf snapshots scoped by both user and device and applies them as KOReader collections.
- [x] Phase 4 has behavioral red-before-green evidence and a user-facing changelog fragment.
- [x] Full unit lane `python -m pytest -q -p no:randomly -m unit` has zero failures.
- [x] All scoped changes are committed as `new-usemame` and pushed to `feat/cwngsync-send-to-device`.

## Now / next action

Commit the verified implementation with the required public identity and push the branch.

## How to verify

- Focused Phase 3 and Phase 4 pytest modules, with their pre-implementation failures retained in the session report.
- Existing CWNGSync unit group: `python -m pytest -q -p no:randomly tests/unit/test_cwngsync_*.py`.
- Full unit lane: `python -m pytest -q -p no:randomly -m unit`.
- `git diff --check`, scoped staged-diff audit, clean local/remote commit equality.

## Decisions and rationale

- 2026-08-28: use CWNG briefing/git-manager, capabilities/routing, and run-to-done guidance; implement locally in the current Sol session with no parallel agents.
- 2026-08-28: replace the completed SPA mission ledger inherited from another branch with this branch's active two-phase mission.
- 2026-08-28: preserve the immutable Phase 1 rule that an inventory omission is only absence of evidence and can never create a delete request.
- 2026-08-28: use latest storage at queue time as an early refusal, freshly reported storage at claim time as server admission, and KOReader's immediate pre-download `df` result as the authoritative racing check. A losing final check reports refusal and requeues without creating a partial file.
- 2026-08-28: namespace KOReader-managed collection names with an opaque per-user/per-device scope; rebuild only that scope's collections so membership removals and shelf renames converge without touching another account's or unmanaged collections.
- 2026-08-28: expose exact inventory observation IDs and storage values in the existing Devices API/UI; deletion requests are always explicit user actions against one named observation.

## Open questions

- None.
