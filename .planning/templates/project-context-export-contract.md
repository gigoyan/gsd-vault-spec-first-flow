# Project Context Export Contract

## Purpose
Defines the root-only project-context export package for GSD-enabled repositories.

## Non-Mutation Rule
The exporter reads source project files and vault notes but never mutates them.

## Root-Only Rule
Each export profile directory contains files only and no generated subdirectories.

## Profiles
- minimal
- handoff: agent-runtime handoff / agent-runtime continuation package
- full-context: agent-runtime continuation package with broader work history
- raw-plus-summary

## Runtime Adapter Boundaries
- Generated project-local runtime adapter outputs are excluded by default.
- Excluded generated runtime outputs include `.codex/**` and generated `.claude/**`, including `.claude/settings.json`, `.claude/agents/**`, `.claude/skills/**`, `.claude/rules/**`, and `.claude/hooks/**`.
- Root `CLAUDE.md` is a project-facing runtime adapter surface, not generated `.claude/**`.
- Include root `CLAUDE.md` only in handoff, full-context, and raw-plus-summary profiles when present; do not include generated `.claude/**` by default.
- `source-index.json` and `export-manifest.json` should record runtime-neutral metadata such as `target_runtimes` and `runtime_surfaces_included` when the exporter supports it.

## Versioning
Each profile has independent `profile_version`, `export-lock.json`, `export-manifest.json`, `source-index.json`, and `checksums.sha256`.

## Incremental Rules
`full` renders current sources, `incremental` reuses unchanged non-metadata output files directly from the selected profile output root when the previous lock proves safe reuse, and `incremental-strict` fails when the selected profile lock or existing non-metadata outputs are missing or inconsistent. Incremental writes only changed or new non-metadata outputs plus regenerated metadata files. Metadata files are always regenerated.

## Manifest Clarity
`export-manifest.json` includes `changed_sources_without_rendered_output_change` for sources whose hashes changed while final redacted non-metadata outputs stayed byte-for-byte unchanged, explaining why `profile_version` may stay the same even when source hashes changed.

## Source Reference Rule
Every compacted claim must reference source paths or be marked `Unknown`. Claims in `source-index.json` must be short, sanitized labels or summaries; they must not contain full source or vault note bodies.

## Source Materials Rule
When `.planning/source-materials/SOURCE_MATERIALS.md` exists, exports may include compact registry status, sanitized material labels, evidence status, and traceability references in `source-index.json`. Do not copy raw source bodies, sensitive material, large binaries, licensed content, `.planning/source-materials/materials/**`, or `.planning/source-materials/extracts/**` into project-context export outputs.

## Vault Rule
Only read the active project namespace. Never read sibling namespaces. Select current priorities, directly referenced notes, relevant atlas notes, and knowledge notes matching active-work keywords; do not perform broad vault dumps.

## Redaction Rule
Redact secrets by default after all Markdown and JSON outputs are serialized, then base hashes, locks, manifests, and checksums on final redacted content. Flag manual review when redactions occur.
