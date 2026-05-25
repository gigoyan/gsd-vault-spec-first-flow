#!/usr/bin/env python3
"""Export the reusable GSD blueprint into a compact package."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXPORT_SCHEMA_VERSION = 1
EXPORT_RENDERER_VERSION = 1
DEFAULT_OUTPUT_ROOT = ".gsd/exports"
MANIFEST_RELATIVE_PATH = ".gsd/blueprint-manifest.json"
SKILL_ROOT = ".agents/skills"
TEMPLATE_ROOT = ".planning/templates"
STACK_PROFILE_ROOT = ".agents/stack-profiles"
SECTION_START_PREFIX = "<!-- GSD-EXPORT-SECTION:START "
SECTION_END_PREFIX = "<!-- GSD-EXPORT-SECTION:END "
METADATA_FILES = [
    "index.md",
    "export-lock.json",
    "export-manifest.json",
    "checksums.sha256",
    "git-status.txt",
]
BASE_CONTENT_FILES = [
    "skills.md",
    "templates.md",
    "agents.md",
    "project.md",
]
ROOT_COPY_TARGETS = {
    "AGENTS.md": "agents.md",
    "PROJECT.md": "project.md",
}
KNOWN_STACK_PROFILE_DOMAINS = {
    "backend",
    "frontend",
    "data",
    "auth",
    "integration",
    "hosting",
    "observability",
}
RUNTIME_PLANNING_EXACT = {
    ".planning/STATE.md",
    ".planning/ROADMAP.md",
    ".planning/CONTEXT_INDEX.md",
}
RUNTIME_PLANNING_PREFIXES = (
    ".planning/milestones/",
    ".planning/phases/",
    ".planning/verification/",
    ".planning/archive/",
)


@dataclass(frozen=True)
class GitInfo:
    available: bool
    branch: str
    commit: str
    status_short: str
    dirty: bool
    note: str | None = None


@dataclass(frozen=True)
class Section:
    group: str
    section_id: str
    source_path: str
    source_hash: str
    rendered: str
    rendered_hash: str
    content_mode: str
    start_marker: str
    end_marker: str


@dataclass(frozen=True)
class CopyOutput:
    source_path: str
    target_file: str
    source_hash: str
    content: str


@dataclass(frozen=True)
class RenderPlan:
    content_by_file: dict[str, str]
    consolidated_outputs: dict[str, list[Section]]
    copy_outputs: dict[str, CopyOutput]
    skill_sources: list[str]
    template_sources: list[str]
    stack_profile_sources_by_target: dict[str, list[str]]
    copied_root_files: list[dict[str, str]]
    skipped: list[dict[str, str]]
    warnings: list[str]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the reusable GSD blueprint into a compact package."
    )
    parser.add_argument(
        "--repo-root",
        help="Repository root. Defaults to the nearest parent containing .gsd/blueprint-manifest.json.",
    )
    parser.add_argument(
        "--output-root",
        default=DEFAULT_OUTPUT_ROOT,
        help="Output root. Relative paths are resolved against the repository root.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned export without writing files.",
    )
    parser.add_argument(
        "--include-dirty",
        action="store_true",
        help="Acknowledge dirty exports explicitly. Dirty exports are allowed and marked by default.",
    )
    parser.add_argument(
        "--mode",
        choices=("full", "incremental", "incremental-strict"),
        default="full",
        help="Export mode. Full is the canonical correctness path.",
    )
    parser.add_argument(
        "--base-export",
        help="Previous export directory to use as the cache base for incremental modes.",
    )
    parser.add_argument(
        "--verify-against-full-render",
        action="store_true",
        help="After incremental rendering, compare reusable output content against a fresh full render.",
    )
    return parser.parse_args(argv)


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / MANIFEST_RELATIVE_PATH).is_file():
            return candidate

    fail(
        "could not find repository root; pass --repo-root pointing to a directory "
        "that contains .gsd/blueprint-manifest.json"
    )


def resolve_repo_root(value: str | None) -> Path:
    if value:
        root = Path(value).expanduser().resolve()
        if not root.is_dir():
            fail(f"--repo-root is not a directory: {root}")
        if not (root / MANIFEST_RELATIVE_PATH).is_file():
            fail(f"manifest not found under --repo-root: {root / MANIFEST_RELATIVE_PATH}")
        return root

    return find_repo_root(Path.cwd())


def resolve_output_root(repo_root: Path, value: str) -> Path:
    output_root = Path(value).expanduser()
    if not output_root.is_absolute():
        output_root = repo_root / output_root
    return output_root.resolve()


def resolve_base_export(value: str | None, mode: str) -> Path | None:
    if mode == "full":
        return None
    if not value:
        fail("--base-export is required for incremental modes; automatic base detection is not implemented")
    base_export = Path(value).expanduser().resolve()
    if not base_export.is_dir():
        fail(f"--base-export is not a directory: {base_export}")
    return base_export


def load_manifest_bytes(repo_root: Path) -> bytes:
    manifest_path = repo_root / MANIFEST_RELATIVE_PATH
    try:
        return manifest_path.read_bytes()
    except OSError as exc:
        fail(f"could not read manifest {manifest_path}: {exc}")


def load_manifest(repo_root: Path) -> tuple[dict[str, Any], str]:
    raw = load_manifest_bytes(repo_root)
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"manifest is not valid UTF-8 JSON: {exc}")

    files = manifest.get("files")
    if not isinstance(files, list):
        fail("manifest schema is unsupported: expected top-level 'files' array")

    return manifest, sha256_bytes(raw)


def manifest_version(manifest: dict[str, Any]) -> str:
    value = manifest.get("blueprint_version", manifest.get("version", "unversioned"))
    if value is None or str(value).strip() == "":
        return "unversioned"
    return str(value)


def sanitize_component(value: str) -> str:
    value = value.strip() or "unknown"
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._")
    return value or "unknown"


def run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )


def collect_git_info(repo_root: Path) -> GitInfo:
    try:
        inside = run_git(repo_root, ["rev-parse", "--is-inside-work-tree"])
    except OSError as exc:
        return GitInfo(False, "nogit", "nogit", "", False, f"git unavailable: {exc}")

    if inside.returncode != 0 or inside.stdout.strip() != "true":
        note = inside.stderr.strip() or inside.stdout.strip() or "not a git repository"
        return GitInfo(False, "nogit", "nogit", "", False, note)

    branch_result = run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    commit_result = run_git(repo_root, ["rev-parse", "--short", "HEAD"])
    status_result = run_git(repo_root, ["status", "--porcelain"])

    if branch_result.returncode != 0 or commit_result.returncode != 0 or status_result.returncode != 0:
        note = "\n".join(
            part
            for part in (
                branch_result.stderr.strip(),
                commit_result.stderr.strip(),
                status_result.stderr.strip(),
            )
            if part
        )
        return GitInfo(False, "nogit", "nogit", "", False, note or "git query failed")

    status_short = status_result.stdout
    return GitInfo(
        available=True,
        branch=branch_result.stdout.strip() or "nogit",
        commit=commit_result.stdout.strip() or "nogit",
        status_short=status_short,
        dirty=bool(status_short.strip()),
    )


def normalize_manifest_path(raw_path: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw_path, str) or raw_path.strip() == "":
        return None, "missing or non-string path"

    candidate = raw_path.replace("\\", "/").strip()
    if candidate.startswith("./"):
        candidate = candidate[2:]

    if candidate.startswith("/") or re.match(r"^[A-Za-z]:", candidate):
        return None, "absolute paths are not allowed"

    normalized = posixpath.normpath(candidate)
    if normalized == ".":
        return None, "empty path after normalization"
    if normalized == ".." or normalized.startswith("../"):
        return None, "path traversal outside repository is not allowed"
    return normalized, None


def has_glob(path: str) -> bool:
    return any(char in path for char in "*?[]")


def sort_normalized_paths(paths: set[str] | list[str]) -> list[str]:
    return sorted(paths, key=lambda path: path.lower())


def is_skill_instruction(path: str) -> bool:
    parts = path.split("/")
    return (
        len(parts) == 4
        and parts[0] == ".agents"
        and parts[1] == "skills"
        and parts[3] == "SKILL.md"
    )


def is_template(path: str) -> bool:
    return path.startswith(f"{TEMPLATE_ROOT}/") and not has_glob(path)


def is_stack_profile_candidate(path: str) -> bool:
    return path == STACK_PROFILE_ROOT or path.startswith(f"{STACK_PROFILE_ROOT}/")


def is_stack_profile_source(path: str) -> bool:
    parts = path.split("/")
    return (
        len(parts) >= 5
        and parts[0] == ".agents"
        and parts[1] == "stack-profiles"
        and not has_glob(path)
    )


def template_title(path: str) -> str:
    return path[len(TEMPLATE_ROOT) + 1 :]


def skill_title(path: str) -> str:
    return path.split("/")[2]


def stack_profile_title(path: str) -> str:
    return path[len(STACK_PROFILE_ROOT) + 1 :]


def stack_profile_domain(path: str) -> str:
    parts = path.split("/")
    if len(parts) < 3:
        return "other"
    domain = parts[2]
    if domain in KNOWN_STACK_PROFILE_DOMAINS:
        return domain
    return "other"


def stack_profile_target_name(domain: str) -> str:
    return f"stack-profiles-{domain}.md"


def is_runtime_planning(path: str) -> bool:
    return path in RUNTIME_PLANNING_EXACT or any(
        path.startswith(prefix) for prefix in RUNTIME_PLANNING_PREFIXES
    )


def is_generated_project_local(entry: dict[str, Any]) -> bool:
    return entry.get("owner") == "generated_project_local" or entry.get("sync_strategy") == "ignore"


def is_project_preserve(entry: dict[str, Any]) -> bool:
    return entry.get("owner") == "project_preserve" or entry.get("sync_strategy") == "preserve"


def source_path(repo_root: Path, relative_path: str) -> tuple[Path | None, str | None]:
    candidate = repo_root.joinpath(*relative_path.split("/"))
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return None, "source file is missing"
    except OSError as exc:
        return None, f"could not resolve source path: {exc}"

    try:
        resolved.relative_to(repo_root)
    except ValueError:
        return None, "source path resolves outside repository root"

    if not resolved.is_file():
        return None, "source path is not a file"

    return resolved, None


def add_skip(
    skipped: list[dict[str, str]],
    path: str,
    reason: str,
    entry: dict[str, Any] | None = None,
) -> None:
    item = {"path": path, "reason": reason}
    if entry is not None:
        owner = entry.get("owner")
        sync_strategy = entry.get("sync_strategy")
        if owner is not None:
            item["owner"] = str(owner)
        if sync_strategy is not None:
            item["sync_strategy"] = str(sync_strategy)
    skipped.append(item)


def readable_text_source(repo_root: Path, relative_path: str) -> tuple[bool, str | None]:
    path, error = source_path(repo_root, relative_path)
    if error or path is None:
        return False, error or "source path unavailable"
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return False, "source file is not UTF-8 text"
    except OSError as exc:
        return False, f"could not read source file: {exc}"
    return True, None


def add_stack_profile_source(
    repo_root: Path,
    stack_profile_sources_by_domain: dict[str, set[str]],
    skipped: list[dict[str, str]],
    warnings: list[str],
    path: str,
    entry: dict[str, Any] | None = None,
) -> None:
    if not is_stack_profile_source(path):
        add_skip(skipped, path, "unsupported stack-profile path shape", entry)
        return

    is_readable, error = readable_text_source(repo_root, path)
    if not is_readable:
        add_skip(skipped, path, error or "stack-profile source is not readable", entry)
        warnings.append(f"Skipped stack-profile source {path}: {error}.")
        return

    domain = stack_profile_domain(path)
    stack_profile_sources_by_domain.setdefault(domain, set()).add(path)


def expand_stack_profile_glob(repo_root: Path, pattern: str) -> list[str]:
    matches: list[str] = []
    patterns = [pattern]
    if pattern.endswith("/**"):
        patterns.append(f"{pattern}/*")

    for current_pattern in patterns:
        try:
            candidates = repo_root.glob(current_pattern)
            for candidate in candidates:
                try:
                    resolved = candidate.resolve(strict=True)
                except (FileNotFoundError, OSError):
                    continue
                try:
                    resolved.relative_to(repo_root)
                except ValueError:
                    continue
                if not resolved.is_file():
                    continue
                relative = resolved.relative_to(repo_root).as_posix()
                if is_stack_profile_source(relative):
                    matches.append(relative)
        except ValueError:
            continue

    return sort_normalized_paths(set(matches))


def classify_manifest(
    repo_root: Path, manifest: dict[str, Any]
) -> tuple[list[str], list[str], dict[str, list[str]], dict[str, str], list[dict[str, str]], list[str]]:
    skill_sources: set[str] = set()
    template_sources: set[str] = set()
    stack_profile_sources_by_domain: dict[str, set[str]] = {}
    root_copy_sources: dict[str, str] = {}
    skipped: list[dict[str, str]] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            add_skip(skipped, "<invalid-entry>", "manifest entry is not an object")
            warnings.append("Skipped a non-object manifest entry.")
            continue

        path, path_error = normalize_manifest_path(entry.get("path"))
        if path_error:
            add_skip(skipped, str(entry.get("path")), path_error, entry)
            warnings.append(f"Skipped invalid manifest path {entry.get('path')!r}: {path_error}.")
            continue

        if path in seen:
            warnings.append(f"Duplicate manifest entry encountered for {path}; classified once.")
            continue
        seen.add(path)

        if is_generated_project_local(entry):
            add_skip(skipped, path, "generated project-local output is not exported", entry)
            continue

        if is_project_preserve(entry):
            add_skip(skipped, path, "project-preserve entry is not exported", entry)
            continue

        if has_glob(path):
            if is_stack_profile_candidate(path):
                expanded_paths = expand_stack_profile_glob(repo_root, path)
                if not expanded_paths:
                    add_skip(skipped, path, "stack-profile wildcard matched no concrete source files", entry)
                    warnings.append(f"Stack-profile wildcard matched no concrete source files: {path}.")
                    continue
                for expanded_path in expanded_paths:
                    add_stack_profile_source(
                        repo_root,
                        stack_profile_sources_by_domain,
                        skipped,
                        warnings,
                        expanded_path,
                        entry,
                    )
                continue
            add_skip(skipped, path, "wildcard manifest entries are not copied into export packages", entry)
            continue

        if is_skill_instruction(path):
            skill_sources.add(path)
            continue

        if is_template(path):
            template_sources.add(path)
            continue

        if is_stack_profile_candidate(path):
            add_stack_profile_source(
                repo_root,
                stack_profile_sources_by_domain,
                skipped,
                warnings,
                path,
                entry,
            )
            continue

        if path in ROOT_COPY_TARGETS:
            root_copy_sources[path] = ROOT_COPY_TARGETS[path]
            continue

        if is_runtime_planning(path):
            add_skip(skipped, path, "runtime or history planning file is excluded", entry)
            continue

        if path.startswith(".planning/"):
            add_skip(
                skipped,
                path,
                "non-template .planning file is not part of the root-only export",
                entry,
            )
            continue

        add_skip(
            skipped,
            path,
            "source blueprint file is not copied by the root-only export",
            entry,
        )

    local_export_skill = f"{SKILL_ROOT}/gsd-export-blueprint-package/SKILL.md"
    if (repo_root / ".agents" / "skills" / "gsd-export-blueprint-package" / "SKILL.md").is_file():
        if local_export_skill not in skill_sources:
            skill_sources.add(local_export_skill)
            warnings.append(
                "Consolidated local gsd-export-blueprint-package/SKILL.md even though it was not listed in the manifest."
            )

    return (
        sorted(skill_sources),
        sorted(template_sources),
        {
            domain: sort_normalized_paths(sources)
            for domain, sources in sorted(stack_profile_sources_by_domain.items())
        },
        dict(sorted(root_copy_sources.items())),
        sorted(skipped, key=lambda item: item["path"]),
        sorted(warnings),
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(data: str) -> str:
    return sha256_bytes(data.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_source_bytes(repo_root: Path, relative_path: str) -> bytes:
    path, error = source_path(repo_root, relative_path)
    if error or path is None:
        raise FileNotFoundError(f"{relative_path}: {error}")
    return path.read_bytes()


def read_source_text(repo_root: Path, relative_path: str) -> str:
    raw = read_source_bytes(repo_root, relative_path)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnicodeDecodeError(exc.encoding, exc.object, exc.start, exc.end, f"{relative_path}: {exc.reason}")


def source_hash(repo_root: Path, relative_path: str) -> str:
    return sha256_bytes(read_source_bytes(repo_root, relative_path))


def markdown_source(path: str) -> bool:
    return path.lower().endswith((".md", ".markdown"))


def code_fence_language(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".toml"):
        return "toml"
    if lowered.endswith(".json"):
        return "json"
    if lowered.endswith((".yaml", ".yml")):
        return "yaml"
    if lowered.endswith(".txt"):
        return "text"
    if ".template" in lowered:
        return "text"
    return "text"


def code_fence_for(content: str) -> str:
    longest = 0
    for match in re.finditer(r"`+", content):
        longest = max(longest, len(match.group(0)))
    return "`" * max(3, longest + 1)


def section_markers(group: str, section_id: str) -> tuple[str, str]:
    start = f"{SECTION_START_PREFIX}{group} {section_id} -->"
    end = f"{SECTION_END_PREFIX}{group} {section_id} -->"
    return start, end


def ensure_trailing_newline(content: str) -> str:
    return content if content.endswith("\n") else f"{content}\n"


def render_section(
    group: str,
    section_id: str,
    title: str,
    source: str,
    body: str,
    content_mode: str,
) -> Section:
    start_marker, end_marker = section_markers(group, section_id)
    rendered_body = ensure_trailing_newline(body)
    rendered = (
        f"{start_marker}\n"
        f"## {title}\n\n"
        f"<!-- source: {source} -->\n\n"
        f"{rendered_body}"
        f"{end_marker}\n"
    )
    return Section(
        group=group,
        section_id=section_id,
        source_path=source,
        source_hash="",
        rendered=rendered,
        rendered_hash=sha256_text(rendered),
        content_mode=content_mode,
        start_marker=start_marker,
        end_marker=end_marker,
    )


def render_markdown_source_section(
    repo_root: Path,
    group: str,
    section_id: str,
    title: str,
    source: str,
) -> Section:
    body = read_source_text(repo_root, source)
    section = render_section(group, section_id, title, source, body, "markdown")
    return Section(
        group=section.group,
        section_id=section.section_id,
        source_path=section.source_path,
        source_hash=source_hash(repo_root, source),
        rendered=section.rendered,
        rendered_hash=section.rendered_hash,
        content_mode=section.content_mode,
        start_marker=section.start_marker,
        end_marker=section.end_marker,
    )


def render_stack_profile_section(repo_root: Path, source: str) -> Section:
    content = read_source_text(repo_root, source)
    if markdown_source(source):
        body = content
        mode = "markdown"
    else:
        fence = code_fence_for(content)
        language = code_fence_language(source)
        body = f"{fence}{language}\n{ensure_trailing_newline(content)}{fence}\n"
        mode = f"fenced:{language}"

    section_id = stack_profile_title(source)
    section = render_section(
        "stack-profiles",
        section_id,
        section_id,
        source,
        body,
        mode,
    )
    return Section(
        group=section.group,
        section_id=section.section_id,
        source_path=section.source_path,
        source_hash=source_hash(repo_root, source),
        rendered=section.rendered,
        rendered_hash=section.rendered_hash,
        content_mode=section.content_mode,
        start_marker=section.start_marker,
        end_marker=section.end_marker,
    )


def build_consolidated_file(target_file: str, sections: list[Section]) -> str:
    content = f"# {target_file}\n\n"
    if sections:
        content += "\n".join(section.rendered for section in sections)
    return content


def build_sections(
    repo_root: Path,
    sources: list[str],
    group: str,
    title_for_path: Any,
    warnings: list[str],
) -> list[Section]:
    sections: list[Section] = []
    for relative_path in sources:
        try:
            section_id = title_for_path(relative_path)
            sections.append(
                render_markdown_source_section(
                    repo_root,
                    group,
                    section_id,
                    section_id,
                    relative_path,
                )
            )
        except (OSError, UnicodeError) as exc:
            warnings.append(f"Skipped {group} source {relative_path}: {exc}.")
    return sections


def build_render_plan(repo_root: Path, manifest: dict[str, Any]) -> RenderPlan:
    (
        skill_sources,
        template_sources,
        stack_profile_sources_by_domain,
        root_copy_sources,
        skipped,
        warnings,
    ) = classify_manifest(repo_root, manifest)

    consolidated_outputs: dict[str, list[Section]] = {}
    skills_sections = build_sections(repo_root, skill_sources, "skills", skill_title, warnings)
    template_sections = build_sections(repo_root, template_sources, "templates", template_title, warnings)
    consolidated_outputs["skills.md"] = skills_sections
    consolidated_outputs["templates.md"] = template_sections

    stack_profile_sources_by_target: dict[str, list[str]] = {}
    for domain, sources in stack_profile_sources_by_domain.items():
        target_name = stack_profile_target_name(domain)
        sections: list[Section] = []
        for relative_path in sources:
            try:
                sections.append(render_stack_profile_section(repo_root, relative_path))
            except (OSError, UnicodeError) as exc:
                warnings.append(f"Skipped stack-profile source {relative_path}: {exc}.")
        consolidated_outputs[target_name] = sections
        stack_profile_sources_by_target[target_name] = sources

    content_by_file = {
        target: build_consolidated_file(target, sections)
        for target, sections in sorted(consolidated_outputs.items())
    }

    copy_outputs: dict[str, CopyOutput] = {}
    copied_root_files: list[dict[str, str]] = []
    for source_relative_path, target_name in root_copy_sources.items():
        try:
            content = read_source_text(repo_root, source_relative_path)
        except (OSError, UnicodeError) as exc:
            add_skip(skipped, source_relative_path, f"copy failed: {exc}")
            warnings.append(f"Skipped root copy source {source_relative_path}: {exc}.")
            continue
        copy_output = CopyOutput(
            source_path=source_relative_path,
            target_file=target_name,
            source_hash=source_hash(repo_root, source_relative_path),
            content=content,
        )
        copy_outputs[target_name] = copy_output
        content_by_file[target_name] = content
        copied_root_files.append({"source": source_relative_path, "target": target_name})

    content_by_file["index.md"] = build_index_md()

    return RenderPlan(
        content_by_file=dict(sorted(content_by_file.items())),
        consolidated_outputs=dict(sorted(consolidated_outputs.items())),
        copy_outputs=dict(sorted(copy_outputs.items())),
        skill_sources=skill_sources,
        template_sources=template_sources,
        stack_profile_sources_by_target=dict(sorted(stack_profile_sources_by_target.items())),
        copied_root_files=sorted(copied_root_files, key=lambda item: item["target"]),
        skipped=sorted(skipped, key=lambda item: item["path"]),
        warnings=sorted(set(warnings)),
    )


def build_index_md() -> str:
    return """# GSD Blueprint Export Index

