# Document-First Intake Reference

Use this reference with `$gsd-new-project` when intake needs to choose the right starting point before milestone planning.

## Route-To-Surface Matrix
| Intake route | Start from | Populate first | Follow-up rule |
| --- | --- | --- | --- |
| `placeholder_bootstrap` | Placeholder `PROJECT.md` and `.planning/REQUIREMENTS.md` | Bootstrap charter and requirements | Ask only for missing bootstrap essentials after checking repo evidence. |
| `document_first_intake` | Registered source materials, supplied docs, notes, transcripts, briefs, or stable repo evidence | The highest artifact surfaces the material can already support: bootstrap docs first, then Project Idea Document or Technical Specification when the material is already specific enough | Register durable repository-local source materials before extraction when applicable. Extract before re-asking. Keep contradictions and gaps explicit. |
| `partial_maturity_continuation` | Existing project artifacts and supplied materials | The highest valid readiness artifact that is stale, incomplete, or missing | Continue forward from the strongest current artifact instead of restarting earlier work. |

## Source-Material Boundary
- Use `$gsd-register-source-materials` before document-first extraction when the user supplies repository-relative file or directory paths that should become project evidence.
- Registration owns classification, custody choice, provenance, registry rows, compact claim index entries, conflicts/unknowns, and consumption logging according to `.planning/templates/source-materials-contract.md`.
- Document-first intake owns extraction from registered material into the appropriate project/spec artifact. Extracted claims should cite `source_id` values and anchors when available instead of copying raw source bodies or recreating registry metadata.
- If a supplied note is transient conversational context and does not need durable registry treatment, intake may use it directly while still preserving `Confirmed`, `Suggested`, and `Unknown` statuses.

## Evidence Handling
- Mark inputs as `Confirmed` when directly supported by repo evidence, supplied material, or an explicit user decision.
- Mark inputs as `Suggested` when they are inferred, recommended, or useful prefills derived from evidence.
- Mark inputs as `Unknown` when the material is missing, contradictory, or not strong enough to confirm.
- Do not silently upgrade `Suggested` to `Confirmed`.

## Document-First Extraction Rules
1. Register user-supplied repository-relative files or directories through `$gsd-register-source-materials` when they should become project evidence.
2. Read registered materials, supplied transient context, and relevant repo evidence before asking bootstrap or readiness questions.
3. Extract facts into the correct artifact surface rather than dumping raw notes or registry rows into the nearest file.
4. Preserve contradictions inline so later planning can resolve them without losing source context; keep registry conflicts in the source-material registry.
5. Ask only focused residual questions that materially unblock the next artifact.
6. Use UI options whenever the residual question can be answered through structured choices, confirmations, or recommended combinations.

## Continuation Rules
- If `PROJECT.md` and `.planning/REQUIREMENTS.md` are usable but the Project Idea Document is missing, continue into the Project Idea Document instead of redoing bootstrap.
- If the Project Idea Document is current and the Technical Specification is partial, continue the Technical Specification from the existing content.
- If supplied material clearly answers an open field in an existing artifact, refresh that field from the material and keep the evidence status visible.
- Preserve the normal project-facing Spec-First readiness gate for later milestone planning and implementation.
