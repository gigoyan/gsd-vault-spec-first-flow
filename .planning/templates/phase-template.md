# Phase Template

## Objective
- What does this phase need to achieve?
- If the phase depends on prior durable context, note whether a narrow memory lookup is needed before implementation.
- State the concrete execution result expected at the end of this phase.

## Behavior Slice
- What coherent behavior or scenario set is being changed?
- Name the exact spec, workflow, or implementation slice this phase is responsible for.

## Steps
1. Read `.planning/CONTEXT_INDEX.md` when available and identify the narrowest relevant routing row or module card before scanning source files.
2. Define the minimum sufficient validation set for this behavior slice.
3. Update the required tests or checks first when practical.
4. Implement the minimum change needed to satisfy that validation.
5. If the phase produces durable insight, leave a session-save candidate for later instead of writing memory here.

## Constraints
- C1:
- C2:

## Touched Areas
- Paths:
- Expected change type:
- Note any files that must stay untouched unless the phase is explicitly replanned.

## Context Routing
- Context index consulted:
- Routing row used:
- Module card used:
- Start-here paths:
- Inspect-next paths:
- Usually-changes paths:
- Avoid-unless-needed paths:
- Validation path:
- Refresh needed before execution: `yes` | `no` | `unknown`

## Implementation Notes
- Concrete instructions, examples, or sequencing notes needed to execute this phase without guesswork.
- When the phase relies on repo evidence, recommendations, or unresolved gaps, keep them labeled as `Confirmed`, `Suggested`, or `Unknown` instead of flattening them into certainty.

## Test-First Validation
- Primary test level:
- Minimum sufficient pre-implementation validation set:
- First decisive failing test or check:
- Additional tests or checks required for this slice:
- Failing-first expectation or exception note:
- Broader phase checks:
- Memory-aware context pack:

## Done Criteria
- DC1: The minimum sufficient validation set exists for the changed behavior before implementation, or an exception is justified with the nearest safeguard.
- DC2: Implementation is the minimum needed to satisfy the selected validation set and broader required checks.
- DC3: Validated behavior satisfies the milestone acceptance criteria with evidence suitable for verification.
- DC4: Phase-specific constraints and implementation notes were respected, or any necessary deviation was escalated for replanning.
- DC5: Context routing was followed or a justified exception was recorded when the context index was missing, stale, or insufficient.

## Post-Verification Routing
- In standalone verification, if this phase passes and the milestone is incomplete, the next action should be `$gsd-plan-milestone` for the next bounded phase in the same milestone.
- In `$gsd-run-milestone`, the root orchestrator may assign a verification-and-next-phase-planning child that verifies this phase and, after a pass with an incomplete milestone, creates the next bounded phase in the same delegated step.
- The `$gsd-run-milestone` composite child must not create a next phase on `fail`, `partial`, blocked verification, or completed milestone, and must not execute the newly created phase.
- Keep the phase repo-local unless the next phase explicitly needs a memory lookup or later session-save follow-up.