This package is a root-only export of the reusable GSD blueprint for ChatGPT Project upload or portable review. It is a flattened representation, not the original repository layout.

The source repository contains directories such as `.agents/skills/**`, `.planning/templates/**`, `.agents/stack-profiles/**`, `.gsd/**`, plus root files such as `AGENTS.md` and `PROJECT.md`. This export intentionally consolidates those sources into a small set of root files, so separate skill folders, template files, stack-profile folders, runtime planning files, and generated `.codex` outputs are intentionally absent.

Consolidated sections in `skills.md`, `templates.md`, and `stack-profiles-<domain>.md` represent original source files. Skipped files are intentional and are recorded in `export-manifest.json`. Runtime planning files, milestones, phases, verification artifacts, `.codex` outputs, and project history are not exported. `export-lock.json` tracks source-to-section mappings for incremental export. `export-manifest.json` summarizes the export run. `checksums.sha256` covers final root-level export files except itself.

| Original GSD source                                                            | Export representation                                                                  |
| ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| `.agents/skills/**/SKILL.md`                                                   | sections inside `skills.md`                                                            |
| `.planning/templates/**`                                                       | sections inside `templates.md`                                                         |
| `.agents/stack-profiles/<domain>/**`                                           | sections inside `stack-profiles-<domain>.md`                                           |
| `AGENTS.md`                                                                    | copied as `agents.md`                                                                  |
| `PROJECT.md`                                                                   | copied as `project.md`                                                                 |
| `.gsd/blueprint-manifest.json`                                                 | represented through `export-manifest.json` and `export-lock.json`, not copied directly |
| `.agents/skills/**/agents/openai.yaml`                                         | skipped intentionally and recorded in `export-manifest.json`                           |
| `.planning/STATE.md`, `.planning/ROADMAP.md`, milestones, phases, verification | not exported because they are runtime/project history artifacts                        |
| `.codex/**`                                                                    | not exported because generated project-local files are ignored                         |
"""


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def export_dir_name(version: str, timestamp: dt.datetime, git_info: GitInfo) -> str:
    stamp = timestamp.strftime("%Y%m%d-%H%M%S")
    clean_state = "dirty" if git_info.dirty else "clean"
    return "gsd-blueprint-{version}-{stamp}-{branch}-{commit}-{state}".format(
        version=sanitize_component(version),
        stamp=stamp,
        branch=sanitize_component(git_info.branch),
        commit=sanitize_component(git_info.commit),
        state=clean_state,
    )


def git_status_text(git_info: GitInfo) -> str:
    if not git_info.available:
        note = git_info.note or "git is unavailable or this is not a git repository"
        return f"Git information unavailable.\n\n{note}\n"

    status = git_info.status_short
    if not status:
        status = "(clean)\n"

    return (
        f"current branch: {git_info.branch}\n"
        f"current short commit: {git_info.commit}\n"
        "\n"
        "git status --short:\n"
        f"{status}"
    )


def write_bytes(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8"))


def write_content_files(export_root: Path, content_by_file: dict[str, str]) -> None:
    for target_file, content in sorted(content_by_file.items()):
        write_bytes(export_root / target_file, content)


def write_checksums(export_root: Path, generated_files: list[str]) -> None:
    lines = []
    for relative_path in sorted(path for path in generated_files if path != "checksums.sha256"):
        digest = sha256_file(export_root / relative_path)
        lines.append(f"{digest}  {relative_path}\n")
    write_bytes(export_root / "checksums.sha256", "".join(lines))


def parse_marked_sections(content: str, expected_group: str) -> tuple[dict[str, str], str | None]:
    lines = content.splitlines(keepends=True)
    sections: dict[str, str] = {}
    index = 0
    start_re = re.compile(
        rf"^<!-- GSD-EXPORT-SECTION:START {re.escape(expected_group)} (.+) -->\r?\n?$"
    )

    while index < len(lines):
        line = lines[index]
        start_match = start_re.match(line)
        if not start_match:
            if SECTION_START_PREFIX in line or SECTION_END_PREFIX in line:
                return {}, f"malformed section marker in {expected_group} target"
            index += 1
            continue

        section_id = start_match.group(1)
        if section_id in sections:
            return {}, f"duplicate section marker for {expected_group} {section_id}"
        expected_end = f"{SECTION_END_PREFIX}{expected_group} {section_id} -->"
        start_index = index
        index += 1
        while index < len(lines):
            if lines[index].strip() == expected_end:
                index += 1
                sections[section_id] = "".join(lines[start_index:index])
                break
            index += 1
        else:
            return {}, f"missing end marker for {expected_group} {section_id}"

    if not sections:
        return {}, f"no {expected_group} section markers found"
    return sections, None


def load_previous_lock(base_export: Path) -> tuple[dict[str, Any] | None, str | None]:
    lock_path = base_export / "export-lock.json"
    if not lock_path.is_file():
        return None, "missing previous export-lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid previous export-lock.json: {exc}"
    if not isinstance(lock, dict):
        return None, "previous export-lock.json is not an object"
    if lock.get("schema_version") != EXPORT_SCHEMA_VERSION:
        return None, "unsupported previous export-lock.json schema_version"
    if lock.get("export_renderer_version") != EXPORT_RENDERER_VERSION:
        return None, "unsupported previous export renderer version"
    if not isinstance(lock.get("outputs"), list):
        return None, "previous export-lock.json is missing outputs array"
    return lock, None


def previous_output_by_target(lock: dict[str, Any]) -> dict[str, dict[str, Any]]:
    outputs: dict[str, dict[str, Any]] = {}
    for output in lock.get("outputs", []):
        if isinstance(output, dict) and isinstance(output.get("target_file"), str):
            outputs[output["target_file"]] = output
    return outputs


def previous_sections_by_id(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for section in output.get("sections", []):
        if isinstance(section, dict) and isinstance(section.get("section_id"), str):
            sections[section["section_id"]] = section
    return sections


def build_incremental_content(
    render_plan: RenderPlan,
    base_export: Path,
    mode: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    strict = mode == "incremental-strict"
    lock, lock_error = load_previous_lock(base_export)
    stats: dict[str, Any] = {
        "incremental_fallback_occurred": False,
        "incremental_fallback_reasons": [],
        "changed_sections": [],
        "added_sections": [],
        "removed_sections": [],
        "changed_copied_files": [],
        "regenerated_target_files": [],
        "reused_sections": [],
        "reused_copied_files": [],
    }

    if lock_error or lock is None:
        if strict:
            fail(lock_error or "previous export lock could not be loaded")
        stats["incremental_fallback_occurred"] = True
        stats["incremental_fallback_reasons"].append(lock_error or "previous export lock could not be loaded")
        stats["regenerated_target_files"] = sorted(render_plan.content_by_file)
        return render_plan.content_by_file.copy(), stats

    previous_outputs = previous_output_by_target(lock)
    content_by_file: dict[str, str] = {}
    current_consolidated_targets = set(render_plan.consolidated_outputs)

    for target_file, sections in render_plan.consolidated_outputs.items():
        previous_output = previous_outputs.get(target_file)
        previous_target = base_export / target_file
        if not previous_output or previous_output.get("output_kind") != "consolidated":
            reason = f"previous lock missing consolidated output for {target_file}"
            if strict:
                fail(reason)
            stats["incremental_fallback_occurred"] = True
            stats["incremental_fallback_reasons"].append(reason)
            stats["regenerated_target_files"].append(target_file)
            content_by_file[target_file] = render_plan.content_by_file[target_file]
            continue
        if not previous_target.is_file():
            reason = f"previous export missing required target file {target_file}"
            if strict:
                fail(reason)
            stats["incremental_fallback_occurred"] = True
            stats["incremental_fallback_reasons"].append(reason)
            stats["regenerated_target_files"].append(target_file)
            content_by_file[target_file] = render_plan.content_by_file[target_file]
            continue

        group = previous_output.get("group")
        expected_group = sections[0].group if sections else previous_output.get("group")
        if group != expected_group or not isinstance(expected_group, str):
            reason = f"previous lock group mismatch for {target_file}"
            if strict:
                fail(reason)
            stats["incremental_fallback_occurred"] = True
            stats["incremental_fallback_reasons"].append(reason)
            stats["regenerated_target_files"].append(target_file)
            content_by_file[target_file] = render_plan.content_by_file[target_file]
            continue

        try:
            previous_content = previous_target.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            reason = f"could not read previous target {target_file}: {exc}"
            if strict:
                fail(reason)
            stats["incremental_fallback_occurred"] = True
            stats["incremental_fallback_reasons"].append(reason)
            stats["regenerated_target_files"].append(target_file)
            content_by_file[target_file] = render_plan.content_by_file[target_file]
            continue

        parsed_sections, parse_error = parse_marked_sections(previous_content, expected_group)
        if parse_error:
            if strict:
                fail(f"{target_file}: {parse_error}")
            stats["incremental_fallback_occurred"] = True
            stats["incremental_fallback_reasons"].append(f"{target_file}: {parse_error}")
            stats["regenerated_target_files"].append(target_file)
            content_by_file[target_file] = render_plan.content_by_file[target_file]
            continue

        previous_sections = previous_sections_by_id(previous_output)
        previous_ids = set(previous_sections)
        current_ids = {section.section_id for section in sections}
        for removed_id in sorted(previous_ids - current_ids):
            stats["removed_sections"].append({"target_file": target_file, "section_id": removed_id})

        patched_sections: list[str] = []
        target_fallback_reason: str | None = None
        for section in sections:
            previous_section = previous_sections.get(section.section_id)
            previous_rendered = parsed_sections.get(section.section_id)
            if previous_section is None:
                stats["added_sections"].append(
                    {"target_file": target_file, "section_id": section.section_id, "source_path": section.source_path}
                )
                patched_sections.append(section.rendered)
                continue
            if previous_rendered is None:
                target_fallback_reason = f"previous marker missing for {target_file} section {section.section_id}"
                break
            previous_rendered_hash = previous_section.get("rendered_section_hash")
            if previous_rendered_hash != sha256_text(previous_rendered):
                target_fallback_reason = f"previous target content hash mismatch for {target_file} section {section.section_id}"
                break
            if previous_section.get("source_path") != section.source_path:
                stats["changed_sections"].append(
                    {"target_file": target_file, "section_id": section.section_id, "source_path": section.source_path}
                )
                patched_sections.append(section.rendered)
                continue
            if previous_section.get("source_hash") == section.source_hash:
                patched_sections.append(previous_rendered)
                stats["reused_sections"].append(
                    {"target_file": target_file, "section_id": section.section_id, "source_path": section.source_path}
                )
                continue
            stats["changed_sections"].append(
                {"target_file": target_file, "section_id": section.section_id, "source_path": section.source_path}
            )
            patched_sections.append(section.rendered)

        if target_fallback_reason:
            if strict:
                fail(target_fallback_reason)
            stats["incremental_fallback_occurred"] = True
            stats["incremental_fallback_reasons"].append(target_fallback_reason)
            stats["regenerated_target_files"].append(target_file)
            content_by_file[target_file] = render_plan.content_by_file[target_file]
            continue

        content_by_file[target_file] = f"# {target_file}\n\n"
        if patched_sections:
            content_by_file[target_file] += "\n".join(patched_sections)

    for target_file, previous_output in sorted(previous_outputs.items()):
        if target_file in current_consolidated_targets:
            continue
        if previous_output.get("output_kind") != "consolidated":
            continue
        for previous_section in previous_output.get("sections", []):
            if isinstance(previous_section, dict) and isinstance(previous_section.get("section_id"), str):
                stats["removed_sections"].append(
                    {
                        "target_file": target_file,
                        "section_id": previous_section["section_id"],
                        "source_path": str(previous_section.get("source_path", "")),
                    }
                )

    for target_file, copy_output in render_plan.copy_outputs.items():
        previous_output = previous_outputs.get(target_file)
        previous_target = base_export / target_file
        if not previous_output or previous_output.get("output_kind") != "copy":
            reason = f"previous lock missing copy output for {target_file}"
            if strict:
                fail(reason)
            stats["incremental_fallback_occurred"] = True
            stats["incremental_fallback_reasons"].append(reason)
            stats["regenerated_target_files"].append(target_file)
            content_by_file[target_file] = copy_output.content
            continue
        if not previous_target.is_file():
            reason = f"previous export missing required target file {target_file}"
            if strict:
                fail(reason)
            stats["incremental_fallback_occurred"] = True
            stats["incremental_fallback_reasons"].append(reason)
            stats["regenerated_target_files"].append(target_file)
            content_by_file[target_file] = copy_output.content
            continue
        previous_target_hash = previous_output.get("target_hash")
        actual_previous_hash = sha256_file(previous_target)
        if previous_target_hash != actual_previous_hash:
            reason = f"previous target content hash mismatch for {target_file}"
            if strict:
                fail(reason)
            stats["incremental_fallback_occurred"] = True
            stats["incremental_fallback_reasons"].append(reason)
            stats["regenerated_target_files"].append(target_file)
            content_by_file[target_file] = copy_output.content
            continue
        if previous_output.get("source_hash") == copy_output.source_hash:
            content_by_file[target_file] = previous_target.read_bytes().decode("utf-8")
            stats["reused_copied_files"].append({"target_file": target_file, "source_path": copy_output.source_path})
        else:
            content_by_file[target_file] = copy_output.content
            stats["changed_copied_files"].append({"target_file": target_file, "source_path": copy_output.source_path})

    content_by_file["index.md"] = render_plan.content_by_file["index.md"]
    stats["regenerated_target_files"] = sorted(set(stats["regenerated_target_files"]))
    return dict(sorted(content_by_file.items())), stats


def output_lock_entries(render_plan: RenderPlan, content_by_file: dict[str, str]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for target_file, sections in sorted(render_plan.consolidated_outputs.items()):
        group = sections[0].group if sections else (
            "skills" if target_file == "skills.md" else "templates"
        )
        outputs.append(
            {
                "target_file": target_file,
                "output_kind": "consolidated",
                "group": group,
                "target_hash": sha256_text(content_by_file[target_file]),
                "sections": [
                    {
                        "section_id": section.section_id,
                        "source_path": section.source_path,
                        "source_hash": section.source_hash,
                        "rendered_section_hash": section.rendered_hash,
                        "content_mode": section.content_mode,
                        "start_marker": section.start_marker,
                        "end_marker": section.end_marker,
                    }
                    for section in sections
                ],
            }
        )
    for target_file, copy_output in sorted(render_plan.copy_outputs.items()):
        outputs.append(
            {
                "target_file": target_file,
                "output_kind": "copy",
                "source_path": copy_output.source_path,
                "source_hash": copy_output.source_hash,
                "target_hash": sha256_text(content_by_file[target_file]),
            }
        )
    return outputs


def generated_files_for(render_plan: RenderPlan) -> list[str]:
    content_files = sorted(
        set(BASE_CONTENT_FILES)
        | set(render_plan.stack_profile_sources_by_target)
        | {"index.md"}
    )
    return sorted(set(METADATA_FILES) | set(content_files))


def build_export_lock(
    repo_root: Path,
    manifest_hash: str,
    version: str,
    git_info: GitInfo,
    generated_at: dt.datetime,
    mode: str,
    base_export: Path | None,
    generated_files: list[str],
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "export_renderer_version": EXPORT_RENDERER_VERSION,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "source_repository_path": str(repo_root),
        "source_manifest_path": MANIFEST_RELATIVE_PATH,
        "source_manifest_hash": manifest_hash,
        "blueprint_version": version,
        "git_branch": git_info.branch,
        "git_commit": git_info.commit,
        "git_dirty": git_info.dirty,
        "export_mode": mode,
        "base_export_path": str(base_export) if base_export else "",
        "generated_files": generated_files,
        "outputs": outputs,
    }


def build_export_manifest(
    repo_root: Path,
    version: str,
    git_info: GitInfo,
    generated_at: dt.datetime,
    mode: str,
    base_export: Path | None,
    generated_files: list[str],
    render_plan: RenderPlan,
    incremental_stats: dict[str, Any],
    verification_result: dict[str, Any] | None,
) -> dict[str, Any]:
    consolidated_stack_profile_sources = {
        target: sources
        for target, sources in render_plan.stack_profile_sources_by_target.items()
        if target in render_plan.content_by_file
    }
    manifest = {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "export_renderer_version": EXPORT_RENDERER_VERSION,
        "export_mode": mode,
        "base_export_path": str(base_export) if base_export else "",
        "incremental_fallback_occurred": incremental_stats.get("incremental_fallback_occurred", False),
        "incremental_fallback_reasons": incremental_stats.get("incremental_fallback_reasons", []),
        "changed_sections": incremental_stats.get("changed_sections", []),
        "added_sections": incremental_stats.get("added_sections", []),
        "removed_sections": incremental_stats.get("removed_sections", []),
        "changed_copied_files": incremental_stats.get("changed_copied_files", []),
        "regenerated_target_files": incremental_stats.get("regenerated_target_files", []),
        "reused_sections": incremental_stats.get("reused_sections", []),
        "reused_copied_files": incremental_stats.get("reused_copied_files", []),
        "full_render_verification": verification_result,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "source_repository_path": str(repo_root),
        "source_manifest_path": MANIFEST_RELATIVE_PATH,
        "blueprint_version": version,
        "git_branch": git_info.branch,
        "git_commit": git_info.commit,
        "git_dirty": git_info.dirty,
        "git_status_file": "git-status.txt",
        "index_file": "index.md",
        "export_lock_file": "export-lock.json",
        "generated_files": generated_files,
        "consolidated_skill_sources": render_plan.skill_sources,
        "consolidated_template_sources": render_plan.template_sources,
        "consolidated_stack_profile_sources": consolidated_stack_profile_sources,
        "root_file_sources": render_plan.copied_root_files,
        "skipped_manifest_sources": render_plan.skipped,
        "warnings": render_plan.warnings,
        "counts": {
            "skills_consolidated": len(render_plan.skill_sources),
            "templates_consolidated": len(render_plan.template_sources),
            "stack_profile_files_generated": len(render_plan.stack_profile_sources_by_target),
            "stack_profile_sources_consolidated": sum(
                len(sources) for sources in consolidated_stack_profile_sources.values()
            ),
            "root_files_copied": len(render_plan.copied_root_files),
            "files_skipped": len(render_plan.skipped),
            "warnings": len(render_plan.warnings),
            "changed_sections": len(incremental_stats.get("changed_sections", [])),
            "added_sections": len(incremental_stats.get("added_sections", [])),
            "removed_sections": len(incremental_stats.get("removed_sections", [])),
            "changed_copied_files": len(incremental_stats.get("changed_copied_files", [])),
            "regenerated_target_files": len(incremental_stats.get("regenerated_target_files", [])),
        },
    }
    return manifest


def verify_against_full_render(
    incremental_content: dict[str, str],
    full_content: dict[str, str],
) -> dict[str, Any]:
    excluded = ["checksums.sha256", "export-lock.json", "export-manifest.json"]
    compare_targets = sorted(
        target
        for target in set(incremental_content) | set(full_content)
        if target not in excluded
    )
    mismatches = [
        target
        for target in compare_targets
        if incremental_content.get(target) != full_content.get(target)
    ]
    return {
        "enabled": True,
        "passed": not mismatches,
        "compared_files": compare_targets,
        "excluded_files": excluded,
        "mismatches": mismatches,
    }


def print_dry_run(
    export_root: Path,
    mode: str,
    base_export: Path | None,
    git_info: GitInfo,
    render_plan: RenderPlan,
    incremental_stats: dict[str, Any] | None,
) -> None:
    print(f"Planned output directory: {export_root}")
    print(f"Export mode: {mode}")
    if base_export:
        print(f"Base export: {base_export}")
    print(f"Git branch: {git_info.branch}")
    print(f"Git commit: {git_info.commit}")
    print(f"Git dirty: {str(git_info.dirty).lower()}")
    print()
    print(f"Skills to consolidate ({len(render_plan.skill_sources)}):")
    for path in render_plan.skill_sources:
        print(f"  - {path}")
    print()
    print(f"Templates to consolidate ({len(render_plan.template_sources)}):")
    for path in render_plan.template_sources:
        print(f"  - {path}")
    print()
    stack_profile_source_count = sum(len(sources) for sources in render_plan.stack_profile_sources_by_target.values())
    print(
        "Stack-profile sources to consolidate "
        f"({stack_profile_source_count}) into {len(render_plan.stack_profile_sources_by_target)} domain file(s):"
    )
    for target, sources in render_plan.stack_profile_sources_by_target.items():
        print(f"  - {target}")
        for path in sources:
            print(f"    - {path}")
    print()
    print(f"Root files to copy ({len(render_plan.copied_root_files)}):")
    for item in render_plan.copied_root_files:
        print(f"  - {item['source']} -> {item['target']}")
    print()
    print(f"Generated metadata: index.md, export-lock.json, export-manifest.json, checksums.sha256, git-status.txt")
    if incremental_stats:
        print()
        print(f"Incremental fallback: {str(incremental_stats.get('incremental_fallback_occurred', False)).lower()}")
        for reason in incremental_stats.get("incremental_fallback_reasons", []):
            print(f"  - {reason}")
    print()
    print(f"Manifest entries to skip ({len(render_plan.skipped)}):")
    for item in render_plan.skipped:
        print(f"  - {item['path']}: {item['reason']}")
    if render_plan.warnings:
        print()
        print(f"Warnings ({len(render_plan.warnings)}):")
        for warning in render_plan.warnings:
            print(f"  - {warning}")


def create_export(args: argparse.Namespace) -> Path:
    repo_root = resolve_repo_root(args.repo_root)
    output_root = resolve_output_root(repo_root, args.output_root)
    base_export = resolve_base_export(args.base_export, args.mode)
    manifest, manifest_hash = load_manifest(repo_root)
    version = manifest_version(manifest)
    generated_at = now_utc()
    git_info = collect_git_info(repo_root)
    export_root = output_root / export_dir_name(version, generated_at, git_info)
    render_plan = build_render_plan(repo_root, manifest)
    render_plan.content_by_file["git-status.txt"] = git_status_text(git_info)

    incremental_stats: dict[str, Any] = {
        "incremental_fallback_occurred": False,
        "incremental_fallback_reasons": [],
        "changed_sections": [],
        "added_sections": [],
        "removed_sections": [],
        "changed_copied_files": [],
        "regenerated_target_files": [],
        "reused_sections": [],
        "reused_copied_files": [],
    }
    content_by_file = render_plan.content_by_file.copy()
    if args.mode in {"incremental", "incremental-strict"}:
        if base_export is None:
            fail("--base-export is required for incremental modes")
        content_by_file, incremental_stats = build_incremental_content(render_plan, base_export, args.mode)
        content_by_file["git-status.txt"] = git_status_text(git_info)

    verification_result = None
    if args.verify_against_full_render:
        if args.mode == "full":
            verification_result = {
                "enabled": True,
                "passed": True,
                "compared_files": sorted(content_by_file),
                "excluded_files": [],
                "mismatches": [],
                "note": "full mode is already the canonical full render",
            }
        else:
            full_content = render_plan.content_by_file.copy()
            full_content["git-status.txt"] = git_status_text(git_info)
            verification_result = verify_against_full_render(content_by_file, full_content)
            if not verification_result["passed"]:
                fail(
                    "--verify-against-full-render failed; mismatched files: "
                    + ", ".join(verification_result["mismatches"])
                )

    generated_files = generated_files_for(render_plan)

    if args.dry_run:
        print_dry_run(
            export_root,
            args.mode,
            base_export,
            git_info,
            render_plan,
            incremental_stats if args.mode != "full" else None,
        )
        return export_root

    if export_root.exists():
        fail(f"export directory already exists: {export_root}")

    export_root.mkdir(parents=True)
    write_content_files(export_root, content_by_file)

    outputs = output_lock_entries(render_plan, content_by_file)
    export_lock = build_export_lock(
        repo_root,
        manifest_hash,
        version,
        git_info,
        generated_at,
        args.mode,
        base_export,
        generated_files,
        outputs,
    )
    write_bytes(
        export_root / "export-lock.json",
        json.dumps(export_lock, indent=2, ensure_ascii=False) + "\n",
    )

    export_manifest = build_export_manifest(
        repo_root,
        version,
        git_info,
        generated_at,
        args.mode,
        base_export,
        generated_files,
        render_plan,
        incremental_stats,
        verification_result,
    )
    write_bytes(
        export_root / "export-manifest.json",
        json.dumps(export_manifest, indent=2, ensure_ascii=False) + "\n",
    )
    write_checksums(export_root, generated_files)

    print(f"Export package created: {export_root}")
    print(
        "Counts: "
        f"skills={len(render_plan.skill_sources)}, "
        f"templates={len(render_plan.template_sources)}, "
        f"stack_profile_files={len(render_plan.stack_profile_sources_by_target)}, "
        f"stack_profile_sources={sum(len(sources) for sources in render_plan.stack_profile_sources_by_target.values())}, "
        f"root_files={len(render_plan.copied_root_files)}, "
        f"skipped={len(render_plan.skipped)}, "
        f"warnings={len(render_plan.warnings)}"
    )
    print(f"Export mode: {args.mode}")
    if incremental_stats.get("incremental_fallback_occurred"):
        print("Incremental fallback: true")
    if verification_result is not None:
        print(f"Full-render verification: {'passed' if verification_result['passed'] else 'failed'}")
    print(f"Git state: {'dirty' if git_info.dirty else 'clean'}")

    return export_root


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    create_export(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
