---
name: gsd-update-blueprint
description: Audit a target repository against the current reusable GSD blueprint, show safe sync actions and conflicts, then apply only after the user replies exactly `sync approved`.
---

# GSD Update Blueprint

Audit and, after exact confirmation, safely install or update reusable GSD blueprint assets in one target project repository.

The only user-facing command for this workflow is:

```text
$gsd-update-blueprint <TARGET_REPOSITORY_PATH>
```

## Source Of Truth

- Treat the current working directory as the reusable GSD blueprint source path.
- Verify the current working directory contains `.gsd/blueprint-manifest.json`. If it does not, stop with a clear error that the command must be run from the reusable GSD blueprint source repository.
- Use `.gsd/blueprint-manifest.json` from the current working directory as the authoritative inventory.
- Use `.planning/templates/blueprint-distribution-contract.md` for ownership and sync safety rules.
- Use `.gsd/managed-blocks/agents-operating-contract.md` as the canonical source for the target `AGENTS.md` `GSD-BLUEPRINT: operating-contract` managed block.
- Use `.gsd/blueprint.lock.json` in the target project repository when present.

## Input Contract

- Accept exactly one argument: `<TARGET_REPOSITORY_PATH>`.
- Treat that argument as the target project repository path.
- Do not ask for or accept a separate blueprint source path; the source is always the current working directory.
- Do not sync any external repository other than the single target path provided by the user.
- Do not write Obsidian vault memory.

## Primary Purpose

- Replace the old two-step audit-then-sync route with one gated skill.
- Run an audit-only pass first.
- Show proposed safe sync actions and conflicts before any write.
- Require exact confirmation before applying safe sync actions.
- Preserve project-owned runtime artifacts exactly.
- Remove retired blueprint-owned files that were previously installed but no longer exist in the current blueprint manifest, when ownership is proven safe.
- Update or create `.gsd/blueprint.lock.json` only after successful sync.

## Confirmation Gate

After the audit-only pass, show this prompt exactly:

```text
Audit completed. Reply "sync approved" to apply the safe sync actions, or "cancel" to stop.
```

- Accept only the exact response `sync approved` to apply sync actions.
- Accept only the exact response `cancel` to stop without changes.
- Do not accept vague confirmations such as `yes`, `ok`, `approved`, `go ahead`, or similar.
- If the response is anything else, stop and repeat that exact confirmation is required.
- Do not apply blocked, conflicting, destructive, ambiguous, or review-required actions even when the user replies `sync approved`.

## Sync Mode Inference

Infer the mode during audit:

- `install`: the target has no `.gsd/blueprint.lock.json` and no installed GSD files.
- `update`: the target has `.gsd/blueprint.lock.json` or any installed GSD files.

Installed GSD evidence includes existing target files or markers from the manifest, such as `.gsd/blueprint-manifest.json`, `.gsd/managed-blocks/agents-operating-contract.md`, `.agents/skills/gsd-*`, `.planning/templates/blueprint-distribution-contract.md`, `AGENTS.md` with `GSD-BLUEPRINT` markers, or starter planning files from the manifest.

## Audit Workflow

1. Read `.planning/tool-capabilities.md` when present before running repository commands.
2. Confirm the current working directory contains `.gsd/blueprint-manifest.json`; otherwise stop.
3. Resolve the single target path and confirm it exists as a directory.
4. Read and validate `.gsd/blueprint-manifest.json`.
5. Read the target `.gsd/blueprint.lock.json` if it exists.
6. Infer `install` or `update`.
7. For every manifest entry, classify the action without modifying files:
   - `README.md`: always project-owned; never create, compare as required drift, overwrite, replace, or managed-block-update.
   - `blueprint_replace` / `replace`: safe replace only when the target content differs and no overwrite risk is detected.
   - `managed_block`: compare and propose updates only inside matching `GSD-BLUEPRINT` markers.
   - `bootstrap_then_managed_block`: create the full starter file only if missing; if present, compare only the reusable `GSD-BLUEPRINT` block.
   - `bootstrap_if_missing`: create only when missing; preserve existing files exactly.
   - `project_preserve`: preserve and report present or missing when useful.
   - `generated_project_local`: ignore and verify it will not be touched.
