"""Lifecycle tests using temporary agent homes/projects and synthetic data only."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("manage_skill", ROOT / "tools/manage_skill.py")
installer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="shopping-magic-install-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()

    def test_user_locations_are_agent_specific(self) -> None:
        expected = {
            "codex": ".agents/skills/shopping-magic",
            "claude-code": ".claude/skills/shopping-magic",
            "antigravity": ".gemini/config/skills/shopping-magic",
        }
        for agent, rel in expected.items():
            self.assertEqual(installer.destination(agent, "user", None, None, home=self.base), self.base / rel)

    def test_new_install_contains_all_workflow_references(self) -> None:
        target = self.base / "shopping-magic"
        installer.manage("install", target)
        for name in ("storage-cli", "lists-and-purchases", "replenishment", "gifts-and-planning"):
            self.assertEqual(
                (target / f"references/{name}.md").read_bytes(),
                (installer.SOURCE / f"references/{name}.md").read_bytes(),
            )

    def test_full_lifecycle_for_each_agent_preserves_data(self) -> None:
        for agent in ("codex", "claude-code", "antigravity", "custom"):
            with self.subTest(agent=agent):
                project = self.base / agent
                project.mkdir()
                target = (
                    installer.destination(agent, "project", project, None)
                    if agent != "custom"
                    else installer.destination(agent, "user", None, project / "skills/shopping-magic")
                )
                data = project / "private-user-data"
                installer.manage("install", target)
                script = target / "scripts/shopping_store.py"
                req = {
                    "action": "item-add",
                    "request_key": "inst-1",
                    "name": "Synthetic Coffee",
                    "list_name": "Supermarket",
                    "list_kind": "supermarket",
                }
                run = subprocess.run(
                    [sys.executable, str(script), "--data-dir", str(data)],
                    input=json.dumps(req),
                    encoding="utf-8",
                    capture_output=True,
                )
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                before = (data / "shopping-magic.sqlite3").read_bytes()
                installer.manage("update", target)
                self.assertEqual((data / "shopping-magic.sqlite3").read_bytes(), before)
                ctx = subprocess.run(
                    [sys.executable, str(script), "--data-dir", str(data)],
                    input='{"action":"context"}',
                    encoding="utf-8",
                    capture_output=True,
                )
                self.assertEqual(json.loads(ctx.stdout)["result"]["open_items"][0]["name"], "Synthetic Coffee")
                after = (data / "shopping-magic.sqlite3").read_bytes()
                installer.manage("uninstall", target)
                self.assertFalse(target.exists())
                self.assertEqual((data / "shopping-magic.sqlite3").read_bytes(), after)

    def test_install_refuses_existing_folder_and_modified_or_unknown_content(self) -> None:
        unmanaged = self.base / "unmanaged/shopping-magic"
        unmanaged.mkdir(parents=True)
        (unmanaged / "personal.txt").write_text("synthetic private file", encoding="utf-8")
        for action in ("install", "update", "uninstall"):
            with self.subTest(action=action), self.assertRaises(installer.InstallError):
                installer.manage(action, unmanaged)
        self.assertTrue((unmanaged / "personal.txt").exists())

        managed = self.base / "managed/shopping-magic"
        installer.manage("install", managed)
        (managed / "SKILL.md").write_text("synthetic local edit", encoding="utf-8")
        for action in ("update", "uninstall"):
            with self.assertRaises(installer.InstallError):
                installer.manage(action, managed)
        self.assertEqual((managed / "SKILL.md").read_text(encoding="utf-8"), "synthetic local edit")

    def test_partial_manifest_and_extra_data_refused(self) -> None:
        target = self.base / "shopping-magic"
        installer.manage("install", target)
        marker = target / installer.MARKER
        data = json.loads(marker.read_text(encoding="utf-8"))
        data["files"].pop("references/replenishment.md", None)
        marker.write_text(json.dumps(data), encoding="utf-8")
        for action in ("update", "uninstall"):
            with self.assertRaisesRegex(installer.InstallError, "invalid_install_manifest"):
                installer.manage(action, target)
        self.assertTrue((target / "SKILL.md").exists())

    def test_invalid_destination_and_source_overlap(self) -> None:
        for target in (self.base, installer.SOURCE, installer.SOURCE / "nested/shopping-magic"):
            with self.subTest(target=target), self.assertRaises(installer.InstallError):
                installer.manage("install", target)
        with self.assertRaises(installer.InstallError):
            installer.destination("custom", "user", None, None)

    def test_update_rolls_back_if_staged_rename_fails(self) -> None:
        target = self.base / "shopping-magic"
        installer.manage("install", target)
        original = (target / "SKILL.md").read_bytes()
        rename = Path.rename

        def fail_stage(path: Path, dest: Path) -> Path:
            if path.name.startswith(".shopping-magic-stage-"):
                raise OSError("synthetic rename failure")
            return rename(path, dest)

        with patch.object(Path, "rename", fail_stage), self.assertRaises(OSError):
            installer.manage("update", target)
        self.assertEqual((target / "SKILL.md").read_bytes(), original)
        installer.inspect_install(target)
        self.assertFalse(list(self.base.glob(".shopping-magic-*")))

    def test_cli_custom_install_and_uninstall(self) -> None:
        target = self.base / "shopping-magic"
        for action in ("install", "uninstall"):
            p = subprocess.run(
                [sys.executable, str(ROOT / "tools/manage_skill.py"), action,
                 "--agent", "custom", "--destination", str(target)],
                encoding="utf-8",
                capture_output=True,
            )
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertTrue(json.loads(p.stdout)["ok"])

    def junction(self, path: Path, target: Path) -> None:
        if self.base not in path.absolute().parents or self.base not in target.resolve().parents:
            raise AssertionError("junction paths must stay inside test workspace")
        path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, SM_TEST_LINK=str(path), SM_TEST_TARGET=str(target))
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "New-Item -ItemType Junction -Path $env:SM_TEST_LINK -Target $env:SM_TEST_TARGET -ErrorAction Stop | Out-Null",
            ],
            env=env,
            capture_output=True,
            check=True,
        )

        def remove_junction() -> None:
            try:
                path.rmdir()
            except FileNotFoundError:
                pass

        self.addCleanup(remove_junction)

    @unittest.skipUnless(sys.platform == "win32", "Windows junction regression")
    def test_junction_target_and_ancestor_refused(self) -> None:
        for placement in ("target", "ancestor"):
            for action in ("install", "update", "uninstall"):
                with self.subTest(placement=placement, action=action):
                    case = self.base / (placement + "-" + action)
                    actual = case / "outside/shopping-magic"
                    installer.manage("install", actual)
                    before = {p.relative_to(actual).as_posix(): p.read_bytes()
                              for p in actual.rglob("*") if p.is_file()}
                    if placement == "target":
                        link = case / "chosen/shopping-magic"
                        self.junction(link, actual)
                        chosen = link
                    else:
                        link = case / "chosen"
                        self.junction(link, actual.parent)
                        chosen = link / "shopping-magic"
                    with self.assertRaisesRegex(installer.InstallError, "redirected_destination_refused"):
                        installer.manage(action, chosen)
                    self.assertEqual(
                        {p.relative_to(actual).as_posix(): p.read_bytes() for p in actual.rglob("*") if p.is_file()},
                        before,
                    )

    @unittest.skipUnless(sys.platform == "win32", "Windows junction regression")
    def test_junction_inside_owned_installation_refused(self) -> None:
        actual = self.base / "shopping-magic"
        installer.manage("install", actual)
        script = actual / "scripts/shopping_store.py"
        content = script.read_bytes()
        external = self.base / "external-scripts"
        external.mkdir()
        (external / "shopping_store.py").write_bytes(content)
        script.unlink()
        script.parent.rmdir()
        self.junction(actual / "scripts", external)
        for action in ("update", "uninstall"):
            with self.subTest(action=action), self.assertRaisesRegex(
                installer.InstallError, "redirected_install_content_refused"
            ):
                installer.manage(action, actual)
        self.assertEqual((external / "shopping_store.py").read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
