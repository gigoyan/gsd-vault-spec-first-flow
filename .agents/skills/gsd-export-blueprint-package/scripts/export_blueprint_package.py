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
DEFAULT_OUTPUT_ROOT = ".gsd/exports"
MANIFEST_RELATIVE_PATH = ".gsd/blueprint-manifest.json"
SKILL_ROOT = ".agents/skills"
TEMPLATE_ROOT = ".planning/templates"
GENERATED_FILES = [
    "export-manifest.json",
    "checksums.sha256",
    "git-status.txt",
    "skills.md",
    "templates.md",
    "agents.md",
    "project.md",
]
ROOT_COPY_TARGETS = {
    "AGENTS.md": "agents.md",
    "PROJECT.md": "project.md",
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


def load_manifest(repo_root: Path) -> dict[str, Any]:
    manifest_path = repo_root / MANIFEST_RELATIVE_PATH
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        fail(f"could not read manifest {manifest_path}: {exc}")

    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"manifest is not valid UTF-8 JSON: {exc}")

    files = manifest.get("files")
    if not isinstance(files, list):
        fail("manifest schema is unsupported: expected top-level 'files' array")

    return manifest


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


def template_title(path: str) -> str:
    return path[len(TEMPLATE_ROOT) + 1 :]


def skill_title(path: str) -> str:
    return path.split("/")[2]


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


def classify_manifest(
    repo_root: Path, manifest: dict[str, Any]
) -> tuple[list[str], list[str], dict[str, str], list[dict[str, str]], list[str]]:
    skill_sources: set[str] = set()
    template_sources: set[str] = set()
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
            add_skip(skipped, path, "wildcard manifest entries are not copied into export packages", entry)
            continue

        if is_skill_instruction(path):
            skill_sources.add(path)
            continue

        if is_template(path):
            template_sources.add(path)
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
        dict(sorted(root_copy_sources.items())),
        sorted(skipped, key=lambda item: item["path"]),
        sorted(warnings),
    )


def read_source_text(repo_root: Path, relative_path: str) -> str:
    path, error = source_path(repo_root, relative_path)
    if error or path is None:
        raise FileNotFoundError(f"{relative_path}: {error}")
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnicodeDecodeError(exc.encoding, exc.object, exc.start, exc.end, f"{relative_path}: {exc.reason}")


def append_section(buffer: list[str], title: str, source: str, content: str) -> None:
    if buffer:
        buffer.append("\n---\n\n")
    buffer.append(f"## {title}\n\n")
    buffer.append(f"<!-- source: {source} -->\n\n")
    buffer.append(content)
    if not content.endswith("\n"):
        buffer.append("\n")


def build_skills_md(repo_root: Path, skill_sources: list[str], warnings: list[str]) -> str:
    sections: list[str] = ["# skills.md\n\n"]
    for relative_path in skill_sources:
        try:
            content = read_source_text(repo_root, relative_path)
        except (OSError, UnicodeError) as exc:
            warnings.append(f"Skipped skill source {relative_path}: {exc}.")
            continue
        append_section(sections, skill_title(relative_path), relative_path, content)
    return "".join(sections)


def build_templates_md(repo_root: Path, template_sources: list[str], warnings: list[str]) -> str:
    sections: list[str] = ["# templates.md\n\n"]
    for relative_path in template_sources:
        try:
            content = read_source_text(repo_root, relative_path)
        except (OSError, UnicodeError) as exc:
            warnings.append(f"Skipped template source {relative_path}: {exc}.")
            continue
        append_section(sections, template_title(relative_path), relative_path, content)
    return "".join(sections)


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


def copy_root_files_to_export(
    repo_root: Path,
    export_root: Path,
    root_copy_sources: dict[str, str],
    skipped: list[dict[str, str]],
    warnings: list[str],
) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []

    for source_relative_path, target_name in root_copy_sources.items():
        source, error = source_path(repo_root, source_relative_path)
        if error or source is None:
            add_skip(skipped, source_relative_path, error or "source path unavailable")
            warnings.append(f"Skipped root copy source {source_relative_path}: {error}.")
            continue

        target = export_root / target_name
        try:
            target.write_bytes(source.read_bytes())
        except OSError as exc:
            add_skip(skipped, source_relative_path, f"copy failed: {exc}")
            warnings.append(f"Skipped root copy source {source_relative_path}: copy failed: {exc}.")
            continue
        copied.append({"source": source_relative_path, "target": target_name})

    return copied


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(export_root: Path, checksum_targets: list[str]) -> None:
    targets = [
        path for path in checksum_targets if path != "checksums.sha256"
    ]
    lines = []
    for relative_path in sorted(targets):
        digest = sha256_file(export_root / relative_path)
        lines.append(f"{digest}  {relative_path}\n")
    write_bytes(export_root / "checksums.sha256", "".join(lines))


