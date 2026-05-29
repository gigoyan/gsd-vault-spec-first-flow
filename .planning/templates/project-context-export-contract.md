# Project Context Export Contract

## Purpose
Defines the root-only project-context export package for GSD-enabled repositories.

## Non-Mutation Rule
The exporter reads source project files and vault notes but never mutates them.

## Root-Only Rule
Each export profile directory contains files only and no generated subdirectories.

## Profiles
- minimal
- handoff
- full-context
- raw-plus-summary

## Versioning
Each profile has independent `profile_version`, `export-lock.json`, `export-manifest.json`, `source-index.json`, and `checksums.sha256`.

## Incremental Rules
`full` renders current sources, `incremental` reuses unchanged non-metadata output files directly from the selected profile output root when the previous lock proves safe reuse, and `incremental-strict` fails when the selected profile lock or existing non-metadata outputs are missing or inconsistent. Incremental writes only changed or new non-metadata outputs plus regenerated metadata files. Metadata files are always regenerated.

## Manifest Clarity
`export-manifest.json` includes `changed_sources_without_rendered_output_change` for sources whose hashes changed while final redacted non-metadata outputs stayed byte-for-byte unchanged, explaining why `profile_version` may stay the same even when source hashes changed.

## Source Reference Rule
Every compacted claim must reference source paths or be marked `Unknown`. Claims in `source-index.json` must be short, sanitized labels or summaries; they must not contain full source or vault note bodies.

## Vault Rule
Only read the active project namespace. Never read sibling namespaces. Select current priorities, directly referenced notes, relevant atlas notes, and knowledge notes matching active-work keywords; do not perform broad vault dumps.

## Redaction Rule
Redact secrets by default after all Markdown and JSON outputs are serialized, then base hashes, locks, manifests, and checksums on final redacted content. Flag manual review when redactions occur.
