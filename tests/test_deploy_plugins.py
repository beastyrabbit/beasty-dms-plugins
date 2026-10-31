from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import deploy_plugins


def git(root: Path, *arguments: str) -> str:
    # Git hooks export GIT_DIR and friends, which would point at this repository.
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    ).stdout.strip()


class LiveReleaseGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root = base / "repo"
        self.deploy_root = base / "deploy"
        self.plugin_dir = base / "plugins"
        (self.root / "Widget").mkdir(parents=True)
        (self.root / "Widget" / "Widget.qml").write_text("v1\n", encoding="utf-8")
        (self.root / "plugins.json").write_text(
            '{"plugins": [{"directory": "Widget", "id": "widget"}]}',
            encoding="utf-8",
        )
        git(self.root, "init", "-q")
        git(self.root, "add", ".")
        git(
            self.root,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "init",
        )

    def deploy(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = StringIO(), StringIO()
        environment = {
            "DMS_PLUGIN_DEPLOY_ROOT": str(self.deploy_root),
            "DMS_PLUGIN_DIR": str(self.plugin_dir),
        }
        with (
            patch.dict(os.environ, environment),
            patch.object(deploy_plugins, "repository_root", return_value=self.root),
            patch.object(deploy_plugins, "repository_errors", return_value=[]),
            patch.object(deploy_plugins, "deployment_errors", return_value=[]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = deploy_plugins.main(["--no-restart", *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def make_live(self, name: str, content: str) -> None:
        (self.deploy_root / name / "Widget").mkdir(parents=True)
        (self.deploy_root / name / "Widget" / "Widget.qml").write_text(
            content, encoding="utf-8"
        )
        current = self.deploy_root / "current"
        current.unlink(missing_ok=True)
        current.symlink_to(name)

    def test_redeploys_over_a_release_that_head_contains(self) -> None:
        self.assertEqual(self.deploy()[0], 0)

        code, stdout, _ = self.deploy()

        self.assertEqual(code, 0)
        self.assertIn("this deploy changes: no plugin files", stdout)

    def test_refuses_to_replace_an_unmerged_preview(self) -> None:
        name = "20261031-201300-5b4f63003ea0-f73264ea"
        self.make_live(name, "preview\n")

        code, stdout, stderr = self.deploy()

        self.assertEqual(code, 1)
        self.assertIn("this deploy changes: Widget", stdout)
        self.assertIn("5b4f63003ea0 is not part of HEAD", stderr)
        self.assertEqual(os.readlink(self.deploy_root / "current"), name)

    def test_refuses_to_replace_a_dirty_release(self) -> None:
        revision = git(self.root, "rev-parse", "--short=12", "HEAD")
        name = f"20261031-203337-{revision}-dirty-6669a7e0"
        self.make_live(name, "v1\n")

        code, _, stderr = self.deploy()

        self.assertEqual(code, 1)
        self.assertIn("deployed with uncommitted changes", stderr)
        self.assertEqual(os.readlink(self.deploy_root / "current"), name)

    def test_replace_live_overrides_the_guard(self) -> None:
        self.make_live("20261031-201300-5b4f63003ea0-f73264ea", "preview\n")

        code, _, _ = self.deploy("--replace-live")

        self.assertEqual(code, 0)
        live = self.deploy_root / "current" / "Widget" / "Widget.qml"
        self.assertEqual(live.read_text(encoding="utf-8"), "v1\n")


if __name__ == "__main__":
    unittest.main()