8. Compare target lock entries against the current manifest to find retired blueprint files:
   - A retired blueprint file is a path recorded in the target `.gsd/blueprint.lock.json` as previously installed by the blueprint but absent from the current source manifest.
   - Classify retired files as `safe_delete`, `delete_conflict`, or `already_absent`.
   - Classify as `safe_delete` only when the old lock entry proves blueprint ownership and the path is not project-owned, generated project-local, or protected runtime state.
   - If the lock entry includes a checksum or installed content hash, classify as `safe_delete` only when the current target file still matches that recorded installed content.
   - If the lock entry has no checksum, allow `safe_delete` only for strict blueprint-owned file paths from old `blueprint_replace` entries, such as `.agents/skills/**`, `.agents/stack-profiles/**`, `.planning/templates/**`, `.gsd/managed-blocks/**`, and `.gsd/blueprint-manifest.json`; otherwise classify as `delete_conflict`.
   - Never classify a directory wildcard as `safe_delete`; classify only concrete files proven by the lock.
   - Never classify `README.md`, `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/CONTEXT_INDEX.md`, `.planning/milestones/**`, `.planning/phases/**`, `.planning/verification/**`, `.planning/archive/**`, `.codex/**`, `PROJECT.md`, `.planning/CODEBASE_MAP.md`, or `AGENTS.md` as `safe_delete`.
9. Detect conflicts and review-required cases:
   - malformed or duplicated markers
   - unknown ownership
   - legacy unmarked `AGENTS.md` migration requirement
   - local managed-block edits requiring review
   - possible overwrite or delete risk
   - missing markers where insertion would affect existing project content
10. Present the required audit output and exact confirmation prompt.

## Required Audit Output

The audit response must include:

- Audit disposition: `safe_to_sync` | `blocked` | `partial`
- Blueprint source path
- Target project repository path
- Inferred sync mode: `install` | `update`
- Safe sync actions
- Conflicts
- Preserved files
- Skipped files
- Missing files
- Managed-block changes
- Retired blueprint files
- Safe delete actions
- Delete conflicts
- Confirmation prompt

Use dispositions as follows:

- `safe_to_sync`: all proposed writes and safe deletes are safe sync actions and no conflicts block application.
- `partial`: some safe sync actions exist, but conflicts or review-required items must remain unapplied.
- `blocked`: no sync may be applied because conflicts, ownership ambiguity, or overwrite risk affects the operation.

## Sync Workflow

Only after the user replies exactly `sync approved`:

1. Re-read the manifest and target files that will be changed.
2. Reconfirm that each write and safe delete is still a safe sync action from the audit.
3. Apply only safe actions:
   - create missing `blueprint_replace` files from the blueprint
   - replace differing `blueprint_replace` files from the blueprint
   - create missing `bootstrap_then_managed_block` starter files from the blueprint
   - update only marked `GSD-BLUEPRINT` blocks for `managed_block` and existing `bootstrap_then_managed_block` files
   - create missing `bootstrap_if_missing` files
   - delete retired blueprint-owned files classified as `safe_delete`
4. Preserve every project-owned file and generated project-local output exactly.
5. Do not delete any target file except audited `safe_delete` retired blueprint files.
6. Update or create `.gsd/blueprint.lock.json` only after all safe sync writes and safe deletes succeed.
7. Run lightweight verification.
8. Return the required final sync output.

## Sync Safety Rules

- Preserve all project-owned runtime files exactly.
- Never overwrite target `README.md`.
- Never overwrite existing `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/CONTEXT_INDEX.md`, milestones, phases, verification files, or generated `.codex` outputs.
- For `PROJECT.md` and `.planning/CODEBASE_MAP.md`, update only marked `GSD-BLUEPRINT` blocks and preserve all `GSD-PROJECT` content exactly.
- For `AGENTS.md`, update only the `GSD-BLUEPRINT: operating-contract` block and preserve the `GSD-PROJECT: local-settings` block exactly.
- Delete only concrete retired blueprint-owned files proven by the previous target lock and current manifest absence.
- After deleting safe retired files, remove now-empty parent directories only when the directory is inside a strict blueprint-owned namespace and became empty because of the safe deletes, such as a retired `.agents/skills/<skill-name>/` directory.
- Never remove non-empty directories.
- Never remove directories that may contain project-owned content.
- Stop on malformed markers, unknown ownership, legacy unmarked `AGENTS.md` migration, local managed-block edits requiring review, possible overwrite risk, or delete risk.
- If unsure, preserve and report.
- Do not write Obsidian vault memory.

