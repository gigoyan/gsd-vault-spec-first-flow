---
name: gsd-export-project-context
description: export a specific gsd-enabled project repository into a root-only portable context package for chatgpt project sources, codex or agent handoff, compacted workflow state, mapped repository understanding, selected work history, and scoped vault memory. use when the user needs project-specific context export, handoff, compacted state/history, vault-aware context packaging, or incremental project context updates.
---

# GSD Export Project Context

Export a specific GSD-enabled project repository into a compact, root-only context package for ChatGPT Project sources, Codex or agent handoff, workflow continuation, scoped work history, and selected vault memory.

## Source Of Truth

- The selected project repository is the source of truth.
- The exporter reads source project files and selected vault notes, but never mutates them.
- Previous profile locks are only incremental caches and version references. They never replace current source inspection.
- Each export profile has an independent output root, lock, manifest, source index, checksum file, and `profile_version`.

## Profiles

- `minimal`: small ChatGPT Project source package with core project, mapping, state, and vault context.
- `handoff`: default Codex/agent continuation package with active milestone, active phase, latest verification, roadmap, and tool-capability context.
- `full-context`: larger continuation package with milestone-grouped work history.
- `raw-plus-summary`: audit/debug package with summaries plus selected raw source files flattened into the profile output root.

## CLI

Run the bundled script:

```bash
python .agents/skills/gsd-export-project-context/scripts/export_project_context.py --profile handoff --mode incremental --repo-root <project-repo-root> --output-root <profile-output-root>
```

Supported options:

- `--profile minimal|handoff|full-context|raw-plus-summary`
- `--mode full|incremental|incremental-strict`
- `--repo-root <path>`
- `--output-root <path>`
- `--vault-root <path>`
- `--include-vault auto|yes|no`
- `--redact-secrets true|false`
- `--dry-run`
- `--verify-against-full-render`
- `--include-dirty`
- `--snapshot`

Defaults:

- `--profile handoff`
- `--mode incremental`
- `--repo-root .`
- `--include-vault auto`
- `--redact-secrets true`
- `--output-root exports/<profile>` under the selected repository when omitted

## Workflow

1. Read `.planning/tool-capabilities.md` if present before running commands.
2. Identify the target `repo-root`.
3. Select the export profile.
4. Select the export mode.
5. Resolve the profile output root.
6. Inspect the source inventory.
7. Resolve active workflow state, active milestone, active phase, and latest verification where possible.
8. Resolve the vault namespace if vault inclusion is enabled.
9. Run `--dry-run` first when changing exporter logic or when the user asks for preview.
10. Run the exporter.
11. Review generated `index.md`, `source-index.json`, `export-lock.json`, `export-manifest.json`, `checksums.sha256`, and `git-status.txt`.
12. Validate that the profile output root contains files only and no subdirectories.
13. Report the profile, profile version, output root, generated files, changed/reused outputs, unresolved pointers, redaction status, and next recommended action.

## Output Contract

- Each profile output root contains files only.
- No generated subdirectories are allowed inside a profile output root.
- `source-index.json` maps sources and compacted claims to source references.
- `export-lock.json` records profile-specific hashes and version state.
- `export-manifest.json` records run metadata, source changes, changed sources that did not change rendered outputs, generated files, unresolved pointers, redaction status, and incremental behavior.
- `checksums.sha256` covers every root-level output file except itself.
- `git-status.txt` records branch, commit, dirty state, and `git status --short` when Git is available.

## Incremental Rules

- `full`: render current sources and write a fresh profile lock and manifest. The previous lock may be used only to preserve `profile_version` when the rendered source fingerprint is unchanged.
- `incremental`: use `export-lock.json` in the selected profile output root when present. If it is missing, create the first export and record `first_run: true`.
- `incremental-strict`: fail if the selected profile lock is missing, invalid, wrong profile, wrong schema, or inconsistent with existing output files.
- Non-metadata outputs are reused directly from the selected profile output root only when the previous lock proves the output file hash, source hashes, policy hashes, and exporter version are unchanged.
- Metadata files are always regenerated.
- `incremental` writes only changed non-metadata outputs, new outputs, and regenerated metadata files; reused non-metadata outputs are not rewritten.
- `profile_version` increments only when final redacted non-metadata rendered content changes.
- `changed_sources_without_rendered_output_change` lists changed source files whose final redacted non-metadata rendered outputs stayed unchanged.
- Metadata-only run updates do not force `profile_version` to increment.

## Vault Rules

- `auto`: include vault context only when a usable project namespace is found and a vault root is provided or safely resolved.
- `yes`: fail clearly when the vault namespace or vault root cannot be resolved.
- `no`: do not read vault files.
- Read only `projects/<vault-project-id>/` under the vault root.
- Never read sibling vault namespaces and never write vault files.

## Redaction Rules

- Redaction is enabled by default.
- Redact API keys, tokens, passwords, private keys, connection strings, production credentials, payment secrets, authorization headers, and `.env`-like assignments across every serialized output.
- Replacement text is `[REDACTED_SECRET]`.
- Do not print detected secrets in logs, locks, or manifests.
- If redactions occur, `export-manifest.json` marks `manual_review_required: true`.
- Redaction runs after Markdown and JSON outputs are serialized; hashes, lock entries, checksums, and manifests are based on final redacted content.

## Completion Check

- The exporter runs with Python standard library only.
- The selected profile output root is root-only.
- Full and incremental handoff exports work on safe fixtures.
- A no-change incremental run preserves `profile_version`.
- A changed source increments `profile_version`.
- Profile locks, manifests, source indexes, checksums, and git metadata are profile-specific.
- Vault inclusion is project-scoped.
- Redaction runs by default.
- Validation catches broken outputs before reporting success.
