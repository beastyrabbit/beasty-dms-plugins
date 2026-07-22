#!/usr/bin/env python3
"""Atomically deploy tracked DMS plugins, restart DMS, and verify discovery."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from plugin_doctor import (
    deployment_errors,
    load_inventory,
    repository_errors,
    repository_root,
)


def command(*arguments: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def git_output(root: Path, *arguments: str) -> str:
    result = command("git", "-C", str(root), *arguments)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Git command failed")
    return result.stdout.strip()


def replace_symlink(path: Path, target: Path | str) -> None:
    temporary = path.with_name(f".{path.name}.new-{uuid.uuid4().hex}")
    os.symlink(str(target), temporary, target_is_directory=True)
    os.replace(temporary, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="deploy tracked working-tree modifications (intended only for migration)",
    )
    parser.add_argument(
        "--no-restart", action="store_true", help="deploy without restarting DMS"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repository_root()
    if not (root / ".git").is_dir():
        print(
            "Refusing to deploy from a linked/disposable worktree; use the primary checkout.",
            file=sys.stderr,
        )
        return 1

    plugins = load_inventory(root)
    errors = repository_errors(root, plugins)
    if errors:
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    plugin_paths = [plugin.directory for plugin in plugins]
    dirty = git_output(
        root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        *plugin_paths,
    )
    if dirty and not args.allow_dirty:
        print(
            "Refusing to deploy uncommitted plugin changes. Commit them first or use "
            "--allow-dirty for an intentional migration.",
            file=sys.stderr,
        )
        print(dirty, file=sys.stderr)
        return 1

    dms_plugin_dir = Path(
        os.environ.get(
            "DMS_PLUGIN_DIR",
            Path.home() / ".config" / "DankMaterialShell" / "plugins",
        )
    ).expanduser()
    deployment_root = Path(
        os.environ.get(
            "DMS_PLUGIN_DEPLOY_ROOT",
            Path.home() / ".local" / "share" / "dms-plugins" / "beasty-dms-plugins",
        )
    ).expanduser()
    dms_plugin_dir.mkdir(parents=True, exist_ok=True)
    deployment_root.mkdir(parents=True, exist_ok=True)

    revision = git_output(root, "rev-parse", "--short=12", "HEAD")
    suffix = "-dirty" if dirty else ""
    release_name = (
        f"{time.strftime('%Y%m%d-%H%M%S')}-{revision}{suffix}-{uuid.uuid4().hex[:8]}"
    )
    release = deployment_root / release_name
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=deployment_root))
    current = deployment_root / "current"
    old_current = os.readlink(current) if current.is_symlink() else None
    old_links = {
        plugin.directory: os.readlink(dms_plugin_dir / plugin.directory)
        if (dms_plugin_dir / plugin.directory).is_symlink()
        else None
        for plugin in plugins
    }

    try:
        for plugin in plugins:
            shutil.copytree(
                root / plugin.directory,
                staging / plugin.directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        staging.rename(release)
        replace_symlink(current, release.name)
        for plugin in plugins:
            replace_symlink(
                dms_plugin_dir / plugin.directory,
                current / plugin.directory,
            )

        deployed_errors = deployment_errors(
            root, plugins, dms_plugin_dir, deployment_root
        )
        if deployed_errors:
            raise RuntimeError("; ".join(deployed_errors))

        if not args.no_restart:
            restart = command("dms", "restart")
            if restart.returncode != 0:
                raise RuntimeError(restart.stderr.strip() or "dms restart failed")
            deadline = time.monotonic() + 12
            missing = [plugin.plugin_id for plugin in plugins]
            while time.monotonic() < deadline:
                listing = command("dms", "plugins", "list")
                output = (listing.stdout + listing.stderr).lower()
                missing = [
                    plugin.plugin_id
                    for plugin in plugins
                    if plugin.plugin_id.lower() not in output
                ]
                if listing.returncode == 0 and not missing:
                    break
                time.sleep(0.5)
            if missing:
                raise RuntimeError(
                    "DMS did not discover deployed plugins: " + ", ".join(missing)
                )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        if old_current is None:
            current.unlink(missing_ok=True)
        else:
            replace_symlink(current, old_current)
        for plugin in plugins:
            link = dms_plugin_dir / plugin.directory
            old_target = old_links[plugin.directory]
            if old_target is None:
                link.unlink(missing_ok=True)
            else:
                replace_symlink(link, old_target)
        if not args.no_restart:
            command("dms", "restart")
        print(f"Deployment failed and links were rolled back: {error}", file=sys.stderr)
        return 1
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    print(f"Deployed {len(plugins)} plugins to {release}")
    if args.no_restart:
        print("DMS restart skipped")
    else:
        print("DMS restarted and all expected plugin IDs were discovered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