## Retired Blueprint File Rules

- The current manifest defines the desired blueprint-owned inventory.
- The target lock proves what the blueprint previously installed.
- A file that appears in the old target lock but not in the current source manifest is a retired blueprint file candidate.
- Retired file cleanup applies only in `update` mode.
- Do not scan the target repository for extra files to delete. Cleanup is driven by the old lock, not by directory listing guesses.
- Safe retired cleanup may delete concrete old `blueprint_replace` files that are no longer in the manifest.
- If a retired file has been locally modified and the lock has enough checksum evidence to detect that, classify it as `delete_conflict` and preserve it.
- If checksum evidence is missing, delete only strict blueprint-owned paths where project ownership is not expected. Otherwise preserve and report `delete_conflict`.
- Do not delete managed-block host files or bootstrap/project surfaces just because their blueprint source changed or disappeared. For mixed files, remove or update only marked blocks when an explicit current manifest entry authorizes that behavior.
- Do not delete generated project-local files, project runtime files, user documents, or untracked target files that were never recorded in the lock.
- Record every safe delete and every preserved delete conflict in the final lock update.

## Lock File Rules

- Update `.gsd/blueprint.lock.json` only after successful writes and safe deletes.
- The lock should represent the current installed blueprint inventory after sync, not the old inventory.
- When writing lock entries, include enough data for future retirement safety when practical:
  - `path`
  - `owner`
  - `sync_strategy`
  - `last_action`
  - `content_sha256` or equivalent installed-content checksum for concrete files
  - `installed_at` or `updated_at`
- Do not store project secrets or absolute Obsidian vault paths in the lock.

## Managed Block Rules

- Replace only content between matching marker comments.
- Preserve all content outside markers.
- For `AGENTS.md`, the canonical managed block source is `.gsd/managed-blocks/agents-operating-contract.md`.
- If target `AGENTS.md` lacks the `GSD-BLUEPRINT: operating-contract` block, inspect for old unmarked GSD template or operating content before proposing any insertion.
- Legacy signals include `# Codex-Native GSD Template`, `## Operating Mode` with GSD planning references, `## Task Triage` with `$gsd-quick-task`, `## Milestone Workflow`, `## State Management`, `## Output Contract`, or old unmarked lists of GSD skill invocations.
- If legacy unmarked content is found, report `AGENTS.md legacy-template migration required` as a conflict or review-required item. Do not apply migration under this skill without a separate reviewed migration approval.
- If a target managed block has local edits compared with the previously installed lock or known source and those edits require review, stop and report the conflict.
- If markers are malformed, missing from an existing mixed file, or duplicated, stop and report the conflict.

## Verification Checks

Run lightweight checks after sync:

- manifest is valid JSON
- source manifest exists in the blueprint source path
- required blueprint source files exist
- required synced target files exist after sync
- target `README.md` was not created or modified
- existing `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/CONTEXT_INDEX.md`, milestones, phases, verification files, and generated `.codex` outputs were not modified
- only audited `safe_delete` retired blueprint files were deleted
- no protected project-owned file or directory was deleted
- `PROJECT.md` and `.planning/CODEBASE_MAP.md` project-owned blocks were preserved exactly when those files existed before sync
- `AGENTS.md` local settings block was preserved exactly when it existed before sync
- managed block markers are balanced in changed mixed files
- `.gsd/blueprint.lock.json` exists or was updated only after successful sync

## Required Final Sync Output

The final sync response must include:

- Files created
- Files replaced
- Files deleted
- Managed blocks updated
- Files preserved
- Files skipped
- Conflicts
- Lock file status
- Verification checks run
- Final disposition: `pass` | `fail` | `partial`

Use final dispositions as follows:

- `pass`: all safe sync actions applied and verification passed.
- `partial`: some safe sync actions applied, but conflicts or skipped review-required items remain.
- `fail`: a write or verification failed; report exactly what changed before failure and what remains unresolved.

## Completion Check

- The audit pass completed before any write.
- The exact confirmation prompt was shown before sync.
- Sync ran only after exact `sync approved`.
- Project-owned runtime files were preserved.
- Retired blueprint file deletion was limited to audited `safe_delete` files proven by the target lock and current manifest absence.
- Managed blocks changed only inside markers.
- The lock file changed only after successful sync writes and safe deletes.
- No Obsidian vault memory was written.
