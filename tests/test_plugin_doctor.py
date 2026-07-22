from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from plugin_doctor import (  # noqa: E402
    Plugin,
    deployment_errors,
    parse_inventory,
    referenced_paths,
)


class InventoryTests(unittest.TestCase):
    def test_parses_inventory(self) -> None:
        plugins = parse_inventory(
            '{"plugins": [{"directory": "QuickAI", "id": "quickAi"}]}'
        )

        self.assertEqual(plugins, [Plugin("QuickAI", "quickAi")])

    def test_rejects_duplicate_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate plugin id"):
            parse_inventory(
                """{
                    "plugins": [
                        {"directory": "One", "id": "same"},
                        {"directory": "Two", "id": "same"}
                    ]
                }"""
            )

    def test_rejects_nested_directories(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid plugin directory"):
            parse_inventory(
                '{"plugins": [{"directory": "../QuickAI", "id": "quickAi"}]}'
            )

    def test_finds_nested_manifest_paths(self) -> None:
        value = {
            "component": "./Widget.qml",
            "components": {"daemon": "./Daemon.qml"},
            "other": ["plain", "./Startup.qml"],
        }

        self.assertEqual(
            referenced_paths(value), ["Widget.qml", "Daemon.qml", "Startup.qml"]
        )


class DeploymentTests(unittest.TestCase):
    def test_accepts_stable_deployment_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            dms_plugins = base / "dms"
            deployment = base / "deployment"
            deployed_plugin = deployment / "release" / "QuickAI"
            deployed_plugin.mkdir(parents=True)
            dms_plugins.mkdir()
            (deployed_plugin / "plugin.json").write_text("{}", encoding="utf-8")
            os.symlink("release", deployment / "current", target_is_directory=True)
            os.symlink(
                deployment / "current" / "QuickAI",
                dms_plugins / "QuickAI",
                target_is_directory=True,
            )

            errors = deployment_errors(
                base,
                [Plugin("QuickAI", "quickAi")],
                dms_plugins,
                deployment,
            )

        self.assertEqual(errors, [])

    def test_rejects_disposable_and_dangling_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            dms_plugins = base / "dms"
            dms_plugins.mkdir()
            os.symlink(
                "/home/example/orca/workspaces/project/branch/QuickAI",
                dms_plugins / "QuickAI",
                target_is_directory=True,
            )

            errors = deployment_errors(
                base,
                [Plugin("QuickAI", "quickAi")],
                dms_plugins,
                base / "deployment",
            )

        self.assertTrue(
            any("disposable Orca worktree" in error for error in errors), errors
        )
        self.assertTrue(any("dangling" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
