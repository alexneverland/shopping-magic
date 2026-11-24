"""Exercise the packaged distributable, cross-host sharing, and repository checkers."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def _load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(ROOT / "tools"))
builder = _load_tool("build_package")
checker = _load_tool("check_repository")
verifier = _load_tool("verify_scenarios")


class PackageTests(unittest.TestCase):
    def test_repository_checks_and_level1_scenarios_pass(self) -> None:
        self.assertEqual(checker.check(ROOT), [])
        report = verifier.run_level1()
        self.assertEqual(report["failed"], 0, report)
        self.assertEqual(verifier.run_level2()["status"], "not_executed")

    @unittest.skipUnless(os.name == "nt", "Windows default-profile cross-host sharing")
    def test_installed_codex_claude_and_antigravity_share_one_synthetic_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="shopping-magic-host-sharing-") as temp:
            base = Path(temp).resolve()
            archive = builder.build(base / "dist")
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(base / "extracted")
            version = (ROOT / "skills/shopping-magic/VERSION").read_text(encoding="utf-8").strip()
            extracted = base / "extracted" / f"shopping-magic-{version}"
            home = base / "synthetic-user"
            home.mkdir()
            environment = dict(os.environ, USERPROFILE=str(home), SHOPPING_MAGIC_DATA_DIR="")

            for host in ("codex", "claude-code", "antigravity"):
                run = subprocess.run(
                    [sys.executable, str(extracted / "tools/manage_skill.py"), "install", "--agent", host],
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

            scripts = {
                "codex": home / ".agents/skills/shopping-magic/scripts/shopping_store.py",
                "claude": home / ".claude/skills/shopping-magic/scripts/shopping_store.py",
                "antigravity": home / ".gemini/config/skills/shopping-magic/scripts/shopping_store.py",
            }

            def invoke(host: str, req: dict) -> dict:
                run = subprocess.run(
                    [sys.executable, str(scripts[host])],
                    input=json.dumps(req, ensure_ascii=False),
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    cwd=base,
                )
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                return json.loads(run.stdout)

            # Codex adds a need; Claude logs a purchase; Antigravity sees both and same profile_id.
            added = invoke("codex", {
                "action": "item-add",
                "request_key": "share-1",
                "name": "Milk",
                "list_name": "Supermarket",
                "list_kind": "supermarket",
                "quantity": "2",
                "unit": "liters",
            })["result"]
            invoke("claude", {
                "action": "purchase-log",
                "request_key": "share-2",
                "item_id": added["id"],
                "bought_on": "2026-11-24",
                "quantity": "1",
                "unit": "liter",
                "store": "Corner Store",
            })
            ctx_ag = invoke("antigravity", {"action": "context", "as_of": "2026-11-24"})
            ctx_codex = invoke("codex", {"action": "context", "as_of": "2026-11-24"})

            self.assertEqual(ctx_ag["storage"]["profile_id"], ctx_codex["storage"]["profile_id"])
            self.assertEqual(ctx_ag["storage"]["directory"], str(home / ".shopping-magic" / "data"))
            self.assertEqual(len(ctx_ag["result"]["open_items"]), 1)
            self.assertEqual(ctx_ag["result"]["open_items"][0]["remaining_quantity"], "1")
            self.assertEqual(ctx_ag["result"]["open_items"][0]["status"], "partially_bought")
            self.assertEqual(len(ctx_ag["result"]["recent_purchases"]), 1)

    def test_review_zip_has_only_public_files_and_can_install_and_restore(self) -> None:
        with tempfile.TemporaryDirectory(prefix="shopping-magic-package-test-") as temp:
            base = Path(temp).resolve()
            archive = builder.build(base / "dist")
            with zipfile.ZipFile(archive) as bundle:
                version = (ROOT / "skills/shopping-magic/VERSION").read_text(encoding="utf-8").strip()
                expected = {f"shopping-magic-{version}/" + rel for rel in builder.PUBLIC_FILES}
                self.assertEqual(set(bundle.namelist()), expected)
                self.assertIsNone(bundle.testzip())
                bundle.extractall(base / "extracted")
            extracted = base / f"extracted/shopping-magic-{version}"
            installed = base / "custom-agent/shopping-magic"
            p = subprocess.run(
                [sys.executable, str(extracted / "tools/manage_skill.py"), "install",
                 "--agent", "custom", "--destination", str(installed)],
                encoding="utf-8",
                capture_output=True,
            )
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertTrue((installed / "LICENSE").is_file())
            for name in ("storage-cli", "lists-and-purchases", "replenishment", "gifts-and-planning"):
                rel = f"references/{name}.md"
                self.assertEqual((installed / rel).read_bytes(), (extracted / "skills/shopping-magic" / rel).read_bytes())

            def invoke(data_dir: Path, req: dict) -> dict:
                run = subprocess.run(
                    [sys.executable, str(installed / "scripts/shopping_store.py"), "--data-dir", str(data_dir)],
                    input=json.dumps(req, ensure_ascii=False),
                    encoding="utf-8",
                    capture_output=True,
                )
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                return json.loads(run.stdout)["result"]

            prof = base / "synthetic-profile"
            item = invoke(prof, {
                "action": "item-add",
                "request_key": "pkg-1",
                "name": "Synthetic Coffee",
                "list_name": "Supermarket",
                "list_kind": "supermarket",
            })
            exported = invoke(prof, {"action": "export"})
            restored = base / "restored-profile"
            invoke(restored, {"action": "import", "request_key": "pkg-imp", "data": exported})
            self.assertEqual(invoke(restored, {"action": "export"}), exported)
            self.assertEqual(invoke(restored, {"action": "item-get", "id": item["id"]}), item)


if __name__ == "__main__":
    unittest.main()
