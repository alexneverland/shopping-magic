#!/usr/bin/env python3
"""Install, update, or uninstall Shopping Magic without touching user data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile

NAME = "shopping-magic"
MARKER = ".shopping-magic-install.json"
FILES = (
    "SKILL.md",
    "VERSION",
    "LICENSE",
    "scripts/shopping_store.py",
    "references/storage-cli.md",
    "references/lists-and-purchases.md",
    "references/replenishment.md",
    "references/gifts-and-planning.md",
)
SOURCE = Path(__file__).resolve().parents[1] / "skills" / NAME


class InstallError(Exception):
    """Refuse an ambiguous or unsafe installation change."""


def destination(
    agent: str,
    scope: str,
    project: Path | None,
    custom: Path | None,
    home: Path | None = None,
) -> Path:
    """Map documented agent locations; custom agents must supply an exact destination."""
    if agent == "custom":
        if not custom or project:
            raise InstallError("custom_requires_destination_without_project")
        return custom.expanduser().absolute()
    if custom:
        raise InstallError("destination_is_only_for_custom_agents")
    if scope == "project":
        if project is None or not project.is_dir():
            raise InstallError("project_directory_required")
        base = project.absolute() / (".claude/skills" if agent == "claude-code" else ".agents/skills")
    else:
        if project:
            raise InstallError("project_requires_project_scope")
        roots = {
            "codex": ".agents/skills",
            "claude-code": ".claude/skills",
            "antigravity": ".gemini/config/skills",
        }
        base = (home or Path.home()) / roots[agent]
    return base / NAME


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_redirected(path: Path) -> bool:
    """Detect links and Windows reparse points without following them (Python 3.10+)."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if sys.platform == "win32" and not hasattr(info, "st_file_attributes"):
        raise InstallError("reparse_point_check_unavailable")
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def check_path(path: Path) -> Path:
    """Reject root, source-overlapping, and redirected targets before mutating disk."""
    absolute = path.expanduser().absolute()
    if absolute.name != NAME:
        raise InstallError("destination_folder_must_be_shopping-magic")
    for part in (absolute, *absolute.parents):
        if is_redirected(part):
            raise InstallError("redirected_destination_refused")
    resolved = absolute.resolve()
    source = SOURCE.resolve()
    if resolved == source or resolved in source.parents or source in resolved.parents:
        raise InstallError("source_destination_overlap")
    return resolved


def inspect_install(path: Path) -> None:
    """Verify ownership marker, exact manifest files, and SHA-256 digests."""
    if is_redirected(path) or is_redirected(path / MARKER):
        raise InstallError("redirected_install_content_refused")
    try:
        data = json.loads((path / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise InstallError("unmanaged_destination_refused") from None
    if not isinstance(data, dict) or data.get("name") != NAME or data.get("schema") != 1:
        raise InstallError("invalid_install_marker")
    hashes = data.get("files")
    if not isinstance(hashes, dict) or set(hashes) != set(FILES):
        raise InstallError("invalid_install_manifest")
    expected = set(hashes) | {MARKER}
    actual: set[str] = set()
    allowed_dirs = {"scripts", "references"}
    for entry in path.rglob("*"):
        if is_redirected(entry):
            raise InstallError("redirected_install_content_refused")
        rel = entry.relative_to(path).as_posix()
        if entry.is_file():
            actual.add(rel)
        elif rel not in allowed_dirs:
            raise InstallError("unexpected_install_content")
    if actual != expected or any(digest(path / rel) != hashes[rel] for rel in hashes):
        raise InstallError("modified_installation_refused_save_your_changes_first")


def remove_temporary(path: Path, parent: Path) -> None:
    """Delete only a verified sibling temporary directory created by this installer."""
    if is_redirected(path) or path.resolve().parent != parent.resolve() or not path.name.startswith(".shopping-magic-"):
        raise InstallError("unsafe_temporary_path")
    shutil.rmtree(path)


def stage_install(parent: Path) -> Path:
    stage = Path(tempfile.mkdtemp(prefix=".shopping-magic-stage-", dir=parent)).resolve()
    try:
        for rel in FILES:
            source = SOURCE / rel
            if not source.is_file() or is_redirected(source):
                raise InstallError("missing_or_redirected_source_file")
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        marker = {
            "name": NAME,
            "schema": 1,
            "version": (stage / "VERSION").read_text(encoding="utf-8").strip(),
            "files": {rel: digest(stage / rel) for rel in FILES},
        }
        (stage / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        return stage
    except Exception:
        remove_temporary(stage, parent)
        raise


def manage(action: str, target: Path) -> dict[str, str]:
    """Apply install, update, or uninstall safely while preserving all user data."""
    target = check_path(target)
    if action == "install":
        if target.exists():
            raise InstallError("destination_exists_use_update")
    else:
        if not target.is_dir():
            raise InstallError("installation_not_found")
        inspect_install(target)
    if action == "uninstall":
        shutil.rmtree(target)
        return {"action": action, "path": str(target), "user_data": "preserved"}

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = stage_install(target.parent)
    backup: Path | None = None
    try:
        if action == "update":
            backup = Path(tempfile.mkdtemp(prefix=".shopping-magic-backup-", dir=target.parent)).resolve()
            backup.rmdir()
            target.rename(backup)
        try:
            stage.rename(target)
        except Exception:
            if backup is not None:
                backup.rename(target)
                backup = None
            raise
    finally:
        if stage.exists():
            remove_temporary(stage, target.parent)

    result = {"action": action, "path": str(target), "user_data": "preserved"}
    if backup is not None:
        try:
            remove_temporary(backup, target.parent)
        except OSError:
            result["warning"] = f"Update completed; old install remains at {backup}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "update", "uninstall"))
    parser.add_argument("--agent", required=True, choices=("codex", "claude-code", "antigravity", "custom"))
    parser.add_argument("--scope", choices=("user", "project"), default="user")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        target = destination(args.agent, args.scope, args.project, args.destination)
        result = manage(args.action, target)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except (InstallError, OSError) as exc:
        error = str(exc) if isinstance(exc, InstallError) else "filesystem_error"
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
