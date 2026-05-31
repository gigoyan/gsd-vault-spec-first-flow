#!/usr/bin/env python3
"""Export a GSD project repository into a flat project-context package."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
EXPORT_SCHEMA_VERSION = 1
EXPORTER_VERSION = 2
COMPACTION_POLICY_VERSION = "1"
REDACTION_POLICY_VERSION = "1"
SINGLE_RAW_FILE_THRESHOLD = 40 * 1024
COMPACTED_OUTPUT_TARGET = 12 * 1024
VAULT_CONTEXT_TARGET = 20 * 1024
WORK_HISTORY_TARGET = 25 * 1024
EXPORT_SOFT_TARGET = 150 * 1024

BASE_FILES = {
    "project.md": "PROJECT.md",
    "requirements.md": ".planning/REQUIREMENTS.md",
    "project-idea-document.md": ".planning/PROJECT_IDEA_DOCUMENT.md",
    "technical-specification.md": ".planning/TECHNICAL_SPECIFICATION.md",
    "stack-selection.md": ".planning/STACK_SELECTION_AND_CONFIGURATION_PACKAGE.md",
    "codebase-map.md": ".planning/CODEBASE_MAP.md",
    "context-index.md": ".planning/CONTEXT_INDEX.md",
}
ROOT_RUNTIME_INSTRUCTION_FILES = {
    "agents.md": "AGENTS.md",
    "claude.md": "CLAUDE.md",
}
OPTIONAL_ROOT_RUNTIME_INSTRUCTION_FILES = {"claude.md"}
ALT_SOURCE_PATHS = {
    "project-idea-document.md": [
        ".planning/PROJECT_IDEA_DOCUMENT.md",
        ".planning/PROJECT_IDEA.md",
        ".planning/project-idea-document.md",
    ],
    "technical-specification.md": [
        ".planning/TECHNICAL_SPECIFICATION.md",
        ".planning/TECHNICAL_SPEC.md",
        ".planning/technical-specification.md",
    ],
    "stack-selection.md": [
        ".planning/STACK_SELECTION_AND_CONFIGURATION_PACKAGE.md",
        ".planning/STACK_SELECTION.md",
        ".planning/stack-selection-and-configuration-package.md",
    ],
}
PROFILE_FILES = {
    "minimal": [
        "index.md",
        *BASE_FILES.keys(),
        "state-snapshot.md",
        "vault-context.md",
        "source-index.json",
        "export-lock.json",
        "export-manifest.json",
        "checksums.sha256",
        "git-status.txt",
    ],
    "handoff": [
        "index.md",
        *BASE_FILES.keys(),
        "agents.md",
        "state-snapshot.md",
        "vault-context.md",
        "roadmap-summary.md",
        "tool-capabilities.md",
        "active-milestone.md",
        "active-phase.md",
        "latest-verification.md",
        "source-index.json",
        "export-lock.json",
        "export-manifest.json",
        "checksums.sha256",
        "git-status.txt",
    ],
    "full-context": [
        "index.md",
        *BASE_FILES.keys(),
        "agents.md",
        "state-snapshot.md",
        "vault-context.md",
        "roadmap-summary.md",
        "tool-capabilities.md",
        "active-milestone.md",
        "active-phase.md",
        "latest-verification.md",
        "work-history-summary.md",
        "source-index.json",
        "export-lock.json",
        "export-manifest.json",
        "checksums.sha256",
        "git-status.txt",
    ],
    "raw-plus-summary": [
        "index.md",
        *BASE_FILES.keys(),
        "agents.md",
        "state-snapshot.md",
        "vault-context.md",
        "roadmap-summary.md",
        "tool-capabilities.md",
        "active-milestone.md",
        "active-phase.md",
        "latest-verification.md",
        "work-history-summary.md",
        "raw-state.md",
        "raw-roadmap.md",
        "raw-active-milestone.md",
        "raw-active-phase.md",
        "raw-latest-verification.md",
        "source-index.json",
        "export-lock.json",
        "export-manifest.json",
        "checksums.sha256",
        "git-status.txt",
    ],
}
METADATA_FILES = {"index.md", "source-index.json", "export-lock.json", "export-manifest.json", "checksums.sha256", "git-status.txt"}
SKIP_DIRS = {
    ".git",
    ".codex",
    ".claude",
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".cache",
    "__pycache__",
}
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)\b(authorization:\s*bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret|client_secret)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
    re.compile(r"(?i)\b[A-Za-z][A-Za-z0-9+.-]*://[^:\s]+:[^@\s]+@[^)\]\s]+"),
    re.compile(r"\b(sk|pk|ghp|gho|xox[baprs])_[A-Za-z0-9_-]{16,}\b"),
]
KNOWN_FAKE_SECRET_STRINGS = [
    "abcdefghijklmnop",
    "test_secret_123456789",
    "abcdefghijklmnopqrstuvwxyz",
    "postgres://user:password@example.com/db",
]


@dataclass
class Source:
    source_id: str
    source_type: str
    path: str
    absolute_path: Path
    content: str
    digest: str
    included_in: set[str] = field(default_factory=set)
    status: str = "included"


@dataclass
class GitInfo:
    branch: str = "Unknown"
    commit: str = "Unknown"
    dirty: bool | str = "Unknown"
    status_short: str = "Unknown"


@dataclass
class StatePointers:
    current_status: str = "Unknown"
    current_milestone: str = "Unknown"
    milestone_status: str = "Unknown"
    current_phase: str = "Unknown"
    phase_status: str = "Unknown"
    latest_verification: str = "Unknown"
    next_action: str = "Unknown"
    blockers: str = "Unknown"
    open_risks: str = "Unknown"
    mapping_refresh: str = "Unknown"
    durable_memory: str = "Unknown"
    unresolved: list[str] = field(default_factory=list)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a GSD project context package.")
    parser.add_argument("--profile", choices=sorted(PROFILE_FILES), default="handoff")
    parser.add_argument("--mode", choices=("full", "incremental", "incremental-strict"), default="incremental")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root")
    parser.add_argument("--vault-root")
    parser.add_argument("--include-vault", choices=("auto", "yes", "no"), default="auto")
    parser.add_argument("--redact-secrets", choices=("true", "false"), default="true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-against-full-render", action="store_true")
    parser.add_argument("--include-dirty", action="store_true")
    parser.add_argument("--snapshot", action="store_true")
    return parser.parse_args(argv)


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def write_text(path: Path, content: str) -> None:
    path.write_text(content.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8", newline="\n")


def repo_relative(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root).as_posix()


def safe_repo_path(repo_root: Path, relative_path: str) -> Path | None:
    candidate = (repo_root / relative_path).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        return None
    if candidate.is_symlink() or not candidate.is_file():
        return None
    if any(part in SKIP_DIRS for part in candidate.relative_to(repo_root).parts):
        return None
    return candidate


def source_from_repo(repo_root: Path, relative_path: str, sources: dict[str, Source]) -> Source | None:
    path = safe_repo_path(repo_root, relative_path)
    if path is None:
        return None
    rel = repo_relative(repo_root, path)
    source_id = f"repo:{rel}"
    if source_id not in sources:
        content = read_text(path)
        sources[source_id] = Source(source_id, "repo_file", rel, path, content, sha256_text(content))
    return sources[source_id]


def source_from_vault(vault_project_root: Path, vault_project_id: str, path: Path, sources: dict[str, Source]) -> Source | None:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(vault_project_root.resolve()).as_posix()
    except ValueError:
        return None
    if resolved.is_symlink() or not resolved.is_file() or resolved.suffix.lower() != ".md":
        return None
    vault_rel = f"projects/{vault_project_id}/{relative}"
    source_id = f"vault:{vault_rel}"
    if source_id not in sources:
        content = read_text(resolved)
        sources[source_id] = Source(source_id, "vault_note", vault_rel, resolved, content, sha256_text(content))
    return sources[source_id]


def run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo_root, text=True, capture_output=True, check=False)


def collect_git_info(repo_root: Path) -> GitInfo:
    try:
        inside = run_git(repo_root, ["rev-parse", "--is-inside-work-tree"])
    except OSError:
        return GitInfo()
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return GitInfo()
    branch = run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    commit = run_git(repo_root, ["rev-parse", "--short", "HEAD"])
    status = run_git(repo_root, ["status", "--porcelain"])
    return GitInfo(
        branch=branch.stdout.strip() if branch.returncode == 0 and branch.stdout.strip() else "Unknown",
        commit=commit.stdout.strip() if commit.returncode == 0 and commit.stdout.strip() else "Unknown",
        dirty=bool(status.stdout.strip()) if status.returncode == 0 else "Unknown",
        status_short=status.stdout if status.returncode == 0 else "Unknown",
    )


def extract_bullet(content: str, label: str) -> str:
    patterns = [
        rf"(?im)^\s*-\s*{re.escape(label)}\s*:\s*(.+?)\s*$",
        rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            value = match.group(1).strip().strip("`")
            return value or "Unknown"
    return "Unknown"


def extract_section(content: str, heading: str) -> str:
    match = re.search(rf"(?ims)^##+\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##+\s+|\Z)", content)
    if not match:
        return "Unknown"
    body = match.group(1).strip()
    return body if body else "Unknown"


def extract_any_section(content: str, headings: list[str]) -> str:
    for heading in headings:
        value = extract_section(content, heading)
        if value != "Unknown":
            return value
    return "Unknown"


def one_line(value: str, limit: int = 220) -> str:
    if value == "Unknown":
        return value
    text = re.sub(r"\s+", " ", value).strip()
    return text[:limit].rstrip() + ("..." if len(text) > limit else "")


def table_rows(content: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(cells)
    return rows


def normalize_history_value(value: str) -> str:
    if value == "Unknown":
        return value
    return value.replace("\n", "\n  ")


def first_path_like(value: str) -> str:
    if value == "Unknown":
        return value
    ticked = re.search(r"`([^`]+)`", value)
    if ticked:
        return ticked.group(1)
    md_path = re.search(r"(\.planning/[A-Za-z0-9_./ -]+\.md)", value)
    if md_path:
        return md_path.group(1).strip()
    return value.strip()


def resolve_state(repo_root: Path, sources: dict[str, Source]) -> StatePointers:
    state = source_from_repo(repo_root, ".planning/STATE.md", sources)
    if state is None:
        return StatePointers(unresolved=["missing .planning/STATE.md"])
    content = state.content
    pointers = StatePointers(
        current_status=extract_bullet(content, "Status"),
        current_milestone=first_path_like(extract_bullet(content, "Current milestone")),
        milestone_status=extract_bullet(content, "Milestone status"),
        current_phase=first_path_like(extract_bullet(content, "Current phase")),
        phase_status=extract_bullet(content, "Phase status"),
        latest_verification=first_path_like(extract_bullet(content, "Latest verification")),
        next_action=extract_section(content, "Next Action"),
        blockers=extract_section(content, "Blockers"),
        open_risks=extract_section(content, "Open Risks"),
        mapping_refresh=extract_bullet(content, "Refresh follow-up"),
        durable_memory=extract_bullet(content, "Durable-memory follow-up"),
    )
    for label, value in (
        ("active milestone", pointers.current_milestone),
        ("active phase", pointers.current_phase),
        ("latest verification", pointers.latest_verification),
    ):
        if value == "Unknown" or value.lower() in {"none", "`none`"}:
            pointers.unresolved.append(f"{label}: Unknown")
            continue
        if source_from_repo(repo_root, value, sources) is None:
            pointers.unresolved.append(f"{label}: unresolved {value}")
    return pointers


def resolve_project_file(repo_root: Path, target_file: str, sources: dict[str, Source]) -> Source | None:
    candidates = ALT_SOURCE_PATHS.get(target_file, [BASE_FILES[target_file]])
    for candidate in candidates:
        source = source_from_repo(repo_root, candidate, sources)
        if source is not None:
            return source
    return None


def resolve_root_runtime_instruction(repo_root: Path, target_file: str, sources: dict[str, Source]) -> Source | None:
    return source_from_repo(repo_root, ROOT_RUNTIME_INSTRUCTION_FILES[target_file], sources)


def discover_history(repo_root: Path, sources: dict[str, Source]) -> list[Source]:
    discovered: list[Source] = []
    for directory in (".planning/milestones", ".planning/phases", ".planning/verification"):
        root = repo_root / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if path.is_symlink() or any(part in SKIP_DIRS for part in path.relative_to(repo_root).parts):
                continue
            source = source_from_repo(repo_root, repo_relative(repo_root, path), sources)
            if source is not None:
                discovered.append(source)
    return discovered


def resolve_vault_project_id(repo_root: Path, state: Source | None, project: Source | None) -> str:
    for source in (project, state):
        if source is None:
            continue
        match = re.search(r"(?im)GSD Vault Project ID:\s*`?([^`\n]+)`?", source.content)
        if match and match.group(1).strip() and "<" not in match.group(1):
            return match.group(1).strip()
        match = re.search(r"(?im)projects/([^/\s`]+)/", source.content)
        if match:
            return match.group(1).strip()
    return repo_root.name


def relevant_keywords(*contents: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "status", "phase",
        "milestone", "current", "latest", "verification", "planning", "unknown",
    }
    keywords: set[str] = set()
    for content in contents:
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", content.lower()):
            if word not in stop:
                keywords.add(word)
    return keywords


def referenced_vault_paths(vault_project_id: str, *contents: str) -> set[str]:
    refs: set[str] = set()
    for content in contents:
        for match in re.finditer(rf"projects/{re.escape(vault_project_id)}/([^\]\)\s`]+?\.md)", content):
            refs.add(match.group(1).replace("\\", "/"))
        for match in re.finditer(r"(?:vault:)?(knowledge/[^\]\)\s`]+?\.md|atlas/[^\]\)\s`]+?\.md|00-home/[^\]\)\s`]+?\.md)", content):
            refs.add(match.group(1).replace("\\", "/"))
    return refs


def vault_note_score(relative: str, content: str, keywords: set[str], direct_refs: set[str]) -> int:
    rel = relative.lower().replace("\\", "/")
    if relative in direct_refs:
        return 1000
    if rel == "00-home/current priorities.md":
        return 900
    atlas_order = {
        "atlas/project architecture.md": 800,
        "atlas/tech stack.md": 790,
        "atlas/database.md": 780,
        "atlas/deployment.md": 770,
    }
    if rel in atlas_order:
        return atlas_order[rel]
    haystack = f"{rel}\n{content[:2000].lower()}"
    hits = sum(1 for keyword in keywords if keyword in haystack)
    if hits <= 0:
        return 0
    base = 500 if rel.startswith("knowledge/") else 100
    return base + min(hits, 20)


def discover_vault_sources(
    vault_root: Path,
    vault_project_id: str,
    sources: dict[str, Source],
    context_contents: list[str] | None = None,
) -> tuple[list[Source], list[str]]:
    project_root = (vault_root / "projects" / vault_project_id).resolve()
    try:
        project_root.relative_to(vault_root.resolve())
    except ValueError:
        fail("resolved vault project path escapes vault root")
    if not project_root.is_dir():
        return [], [f"vault namespace not found: projects/{vault_project_id}/"]
    selected: list[Source] = []
    context_contents = context_contents or []
    keywords = relevant_keywords(*context_contents)
    direct_refs = referenced_vault_paths(vault_project_id, *context_contents)
    exact = [
        "00-home/current priorities.md",
        "atlas/project architecture.md",
        "atlas/tech stack.md",
        "atlas/database.md",
        "atlas/deployment.md",
    ]
    for relative in exact:
        source = source_from_vault(project_root, vault_project_id, project_root / relative, sources)
        if source:
            selected.append(source)
    scored: list[tuple[int, str, Path]] = []
    for relative in direct_refs:
        candidate = project_root / relative
        if candidate.is_file():
            scored.append((1000, relative, candidate))
    for relative_dir in [
        "knowledge/decisions",
        "knowledge/debugging",
        "knowledge/patterns",
        "knowledge/integrations",
        "knowledge/business",
    ]:
        directory = project_root / relative_dir
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            try:
                relative = path.resolve().relative_to(project_root).as_posix()
            except ValueError:
                continue
            if path.is_symlink() or not path.is_file():
                continue
            score = vault_note_score(relative, read_text(path), keywords, direct_refs)
            if score > 0:
                scored.append((score, relative, path))
    seen = {source.path for source in selected}
    for _score, _relative, path in sorted(scored, key=lambda item: (-item[0], item[1]))[:20]:
        source = source_from_vault(project_root, vault_project_id, path, sources)
        if source and source.path not in seen:
            selected.append(source)
            seen.add(source.path)
    return selected, []


def compact_source(source: Source | None, target_file: str, title: str, max_chars: int = COMPACTED_OUTPUT_TARGET) -> str:
    if source is None:
        return f"# {title}\n\nUnknown\n\n[source: Unknown]\n"
    source.included_in.add(target_file)
    content = source.content.strip()
    if len(content.encode("utf-8")) > SINGLE_RAW_FILE_THRESHOLD:
        content = "\n\n".join(line for line in content.splitlines() if line.startswith("#") or line.startswith("- "))[:max_chars]
    if len(content) > max_chars:
        content = content[:max_chars].rstrip() + "\n\n[Truncated deterministically by exporter]"
    return f"# {title}\n\n{content}\n\n[source: {source.path}]\n"


def render_state_snapshot(repo_root: Path, git: GitInfo, state: Source | None, pointers: StatePointers, target_file: str) -> str:
    if state:
        state.included_in.add(target_file)
    source_ref = ".planning/STATE.md#Current Status" if state else "Unknown"
    touched_areas = extract_section(state.content, "Touched Areas") if state else "Unknown"
    checks_run = extract_section(state.content, "Checks Run") if state else "Unknown"
    context_routing = extract_section(state.content, "Context Index") if state else "Unknown"
    return f"""# State Snapshot

