#!/usr/bin/env python3
"""Build a review ZIP from an explicit public-file allowlist, never user data."""
from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = (
    "README.md",
    "LICENSE",
    "VERIFICATION.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    ".gitignore",
    ".gitattributes",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/dependabot.yml",
    ".github/workflows/ci.yml",
    "skills/shopping-magic/SKILL.md",
    "skills/shopping-magic/VERSION",
    "skills/shopping-magic/LICENSE",
    "skills/shopping-magic/scripts/shopping_store.py",
    "skills/shopping-magic/references/storage-cli.md",
    "skills/shopping-magic/references/lists-and-purchases.md",
    "skills/shopping-magic/references/replenishment.md",
    "skills/shopping-magic/references/gifts-and-planning.md",
    "tools/manage_skill.py",
    "tools/build_package.py",
    "tools/check_repository.py",
    "tools/verify_scenarios.py",
    "tests/test_lists_and_items.py",
    "tests/test_purchases_and_history.py",
    "tests/test_replenishment.py",
    "tests/test_gifts_and_planning.py",
    "tests/test_storage_safety.py",
    "tests/test_install.py",
    "tests/test_package.py",
    "tests/agent-scenarios.md",
)


def build(output_dir: Path, root: Path = ROOT) -> Path:
    """Package known source files only; output never includes user databases or caches."""
    output_dir = output_dir.resolve()
    version = (root / "skills/shopping-magic/VERSION").read_text(encoding="utf-8").strip()
    if not version or any(c not in "0123456789." for c in version):
        raise ValueError("invalid_package_version")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"shopping-magic-{version}.zip"
    for rel in PUBLIC_FILES:
        source = root / rel
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"missing_or_redirected_public_file: {rel}")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for rel in PUBLIC_FILES:
            bundle.write(root / rel, arcname=f"shopping-magic-{version}/{rel}")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    return archive


if __name__ == "__main__":
    print(build(ROOT / "dist"))