def print_dry_run(
    export_root: Path,
    git_info: GitInfo,
    skill_sources: list[str],
    template_sources: list[str],
    root_copy_sources: dict[str, str],
    skipped: list[dict[str, str]],
    warnings: list[str],
) -> None:
    print(f"Planned output directory: {export_root}")
    print(f"Git branch: {git_info.branch}")
    print(f"Git commit: {git_info.commit}")
    print(f"Git dirty: {str(git_info.dirty).lower()}")
    print()
    print(f"Skills to consolidate ({len(skill_sources)}):")
    for path in skill_sources:
        print(f"  - {path}")
    print()
    print(f"Templates to consolidate ({len(template_sources)}):")
    for path in template_sources:
        print(f"  - {path}")
    print()
    print(f"Root files to copy ({len(root_copy_sources)}):")
    for source, target in root_copy_sources.items():
        print(f"  - {source} -> {target}")
    print()
    print(f"Manifest entries to skip ({len(skipped)}):")
    for item in skipped:
        print(f"  - {item['path']}: {item['reason']}")
    if warnings:
        print()
        print(f"Warnings ({len(warnings)}):")
        for warning in warnings:
            print(f"  - {warning}")


def create_export(args: argparse.Namespace) -> Path:
    repo_root = resolve_repo_root(args.repo_root)
    output_root = resolve_output_root(repo_root, args.output_root)
    manifest = load_manifest(repo_root)
    version = manifest_version(manifest)
    generated_at = now_utc()
    git_info = collect_git_info(repo_root)
    export_root = output_root / export_dir_name(version, generated_at, git_info)

    skill_sources, template_sources, root_copy_sources, skipped, warnings = classify_manifest(
        repo_root, manifest
    )

    if args.dry_run:
        print_dry_run(
            export_root,
            git_info,
            skill_sources,
            template_sources,
            root_copy_sources,
            skipped,
            warnings,
        )
        return export_root

    if export_root.exists():
        fail(f"export directory already exists: {export_root}")

    export_root.mkdir(parents=True)

    skills_md = build_skills_md(repo_root, skill_sources, warnings)
    templates_md = build_templates_md(repo_root, template_sources, warnings)
    write_bytes(export_root / "skills.md", skills_md)
    write_bytes(export_root / "templates.md", templates_md)

    copied_root_files = copy_root_files_to_export(
        repo_root, export_root, root_copy_sources, skipped, warnings
    )
    write_bytes(export_root / "git-status.txt", git_status_text(git_info))

    generated_files = GENERATED_FILES.copy()
    export_manifest = {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "source_repository_path": str(repo_root),
        "source_manifest_path": MANIFEST_RELATIVE_PATH,
        "blueprint_version": version,
        "git_branch": git_info.branch,
        "git_commit": git_info.commit,
        "git_dirty": git_info.dirty,
        "git_status_file": "git-status.txt",
        "generated_files": generated_files,
        "consolidated_skill_sources": skill_sources,
        "consolidated_template_sources": template_sources,
        "root_file_sources": sorted(copied_root_files, key=lambda item: item["target"]),
        "skipped_manifest_sources": sorted(skipped, key=lambda item: item["path"]),
        "warnings": sorted(set(warnings)),
        "counts": {
            "skills_consolidated": len(skill_sources),
            "templates_consolidated": len(template_sources),
            "root_files_copied": len(copied_root_files),
            "files_skipped": len(skipped),
            "warnings": len(set(warnings)),
        },
    }
    write_bytes(
        export_root / "export-manifest.json",
        json.dumps(export_manifest, indent=2, ensure_ascii=False) + "\n",
    )
    write_checksums(export_root, generated_files)

    print(f"Export package created: {export_root}")
    print(
        "Counts: "
        f"skills={len(skill_sources)}, "
        f"templates={len(template_sources)}, "
        f"root_files={len(copied_root_files)}, "
        f"skipped={len(skipped)}, "
        f"warnings={len(set(warnings))}"
    )
    if git_info.dirty:
        print("Git state: dirty")
    else:
        print("Git state: clean")

    return export_root


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    create_export(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