## Repository
- Source repository: {repo_root} [source: {source_ref}]
- Git branch: {git.branch} [source: git]
- Git commit: {git.commit} [source: git]
- Git dirty: {git.dirty} [source: git]

## Current Status
- Status: {pointers.current_status} [source: {source_ref}]
- Milestone status: {pointers.milestone_status} [source: {source_ref}]
- Phase status: {pointers.phase_status} [source: {source_ref}]

## Active Milestone
- Path: {pointers.current_milestone} [source: {source_ref}]

## Active Phase
- Path: {pointers.current_phase} [source: {source_ref}]

## Latest Verification
- Path: {pointers.latest_verification} [source: {source_ref}]

## Next Action
{pointers.next_action}

[source: .planning/STATE.md#Next Action]

## Blockers
{pointers.blockers}

[source: .planning/STATE.md#Blockers]

## Open Risks
{pointers.open_risks}

[source: .planning/STATE.md#Open Risks]

## Touched Areas
{touched_areas}

[source: .planning/STATE.md#Touched Areas]

## Checks Run
{checks_run}

[source: .planning/STATE.md#Checks Run]

## Context Routing
- Context index: {context_routing} [source: .planning/STATE.md#Context Index]
- Mapping refresh candidates: {pointers.mapping_refresh} [source: {source_ref}]

## Durable-Memory Follow-Up
- Follow-up: {pointers.durable_memory} [source: {source_ref}]

## Unknowns
{chr(10).join(f"- {item}" for item in pointers.unresolved) if pointers.unresolved else "- None recorded"}
"""


def section_or_unknown(source: Source | None, headings: list[str]) -> str:
    if source is None:
        return "Unknown"
    return extract_any_section(source.content, headings)


def bullet_or_section(source: Source | None, label: str, headings: list[str] | None = None) -> str:
    if source is None:
        return "Unknown"
    value = extract_bullet(source.content, label)
    if value != "Unknown":
        return value
    return extract_any_section(source.content, headings or [label])


def source_reference(source: Source | None, anchor: str) -> str:
    return f"{source.path}#{anchor}" if source else "Unknown"


def render_roadmap(roadmap: Source | None, target_file: str) -> str:
    if roadmap:
        roadmap.included_in.add(target_file)
    current_pointer = section_or_unknown(roadmap, ["Current Pointer", "Current Status"])
    milestones = section_or_unknown(roadmap, ["Milestones"])
    statuses = "Unknown"
    dependencies = "Unknown"
    rows = table_rows(roadmap.content) if roadmap else []
    if rows:
        statuses = "; ".join(" | ".join(row[:3]) for row in rows[1:10]) or "Unknown"
        dependencies = "; ".join(row[-1] for row in rows[1:10] if row[-1]) or "Unknown"
    next_route = section_or_unknown(roadmap, ["Next Planned Route", "Next Action", "Routing Note"])
    return f"""# Roadmap Summary

## Current Pointer
{current_pointer}

[source: {source_reference(roadmap, "Current Pointer")}]

## Milestones
{milestones}

[source: {source_reference(roadmap, "Milestones")}]

## Statuses
{statuses}

[source: {source_reference(roadmap, "Milestones")}]

## Dependencies
{dependencies}

[source: {source_reference(roadmap, "Milestones")}]

## Next Planned Route
{next_route}

[source: {source_reference(roadmap, "Next Planned Route")}]

## Source References
- {roadmap.path if roadmap else "Unknown"}

## Unknowns
- {"None recorded" if roadmap else "Missing .planning/ROADMAP.md"}
"""


def render_active_milestone(source: Source | None, active_phase_path: str, target_file: str) -> str:
    if source:
        source.included_in.add(target_file)
    title = next((line.lstrip("# ").strip() for line in source.content.splitlines() if line.startswith("#")), "Unknown") if source else "Unknown"
    return f"""# Active Milestone

## Milestone Title/Path
- Title: {title}
- Path: {source.path if source else "Unknown"}

## Goal
{section_or_unknown(source, ["Goal"])}

## In Scope
{section_or_unknown(source, ["In Scope"])}

## Out Of Scope
{section_or_unknown(source, ["Out Of Scope", "Out of Scope"])}

## Acceptance Criteria
{section_or_unknown(source, ["Acceptance Criteria"])}

## Planned Phases And Statuses
{section_or_unknown(source, ["Planned Phases", "Phase Details"])}

## Active Phase Relationship
- Active phase: {active_phase_path}

## Risks
{section_or_unknown(source, ["Risks", "Open Risks"])}

## Validation Strategy
{section_or_unknown(source, ["Validation Strategy"])}

## Source References
- [source: {source.path if source else "Unknown"}]

## Unknowns
- {"None recorded" if source else "Active milestone unresolved"}
"""


def render_active_phase(source: Source | None, target_file: str) -> str:
    if source:
        source.included_in.add(target_file)
    title = next((line.lstrip("# ").strip() for line in source.content.splitlines() if line.startswith("#")), "Unknown") if source else "Unknown"
    return f"""# Active Phase

## Phase Title/Path
- Title: {title}
- Path: {source.path if source else "Unknown"}

## Objective
{section_or_unknown(source, ["Objective"])}

## Behavior Slice
{section_or_unknown(source, ["Behavior Slice"])}

## Touched Areas
{section_or_unknown(source, ["Touched Areas"])}

## Context Routing
{section_or_unknown(source, ["Context Routing"])}

## Test-First Validation
{section_or_unknown(source, ["Test-First Validation", "Validation"])}

## Done Criteria
{section_or_unknown(source, ["Done Criteria"])}

## Post-Verification Routing
{section_or_unknown(source, ["Post-Verification Routing"])}

## Source References
- [source: {source.path if source else "Unknown"}]

## Unknowns
- {"None recorded" if source else "Active phase unresolved"}
"""


def render_latest_verification(source: Source | None, target_file: str) -> str:
    if source:
        source.included_in.add(target_file)
    disposition = extract_any_section(source.content, ["Disposition", "Orchestrator Status"]) if source else "Unknown"
    return f"""# Latest Verification

## Verification Path
- Path: {source.path if source else "Unknown"}

## Disposition
{disposition}

## Criteria Checked
{section_or_unknown(source, ["Checks Run", "Evidence"])}

## Checks Run
{section_or_unknown(source, ["Checks Run"])}

## Pass/Fail/Partial Evidence
{section_or_unknown(source, ["Results", "Evidence", "Residual Risks"])}

## Unresolved Issues
{section_or_unknown(source, ["Residual Risks", "Open Risks"])}

## Next Recommended Action
{section_or_unknown(source, ["Orchestrator Status", "Next Action"])}

## Source References
- [source: {source.path if source else "Unknown"}]

## Unknowns
- {"None recorded" if source else "Latest verification unresolved"}
"""


def render_work_history(history: list[Source], target_file: str) -> str:
    parts = ["# Work History Summary\n"]
    if not history:
        return "# Work History Summary\n\nUnknown\n\n[source: Unknown]\n"
    for source in history:
        source.included_in.add(target_file)
        title = next((line.lstrip("# ").strip() for line in source.content.splitlines() if line.startswith("#")), source.path)
        status = extract_bullet(source.content, "Status")
        disposition = extract_bullet(source.content, "Disposition")
        if disposition != "Unknown":
            status = disposition
        goal = extract_bullet(source.content, "Goal")
        if goal == "Unknown":
            goal = extract_bullet(source.content, "Objective")
        completed_phases = extract_completed_phases(source)
        failed_partial_phases = extract_failed_partial_phases(source)
        verification_results = extract_verification_results(source)
        touched = extract_any_section(source.content, ["Touched Areas", "Files reviewed", "Files modified"])
        decisions = extract_any_section(source.content, ["Important Decisions", "Implementation Notes", "Assumptions", "Results"])
        risks = extract_any_section(source.content, ["Residual Risks", "Open Risks", "Risks"])
        parts.append(
            f"## {title}\n\n"
            f"- Status: {status} [source: {source.path}]\n"
            f"- Goal: {goal} [source: {source.path}]\n"
            f"- Completed phases: {normalize_history_value(completed_phases)} [source: {source.path}]\n"
            f"- Failed/partial phases: {normalize_history_value(failed_partial_phases)} [source: {source.path}]\n"
            f"- Verification results: {normalize_history_value(verification_results)} [source: {source.path}]\n"
            f"- Key files/areas touched: {normalize_history_value(touched)} [source: {source.path}]\n"
            f"- Important decisions: {normalize_history_value(decisions)} [source: {source.path}]\n"
            f"- Remaining risks: {normalize_history_value(risks)} [source: {source.path}]\n"
            f"- Source references: {source.path}\n"
        )
        if len("\n".join(parts)) > WORK_HISTORY_TARGET:
            parts.append("\n[Truncated deterministically by exporter]\n")
            break
    return "\n".join(parts)


def extract_completed_phases(source: Source) -> str:
    values: list[str] = []
    for cells in table_rows(source.content):
        if len(cells) >= 3 and cells[0].lower() not in {"phase", "name"}:
            status = " ".join(cells).lower()
            if "complete" in status or "pass" in status:
                values.append(" | ".join(cells))
    if values:
        return "; ".join(values)
    matches = re.findall(r"(?im)^\s*-\s*(.+?(?:completed|passed|implemented).+)$", source.content)
    return "; ".join(match.strip() for match in matches[:6]) or "Unknown"


def extract_failed_partial_phases(source: Source) -> str:
    values: list[str] = []
    for cells in table_rows(source.content):
        if len(cells) >= 3 and cells[0].lower() not in {"phase", "name"}:
            status = " ".join(cells).lower()
            if any(word in status for word in ("partial", "failed", "blocked")):
                values.append(" | ".join(cells))
    matches = re.findall(r"(?im)^\s*-\s*(.+?(?:partial|failed|blocked|blocking gap).+)$", source.content)
    values.extend(match.strip() for match in matches[:6])
    return "; ".join(dict.fromkeys(values)) or "Unknown"


def extract_verification_results(source: Source) -> str:
    if "/verification/" in source.path.replace("\\", "/"):
        results = extract_any_section(source.content, ["Results", "Disposition", "Orchestrator Status"])
        if results != "Unknown":
            return one_line(results, 500)
    phase_status = extract_bullet(source.content, "Phase Status")
    milestone_status = extract_bullet(source.content, "Milestone Status")
    if phase_status != "Unknown" or milestone_status != "Unknown":
        return f"Phase Status: {phase_status}; Milestone Status: {milestone_status}"
    return "Unknown"


def render_vault_context(vault_sources: list[Source], vault_included: bool, reason: str, target_file: str) -> str:
    if not vault_included:
        return f"""# Vault Context

## Current Priorities
Unknown / not included because {reason}.

## Architecture Facts
Unknown

## Stack Facts
Unknown

## Database/Data Facts
Unknown

## Deployment/Ops Facts
Unknown

## Integration Behavior
Unknown

## Durable Decisions
Unknown

## Debugging Insights
Unknown

## Reusable Patterns
Unknown

## Business/Domain Rules
Unknown

## Conflicts/Unknowns
Unknown

## Source Note References
[source: Unknown]
"""
    parts = ["# Vault Context\n"]
    headings = [
        "Current Priorities",
        "Architecture Facts",
        "Stack Facts",
        "Database/Data Facts",
        "Deployment/Ops Facts",
        "Integration Behavior",
        "Durable Decisions",
        "Debugging Insights",
        "Reusable Patterns",
        "Business/Domain Rules",
        "Conflicts/Unknowns",
    ]
    for heading in headings:
        parts.append(f"## {heading}\n")
        matched = False
        for source in vault_sources:
            bucket = source.path.lower()
            if (
                (heading == "Current Priorities" and "current priorities" in bucket)
                or (heading == "Architecture Facts" and "architecture" in bucket)
                or (heading == "Stack Facts" and "tech stack" in bucket)
                or (heading == "Database/Data Facts" and "database" in bucket)
                or (heading == "Deployment/Ops Facts" and "deployment" in bucket)
                or (heading == "Integration Behavior" and "integrations" in bucket)
                or (heading == "Durable Decisions" and "decisions" in bucket)
                or (heading == "Debugging Insights" and "debugging" in bucket)
                or (heading == "Reusable Patterns" and "patterns" in bucket)
                or (heading == "Business/Domain Rules" and "business" in bucket)
            ):
                source.included_in.add(target_file)
                snippet = source.content.strip()[:1500] or "Unknown"
                parts.append(f"{snippet}\n\n[source: vault:{source.path}]\n")
                matched = True
        if not matched:
            parts.append("Unknown\n\n[source: Unknown]\n")
    parts.append("## Source Note References\n")
    parts.extend(f"- vault:{source.path}" for source in vault_sources)
    text = "\n".join(parts)
    if len(text) > VAULT_CONTEXT_TARGET:
        return text[:VAULT_CONTEXT_TARGET].rstrip() + "\n\n[Truncated deterministically by exporter]\n"
    return text


def raw_output(source: Source | None, target_file: str, fallback_title: str) -> str:
    if source is None:
        return f"# {fallback_title}\n\nUnknown\n\n[source: Unknown]\n"
    source.included_in.add(target_file)
    return (
        f"<!-- GSD-PROJECT-CONTEXT-EXPORT:SOURCE {source.path} -->\n"
        f"<!-- source_hash: {source.digest} -->\n\n"
        f"{source.content}\n"
    )


def redact_content(content: str, enabled: bool) -> tuple[str, int]:
    if not enabled:
        return content, 0
    matches = 0
    redacted = content
    for pattern in SECRET_PATTERNS:
        def repl(match: re.Match[str]) -> str:
            nonlocal matches
            matches += 1
            if match.lastindex:
                return f"{match.group(1)}[REDACTED_SECRET]"
            return "[REDACTED_SECRET]"
        redacted = pattern.sub(repl, redacted)
    return redacted, matches


def runtime_metadata(sources: dict[str, Source]) -> dict[str, Any]:
    included_paths = {source.path for source in sources.values() if source.included_in}
    runtime_surfaces = {
        "codex": "AGENTS.md" in included_paths,
        "claude_code": "CLAUDE.md" in included_paths,
        "generated_codex": False,
        "generated_claude": False,
    }
    target_runtimes = [
        runtime
        for runtime in ("codex", "claude_code")
        if runtime_surfaces[runtime]
    ]
    return {
        "target_runtimes": target_runtimes,
        "runtime_surfaces_included": runtime_surfaces,
        "runtime_exclusion_policy": "Generated project-local runtime adapter outputs such as .codex/** and generated .claude/** are excluded by default.",
    }


def source_index(profile: str, version: int, sources: dict[str, Source], claims: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = runtime_metadata(sources)
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": profile,
        "profile_version": version,
        "target_runtimes": metadata["target_runtimes"],
        "runtime_surfaces_included": metadata["runtime_surfaces_included"],
        "runtime_exclusion_policy": metadata["runtime_exclusion_policy"],
        "sources": [
            {
                "source_id": source.source_id,
                "source_type": source.source_type,
                "path": source.path,
                "hash": source.digest,
                "included_in": sorted(source.included_in),
                "status": source.status,
            }
            for source in sorted(sources.values(), key=lambda item: item.source_id)
        ],
        "claims": claims,
    }


def content_fingerprint(outputs: dict[str, str], sources: dict[str, Source], vault_project_id: str, profile: str) -> str:
    payload = {
        "profile": profile,
        "exporter_version": EXPORTER_VERSION,
        "compaction_policy_version": COMPACTION_POLICY_VERSION,
        "redaction_policy_version": REDACTION_POLICY_VERSION,
        "outputs": {name: sha256_text(content) for name, content in sorted(outputs.items()) if name not in METADATA_FILES},
        "vault_project_id": vault_project_id,
    }
    return sha256_text(json.dumps(payload, sort_keys=True))


def previous_source_hashes(lock: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(lock, dict):
        return {}
    hashes: dict[str, str] = {}
    for output in lock.get("outputs", []):
        if not isinstance(output, dict):
            continue
        for path, digest in output.get("source_hashes", {}).items():
            if isinstance(path, str) and isinstance(digest, str):
                hashes[path] = digest
    return hashes


def previous_outputs_by_target(lock: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(lock, dict):
        return {}
    return {
        item.get("target_file"): item
        for item in lock.get("outputs", [])
        if isinstance(item, dict) and isinstance(item.get("target_file"), str)
    }


def source_changes(previous: dict[str, str], sources: dict[str, Source]) -> tuple[list[str], list[str], list[str]]:
    current = {source.path: source.digest for source in sources.values()}
    changed = sorted(path for path in current.keys() & previous.keys() if current[path] != previous[path])
    added = sorted(path for path in current.keys() - previous.keys())
    removed = sorted(path for path in previous.keys() - current.keys())
    return changed, added, removed


def output_source_hashes(name: str, sources: dict[str, Source]) -> dict[str, str]:
    return {source.path: source.digest for source in sources.values() if name in source.included_in}


def safe_reuse_target(previous: dict[str, Any], output_root: Path, target_file: str, current_hashes: dict[str, str]) -> tuple[bool, str]:
    if target_file in METADATA_FILES:
        return False, "metadata is always regenerated"
    previous_by_target = previous_outputs_by_target(previous)
    entry = previous_by_target.get(target_file)
    if not isinstance(entry, dict):
        return False, f"missing previous lock entry for {target_file}"
    target = output_root / target_file
    if not target.is_file():
        return False, f"previous output missing: {target_file}"
    current_file_hash = sha256_text(read_text(target))
    if entry.get("rendered_hash") != current_file_hash:
        return False, f"previous output hash mismatch: {target_file}"
    if entry.get("source_hashes", {}) != current_hashes:
        return False, f"source hashes changed for {target_file}"
    if entry.get("compaction_policy_hash") != sha256_text(COMPACTION_POLICY_VERSION):
        return False, f"compaction policy changed for {target_file}"
    if entry.get("redaction_policy_hash") != sha256_text(REDACTION_POLICY_VERSION):
        return False, f"redaction policy changed for {target_file}"
    if previous.get("exporter_version") != EXPORTER_VERSION:
        return False, f"exporter version changed for {target_file}"
    return True, "reused from previous profile output"


def redact_outputs(outputs: dict[str, str], enabled: bool) -> tuple[dict[str, str], dict[str, Any]]:
    redacted_outputs: dict[str, str] = {}
    total_matches = 0
    redacted_names: list[str] = []
    for name, content in outputs.items():
        redacted, matches = redact_content(content, enabled)
        redacted_outputs[name] = redacted
        if matches:
            total_matches += matches
            redacted_names.append(name)
    return redacted_outputs, {
        "enabled": enabled,
        "matches": total_matches,
        "manual_review_required": total_matches > 0,
        "redacted_outputs": sorted(redacted_names),
    }


def merge_redaction_status(*statuses: dict[str, Any]) -> dict[str, Any]:
    enabled = any(bool(status.get("enabled")) for status in statuses)
    matches = sum(int(status.get("matches", 0)) for status in statuses)
    names: set[str] = set()
    for status in statuses:
        for name in status.get("redacted_outputs", []):
            if isinstance(name, str):
                names.add(name)
    return {
        "enabled": enabled,
        "matches": matches,
        "manual_review_required": matches > 0,
        "redacted_outputs": sorted(names),
    }


def build_lock_outputs(outputs: dict[str, str], sources: dict[str, Source]) -> list[dict[str, Any]]:
    return [
        {
            "target_file": name,
            "output_kind": "raw" if name.startswith("raw-") else "metadata" if name in METADATA_FILES else "compacted",
            "source_paths": sorted(source.path for source in sources.values() if name in source.included_in),
            "source_hashes": output_source_hashes(name, sources),
            "rendered_hash": sha256_text(content),
            "compaction_policy_hash": sha256_text(COMPACTION_POLICY_VERSION),
            "redaction_policy_hash": sha256_text(REDACTION_POLICY_VERSION),
            "source_references": sorted(source.path for source in sources.values() if name in source.included_in),
        }
        for name, content in sorted(outputs.items())
    ]


def rendered_changed_outputs(outputs: dict[str, str], previous_lock: dict[str, Any] | None) -> list[str]:
    previous_by_target = previous_outputs_by_target(previous_lock)
    changed: list[str] = []
    for name, content in sorted(outputs.items()):
        if name in METADATA_FILES:
            continue
        entry = previous_by_target.get(name)
        if not isinstance(entry, dict) or entry.get("rendered_hash") != sha256_text(content):
            changed.append(name)
    return changed


def changed_sources_without_output_change(
    changed_sources: list[str],
    sources: dict[str, Source],
    changed_nonmetadata_outputs: list[str],
) -> list[str]:
    changed_output_names = set(changed_nonmetadata_outputs)
    current_sources = {source.path: source for source in sources.values()}
    unchanged_sources: list[str] = []
    for path in changed_sources:
        source = current_sources.get(path)
        included_outputs = set()
        if source is not None:
            included_outputs = {name for name in source.included_in if name not in METADATA_FILES}
        if not included_outputs or not (included_outputs & changed_output_names):
            unchanged_sources.append(path)
    return sorted(unchanged_sources)


def compact_claim_text(value: str, limit: int = 180) -> str:
    if value == "Unknown":
        return value
    sanitized, _matches = redact_content(one_line(value, limit), True)
    if len(sanitized) > limit:
        sanitized = sanitized[:limit].rstrip() + "..."
    return sanitized or "Unknown"


def vault_claim_label(source: Source) -> str:
    title = next((line.lstrip("# ").strip() for line in source.content.splitlines() if line.startswith("#")), "")
    if title:
        return f"{Path(source.path).name}: {title}"
    first_line = next((line.strip("- ").strip() for line in source.content.splitlines() if line.strip()), "")
    if first_line:
        return f"{Path(source.path).name}: {first_line}"
    return Path(source.path).name


def claim(claim_id: str, target_file: str, value: str, source: Source | None, anchor: str) -> dict[str, Any]:
    source_id = source.source_id if source else "Unknown"
    return {
        "claim_id": claim_id,
        "target_file": target_file,
        "claim": compact_claim_text(value),
        "source_refs": [{"source_id": source_id, "anchor": anchor}],
    }


def build_claims(
    profile: str,
    pointers: StatePointers,
    state_source: Source | None,
    roadmap_source: Source | None,
    milestone_source: Source | None,
    phase_source: Source | None,
    verification_source: Source | None,
    vault_sources: list[Source],
    history: list[Source],
) -> list[dict[str, Any]]:
    claims = [
        claim("state.current_status", "state-snapshot.md", pointers.current_status, state_source, "Current Status"),
        claim("state.current_milestone", "state-snapshot.md", pointers.current_milestone, state_source, "Current Milestone"),
        claim("state.current_phase", "state-snapshot.md", pointers.current_phase, state_source, "Current Phase"),
        claim("state.latest_verification", "state-snapshot.md", pointers.latest_verification, state_source, "Latest Verification"),
        claim("state.touched_areas", "state-snapshot.md", extract_section(state_source.content, "Touched Areas") if state_source else "Unknown", state_source, "Touched Areas"),
        claim("state.checks_run", "state-snapshot.md", extract_section(state_source.content, "Checks Run") if state_source else "Unknown", state_source, "Checks Run"),
        claim("state.open_risks", "state-snapshot.md", pointers.open_risks, state_source, "Open Risks"),
        claim("state.next_action", "state-snapshot.md", pointers.next_action, state_source, "Next Action"),
    ]
    if roadmap_source is not None and profile in {"handoff", "full-context", "raw-plus-summary"}:
        claims.append(claim("roadmap.current_route", "roadmap-summary.md", extract_any_section(roadmap_source.content, ["Current Route", "Current Status", "Milestones"]), roadmap_source, "Roadmap Summary"))
    if milestone_source is not None and profile in {"handoff", "full-context", "raw-plus-summary"}:
        claims.append(claim("active_milestone.goal", "active-milestone.md", extract_bullet(milestone_source.content, "Goal"), milestone_source, "Goal"))
        claims.append(claim("active_milestone.acceptance", "active-milestone.md", extract_section(milestone_source.content, "Acceptance Criteria"), milestone_source, "Acceptance Criteria"))
    if phase_source is not None and profile in {"handoff", "full-context", "raw-plus-summary"}:
        claims.append(claim("active_phase.objective", "active-phase.md", extract_bullet(phase_source.content, "Objective"), phase_source, "Objective"))
        claims.append(claim("active_phase.done_criteria", "active-phase.md", extract_section(phase_source.content, "Done Criteria"), phase_source, "Done Criteria"))
    if verification_source is not None and profile in {"handoff", "full-context", "raw-plus-summary"}:
        claims.append(claim("latest_verification.results", "latest-verification.md", extract_any_section(verification_source.content, ["Results", "Disposition", "Orchestrator Status"]), verification_source, "Results"))
    for index, source in enumerate(vault_sources[:10], start=1):
        claims.append(claim(f"vault.selected_note.{index:03d}", "vault-context.md", vault_claim_label(source), source, "Vault selected note"))
    if profile in {"full-context", "raw-plus-summary"}:
        for index, source in enumerate(history[:20], start=1):
            target = "work-history-summary.md"
            claims.append(claim(f"work_history.{index:03d}.status", target, extract_bullet(source.content, "Status"), source, "Status"))
            claims.append(claim(f"work_history.{index:03d}.risk", target, extract_any_section(source.content, ["Residual Risks", "Open Risks", "Risks"]), source, "Risks"))
            verification = extract_verification_results(source)
            if verification != "Unknown":
                claims.append(claim(f"work_history.{index:03d}.verification", target, verification, source, "Verification Results"))
    return claims


def load_previous_lock(output_root: Path) -> dict[str, Any] | None:
    path = output_root / "export-lock.json"
    if not path.is_file():
        return None
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError:
        return {"_invalid": True}


def validate_previous_lock(lock: dict[str, Any] | None, profile: str, output_root: Path) -> list[str]:
    errors: list[str] = []
    if lock is None:
        errors.append("missing export-lock.json")
        return errors
    if lock.get("_invalid"):
        errors.append("invalid export-lock.json")
        return errors
    if lock.get("profile") != profile:
        errors.append("previous lock profile does not match selected profile")
    if lock.get("schema_version") != SCHEMA_VERSION or lock.get("export_schema_version") != EXPORT_SCHEMA_VERSION:
        errors.append("previous lock schema does not match")
    for output in lock.get("outputs", []):
        target = output_root / output.get("target_file", "")
        if not target.is_file():
            errors.append(f"previous output missing: {output.get('target_file')}")
            continue
        if output.get("target_file") not in METADATA_FILES and output.get("rendered_hash") != sha256_text(read_text(target)):
            errors.append(f"previous output hash mismatch: {output.get('target_file')}")
    return errors


def build_index(
    profile: str,
    profile_version: int,
    generated_at: str,
    repo_root: Path,
    git: GitInfo,
    vault_included: bool,
    vault_project_id: str,
    files: list[str],
    missing: list[str],
    unresolved: list[str],
    redaction_enabled: bool,
) -> str:
    best_use = {
        "minimal": "small ChatGPT Project source package",
        "handoff": "agent-runtime continuation package",
        "full-context": "larger agent-runtime discussion and onboarding package",
        "raw-plus-summary": "audit/debug traceability",
    }[profile]
    upload = ", ".join(name for name in files if name.endswith(".md") or name.endswith(".json"))
    return f"""# GSD Project Context Export

- Export profile: {profile}
- Profile version: {profile_version}
- Export schema version: {EXPORT_SCHEMA_VERSION}
- Generated at: {generated_at}
- Source repository: {repo_root}
- Git branch: {git.branch}
- Git commit: {git.commit}
- Git dirty: {git.dirty}
- Vault included: {vault_included}
- Vault project ID: {vault_project_id}
- Best use: {best_use}
- Files included: {", ".join(files)}
- Missing optional inputs: {", ".join(missing) if missing else "None"}
- Unresolved pointers: {", ".join(unresolved) if unresolved else "None"}
- Redaction status: {"enabled" if redaction_enabled else "disabled"}
- Runtime adapter outputs: generated `.codex/**` and generated `.claude/**` outputs are excluded by default; root runtime instruction surfaces are included only where this profile supports them.
- How to use this package: upload the profile files relevant to your target context. Suggested upload set: {upload}
"""


def build_outputs(args: argparse.Namespace, repo_root: Path, output_root: Path) -> tuple[dict[str, str], dict[str, Source], dict[str, Any], dict[str, Any], dict[str, Any], list[str], list[str], str, bool]:
    sources: dict[str, Source] = {}
    git = collect_git_info(repo_root)
    state_source = source_from_repo(repo_root, ".planning/STATE.md", sources)
    project_source = source_from_repo(repo_root, "PROJECT.md", sources)
    pointers = resolve_state(repo_root, sources)
    vault_project_id = resolve_vault_project_id(repo_root, state_source, project_source)

    missing: list[str] = []
    outputs: dict[str, str] = {}
    for target in BASE_FILES:
        source = resolve_project_file(repo_root, target, sources)
        if source is None:
            missing.append(BASE_FILES[target])
        outputs[target] = compact_source(source, target, target.removesuffix(".md").replace("-", " ").title())

    if args.profile in {"handoff", "full-context", "raw-plus-summary"}:
        for target in ROOT_RUNTIME_INSTRUCTION_FILES:
            source = resolve_root_runtime_instruction(repo_root, target, sources)
            if source is None:
                if target not in OPTIONAL_ROOT_RUNTIME_INSTRUCTION_FILES:
                    missing.append(ROOT_RUNTIME_INSTRUCTION_FILES[target])
                continue
            outputs[target] = compact_source(source, target, target.removesuffix(".md").replace("-", " ").title())

    state_source = source_from_repo(repo_root, ".planning/STATE.md", sources)
    roadmap_source = source_from_repo(repo_root, ".planning/ROADMAP.md", sources)
    tool_source = source_from_repo(repo_root, ".planning/tool-capabilities.md", sources)
    milestone_source = source_from_repo(repo_root, pointers.current_milestone, sources) if pointers.current_milestone != "Unknown" else None
    phase_source = source_from_repo(repo_root, pointers.current_phase, sources) if pointers.current_phase != "Unknown" else None
    verification_source = source_from_repo(repo_root, pointers.latest_verification, sources) if pointers.latest_verification != "Unknown" else None
    if tool_source is None and args.profile in {"handoff", "full-context", "raw-plus-summary"}:
        missing.append(".planning/tool-capabilities.md")

    vault_sources: list[Source] = []
    vault_included = False
    vault_reason = "vault root was unavailable"
    vault_errors: list[str] = []
    if args.include_vault == "no":
        vault_reason = "vault inclusion was disabled"
    else:
        vault_root_value = args.vault_root or os.environ.get("GSD_VAULT_ROOT")
        if vault_root_value:
            vault_root = Path(vault_root_value).expanduser().resolve()
            if not vault_root.is_dir():
                if args.include_vault == "yes":
                    fail(f"--vault-root is not a directory: {vault_root}")
                vault_reason = "vault root was unavailable"
            else:
                context_contents = [
                    source.content
                    for source in (state_source, project_source, roadmap_source, milestone_source, phase_source, verification_source)
                    if source is not None
                ]
                vault_sources, vault_errors = discover_vault_sources(vault_root, vault_project_id, sources, context_contents)
                vault_included = not vault_errors
                vault_reason = "; ".join(vault_errors) if vault_errors else "included"
        elif args.include_vault == "yes":
            fail("--include-vault yes requires --vault-root or GSD_VAULT_ROOT")

    if vault_errors and args.include_vault == "yes":
        fail("; ".join(vault_errors))

    outputs["state-snapshot.md"] = render_state_snapshot(repo_root, git, state_source, pointers, "state-snapshot.md")
    outputs["vault-context.md"] = render_vault_context(vault_sources, vault_included, vault_reason, "vault-context.md")
    if args.profile in {"handoff", "full-context", "raw-plus-summary"}:
        outputs["roadmap-summary.md"] = render_roadmap(roadmap_source, "roadmap-summary.md")
        outputs["tool-capabilities.md"] = compact_source(tool_source, "tool-capabilities.md", "Tool Capabilities")
        outputs["active-milestone.md"] = render_active_milestone(milestone_source, pointers.current_phase, "active-milestone.md")
        outputs["active-phase.md"] = render_active_phase(phase_source, "active-phase.md")
        outputs["latest-verification.md"] = render_latest_verification(verification_source, "latest-verification.md")
    history = discover_history(repo_root, sources) if args.profile in {"full-context", "raw-plus-summary"} else []
    if args.profile in {"full-context", "raw-plus-summary"}:
        outputs["work-history-summary.md"] = render_work_history(history, "work-history-summary.md")
    if args.profile == "raw-plus-summary":
        outputs["raw-state.md"] = raw_output(state_source, "raw-state.md", "Raw State")
        outputs["raw-roadmap.md"] = raw_output(roadmap_source, "raw-roadmap.md", "Raw Roadmap")
        outputs["raw-active-milestone.md"] = raw_output(milestone_source, "raw-active-milestone.md", "Raw Active Milestone")
        outputs["raw-active-phase.md"] = raw_output(phase_source, "raw-active-phase.md", "Raw Active Phase")
        outputs["raw-latest-verification.md"] = raw_output(verification_source, "raw-latest-verification.md", "Raw Latest Verification")

    previous_lock = load_previous_lock(output_root)
    previous_errors = validate_previous_lock(previous_lock, args.profile, output_root)
    if args.mode == "incremental-strict" and previous_errors:
        fail("incremental-strict failed: " + "; ".join(previous_errors))
    first_run = previous_lock is None
    comparable_previous = isinstance(previous_lock, dict) and not previous_errors
    changed_sources, added_sources, removed_sources = source_changes(previous_source_hashes(previous_lock), sources)

    generated_at = now_iso()
    redaction_enabled = args.redact_secrets == "true"
    fallback_reasons = previous_errors if args.mode == "incremental" else []
    if args.mode in {"incremental", "incremental-strict"} and isinstance(previous_lock, dict) and not previous_errors:
        for name in sorted(outputs):
            can_reuse, reason = safe_reuse_target(previous_lock, output_root, name, output_source_hashes(name, sources))
            if can_reuse:
                outputs[name] = read_text(output_root / name)
            else:
                if args.mode == "incremental":
                    fallback_reasons.append(reason)

    redacted_nonmetadata, initial_redaction = redact_outputs(outputs, redaction_enabled)
    previous_fingerprint = previous_lock.get("content_fingerprint") if isinstance(previous_lock, dict) else None
    fingerprint = content_fingerprint(redacted_nonmetadata, sources, vault_project_id, args.profile)
    previous_version = int(previous_lock.get("profile_version", 0)) if isinstance(previous_lock, dict) and isinstance(previous_lock.get("profile_version"), int) else 0
    profile_version = previous_version if previous_fingerprint == fingerprint and previous_version > 0 else previous_version + 1
    content_changed_outputs = rendered_changed_outputs(redacted_nonmetadata, previous_lock) if comparable_previous else sorted(
        name for name in redacted_nonmetadata if name not in METADATA_FILES
    )
    if args.mode == "full" or not comparable_previous:
        nonmetadata_outputs_to_write = sorted(name for name in redacted_nonmetadata if name not in METADATA_FILES)
    else:
        nonmetadata_outputs_to_write = content_changed_outputs
    source_changes_without_output_changes = (
        changed_sources_without_output_change(changed_sources, sources, content_changed_outputs)
        if comparable_previous
        else []
    )

    claims = build_claims(
        args.profile,
        pointers,
        state_source,
        roadmap_source,
        milestone_source,
        phase_source,
        verification_source,
        vault_sources,
        history,
    )
    outputs = redacted_nonmetadata
    runtime = runtime_metadata(sources)
    outputs["source-index.json"] = json.dumps(source_index(args.profile, profile_version, sources, claims), indent=2) + "\n"
    outputs["git-status.txt"] = (
        f"current branch: {git.branch}\n"
        f"current commit: {git.commit}\n"
        f"git dirty: {git.dirty}\n"
        f"git status --short:\n{git.status_short}\n"
    )
    all_files = sorted([name for name in outputs] + ["export-lock.json", "export-manifest.json", "checksums.sha256"])
    outputs["index.md"] = build_index(
        args.profile,
        profile_version,
        generated_at,
        repo_root,
        git,
        vault_included,
        vault_project_id,
        all_files,
        missing,
        pointers.unresolved,
        redaction_enabled,
    )
    outputs, metadata_redaction = redact_outputs(outputs, redaction_enabled)
    redaction_status = merge_redaction_status(initial_redaction, metadata_redaction)

    metadata_changed = sorted(METADATA_FILES)
    changed_outputs = sorted(set(nonmetadata_outputs_to_write) | set(metadata_changed))
    if args.mode in {"incremental", "incremental-strict"} and comparable_previous:
        reused_outputs = sorted(name for name in redacted_nonmetadata if name not in METADATA_FILES and name not in nonmetadata_outputs_to_write)
    else:
        reused_outputs = []
    compacted_outputs = sorted(name for name in outputs if name.endswith(".md") and not name.startswith("raw-") and name not in METADATA_FILES)
    raw_outputs = sorted(name for name in outputs if name.startswith("raw-"))

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "exporter_version": EXPORTER_VERSION,
        "profile": args.profile,
        "profile_version": profile_version,
        "mode": args.mode,
        "first_run": first_run,
        "generated_at": generated_at,
        "repo_root": str(repo_root),
        "git_branch": git.branch,
        "git_commit": git.commit,
        "git_dirty": git.dirty,
        "vault_included": vault_included,
        "vault_project_id": vault_project_id,
        "target_runtimes": runtime["target_runtimes"],
        "runtime_surfaces_included": runtime["runtime_surfaces_included"],
        "runtime_exclusion_policy": runtime["runtime_exclusion_policy"],
        "generated_files": all_files,
        "changed_sources": changed_sources,
        "changed_sources_without_rendered_output_change": source_changes_without_output_changes,
        "added_sources": added_sources,
        "removed_sources": removed_sources,
        "changed_outputs": changed_outputs,
        "reused_outputs": reused_outputs,
        "regenerated_outputs": changed_outputs,
        "compacted_outputs": compacted_outputs,
        "raw_outputs": raw_outputs,
        "missing_optional_sources": sorted(set(missing)),
        "unresolved_pointers": pointers.unresolved,
        "incremental_fallback_occurred": bool(fallback_reasons and args.mode == "incremental"),
        "incremental_fallback_reasons": sorted(set(fallback_reasons)) if args.mode == "incremental" else [],
        "full_render_verification": None,
        "redaction": redaction_status,
        "vault_errors": vault_errors,
        "profile_soft_target_bytes": EXPORT_SOFT_TARGET,
    }
    lock = {
        "schema_version": SCHEMA_VERSION,
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "exporter_version": EXPORTER_VERSION,
        "profile": args.profile,
        "profile_version": profile_version,
        "generated_at": generated_at,
        "repo_root": str(repo_root),
        "repo_root_hash_id": sha256_text(str(repo_root)),
        "vault_project_id": vault_project_id,
        "git_branch": git.branch,
        "git_commit": git.commit,
        "git_dirty": git.dirty,
        "target_runtimes": runtime["target_runtimes"],
        "runtime_surfaces_included": runtime["runtime_surfaces_included"],
        "compaction_policy_version": COMPACTION_POLICY_VERSION,
        "redaction_policy_version": REDACTION_POLICY_VERSION,
        "content_fingerprint": fingerprint,
        "outputs": [],
    }
    outputs["export-lock.json"] = json.dumps(lock, indent=2) + "\n"
    outputs["export-manifest.json"] = json.dumps(manifest, indent=2) + "\n"
    outputs, final_redaction_status = redact_outputs(outputs, redaction_enabled)
    redaction_status = merge_redaction_status(redaction_status, final_redaction_status)
    manifest["redaction"] = redaction_status
    lock["outputs"] = build_lock_outputs(outputs, sources)
    outputs["export-lock.json"] = json.dumps(lock, indent=2) + "\n"
    outputs["export-manifest.json"] = json.dumps(manifest, indent=2) + "\n"
    outputs, final_redaction_status = redact_outputs(outputs, redaction_enabled)
    redaction_status = merge_redaction_status(redaction_status, final_redaction_status)
    manifest["redaction"] = redaction_status
    outputs["export-manifest.json"] = json.dumps(manifest, indent=2) + "\n"
    outputs["export-lock.json"] = json.dumps(lock, indent=2) + "\n"
    outputs, final_redaction_status = redact_outputs(outputs, redaction_enabled)
    redaction_status = merge_redaction_status(redaction_status, final_redaction_status)
    manifest["redaction"] = redaction_status
    outputs["export-manifest.json"] = json.dumps(manifest, indent=2) + "\n"
    outputs["checksums.sha256"] = "".join(
        f"{sha256_text(content)}  {name}\n" for name, content in sorted(outputs.items()) if name != "checksums.sha256"
    )
    return outputs, sources, lock, manifest, git.__dict__, missing, pointers.unresolved, vault_project_id, vault_included


def validate_export(output_root: Path, profile: str) -> list[str]:
    errors: list[str] = []
    if not output_root.is_dir():
        return [f"output root does not exist: {output_root}"]
    for child in output_root.iterdir():
        if child.is_dir():
            errors.append(f"output root contains subdirectory: {child.name}")
    required = PROFILE_FILES[profile]
    for name in required:
        if not (output_root / name).is_file():
            errors.append(f"missing required output: {name}")
    for name in ("source-index.json", "export-lock.json", "export-manifest.json"):
        try:
            data = json.loads(read_text(output_root / name))
        except Exception as exc:
            errors.append(f"{name} is not valid JSON: {exc}")
            continue
        if data.get("profile") != profile:
            errors.append(f"{name} profile mismatch")
    try:
        source_index_data = json.loads(read_text(output_root / "source-index.json"))
        if not source_index_data.get("claims"):
            errors.append("source-index.json has no claims")
        for item in source_index_data.get("claims", []):
            value = item.get("claim", "")
            if isinstance(value, str) and len(value) > 240:
                errors.append(f"source-index claim is too long: {item.get('claim_id')}")
            if isinstance(value, str) and "\n\n" in value:
                errors.append(f"source-index claim appears to contain raw body content: {item.get('claim_id')}")
    except Exception:
        pass
    checksums_path = output_root / "checksums.sha256"
    if checksums_path.is_file():
        seen = set()
        for line in read_text(checksums_path).splitlines():
            if not line.strip():
                continue
            digest, _, filename = line.partition("  ")
            seen.add(filename)
            target = output_root / filename
            if "/" in filename or "\\" in filename:
                errors.append(f"checksum includes non-root path: {filename}")
            elif not target.is_file():
                errors.append(f"checksum target missing: {filename}")
            elif sha256_text(read_text(target)) != digest:
                errors.append(f"checksum mismatch: {filename}")
        expected = {child.name for child in output_root.iterdir() if child.is_file() and child.name != "checksums.sha256"}
        if seen != expected:
            errors.append("checksums.sha256 does not cover exactly all root output files except itself")
    for child in output_root.iterdir():
        if child.is_file():
            content = read_text(child)
            for secret in KNOWN_FAKE_SECRET_STRINGS:
                if secret in content:
                    errors.append(f"known fake secret leaked into output: {child.name}")
    return errors


def write_outputs(output_root: Path, outputs: dict[str, str], snapshot: bool, write_names: set[str] | None = None) -> Path:
    target = output_root
    if snapshot:
        suffix = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = output_root.with_name(f"{output_root.name}-snapshot-{suffix}")
        write_names = None
    if target.exists() and not target.is_dir():
        fail(f"output root is not a directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    for child in target.iterdir():
        if child.is_dir():
            fail(f"refusing to write into non-root-only output root containing subdirectory: {child.name}")
    for name, content in outputs.items():
        if write_names is not None and name not in write_names:
            continue
        if "/" in name or "\\" in name:
            fail(f"internal error: non-root output file name: {name}")
        write_text(target / name, content)
    return target


def verify_against_full_render(args: argparse.Namespace, repo_root: Path, output_root: Path, outputs: dict[str, str]) -> dict[str, Any]:
    full_args = argparse.Namespace(**vars(args))
    full_args.mode = "full"
    full_outputs, *_ = build_outputs(full_args, repo_root, output_root)
    excluded = {"export-lock.json", "export-manifest.json", "checksums.sha256", "git-status.txt", "index.md"}
    compared = sorted((set(outputs) | set(full_outputs)) - excluded)
    mismatches = [
        name
        for name in compared
        if outputs.get(name) != full_outputs.get(name)
    ]
    return {
        "enabled": True,
        "passed": not mismatches,
        "compared_files": compared,
        "excluded_files": sorted(excluded),
        "mismatches": mismatches,
    }


def record_full_render_verification(outputs: dict[str, str], verification: dict[str, Any], redaction_enabled: bool) -> tuple[dict[str, str], dict[str, Any]]:
    manifest = json.loads(outputs["export-manifest.json"])
    existing_redaction = manifest.get("redaction", {})
    manifest["full_render_verification"] = verification
    outputs["export-manifest.json"] = json.dumps(manifest, indent=2) + "\n"
    outputs, redaction_status = redact_outputs(outputs, redaction_enabled)
    manifest = json.loads(outputs["export-manifest.json"])
    manifest["redaction"] = merge_redaction_status(existing_redaction, redaction_status)
    outputs["export-manifest.json"] = json.dumps(manifest, indent=2) + "\n"
    outputs, redaction_status = redact_outputs(outputs, redaction_enabled)
    manifest = json.loads(outputs["export-manifest.json"])
    manifest["redaction"] = merge_redaction_status(manifest.get("redaction", {}), redaction_status)
    outputs["export-manifest.json"] = json.dumps(manifest, indent=2) + "\n"
    outputs["checksums.sha256"] = "".join(
        f"{sha256_text(content)}  {name}\n" for name, content in sorted(outputs.items()) if name != "checksums.sha256"
    )
    return outputs, manifest


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    if not repo_root.is_dir():
        fail(f"--repo-root is not a directory: {repo_root}")
    output_root = Path(args.output_root).expanduser().resolve() if args.output_root else (repo_root / "exports" / args.profile).resolve()
    outputs, _sources, _lock, manifest, _git, _missing, _unresolved, _vault_project_id, _vault_included = build_outputs(args, repo_root, output_root)
    verification = None
    if args.verify_against_full_render:
        verification = verify_against_full_render(args, repo_root, output_root, outputs)
        if not verification["passed"]:
            fail("--verify-against-full-render failed: " + ", ".join(verification["mismatches"]))
        outputs, manifest = record_full_render_verification(outputs, verification, args.redact_secrets == "true")
    if args.dry_run:
        print(f"Planned profile: {args.profile}")
        print(f"Planned output root: {output_root}")
        print("Generated files:")
        for name in sorted(outputs):
            print(f"  - {name}")
        return 0
    write_names = None
    if args.mode in {"incremental", "incremental-strict"} and not args.snapshot:
        write_names = set(manifest.get("regenerated_outputs", []))
    final_root = write_outputs(output_root, outputs, args.snapshot, write_names)
    errors = validate_export(final_root, args.profile)
    if errors:
        for error in errors:
            print(f"validation error: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Export profile: {args.profile}")
    print(f"Profile version: {manifest['profile_version']}")
    print(f"Output root: {final_root}")
    print(f"Generated files: {len(outputs)}")
    print(f"Changed outputs: {len(manifest['changed_outputs'])}")
    print(f"Reused outputs: {len(manifest['reused_outputs'])}")
    print(f"Unresolved pointers: {len(manifest['unresolved_pointers'])}")
    print(f"Redaction matches: {manifest['redaction']['matches']}")
    if verification:
        print("Full-render verification: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
