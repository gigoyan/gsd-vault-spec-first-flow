@AGENTS.md

<!-- GSD-BLUEPRINT:START claude-runtime-adapter -->
# Claude Code Runtime Adapter

Use GSD as the governing coding-agent workflow for this repository.

Claude Code project skills live under:

- `.claude/skills/**`

Claude Code project subagents live under:

- `.claude/agents/**`

Do not assume Codex-specific tool names or runtime behavior exist.

When a GSD instruction refers to delegated-agent behavior, apply the runtime-neutral rule: delegated children must not spawn, delegate to, message, wait for, close, or orchestrate other agents.

Read full adapter rules when the task requires runtime-specific detail:

- `.planning/templates/agent-runtime-adapter-contract.md`
<!-- GSD-BLUEPRINT:END claude-runtime-adapter -->

<!-- GSD-PROJECT:START claude-local-settings -->
## Project-Local Claude Code Notes

- Add project-specific Claude Code guidance here.
<!-- GSD-PROJECT:END claude-local-settings -->
