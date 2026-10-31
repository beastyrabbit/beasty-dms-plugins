#!/usr/bin/env python3
"""Atomically deploy tracked DMS plugins, restart DMS, and verify discovery."""

from __future__ import annotations

import argparse
import os
import re
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
    plugin_dir,
    repository_errors,
    repository_root,
)

RELEASE_NAME = re.compile(
    r"^\d{8}-\d{6}-(?P<revision>[0-9a-f]+)(?P<dirty>-dirty)?-[0-9a-f]{8}$"
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


def new_release_name(revision: str, dirty: bool) -> str:
    suffix = "-dirty" if dirty else ""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{revision}{suffix}-{uuid.uuid4().hex[:8]}"


def plugin_files(path: Path) -> dict[Path, bytes]:
    if not path.is_dir():
        return {}
    return {
        file.relative_to(path): file.read_bytes()
        for file in path.rglob("*")
        if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc"
    }


def changed_plugins(root: Path, live: Path, directories: list[str]) -> list[str]:
    return [
        directory
        for directory in directories
        if plugin_files(root / directory) != plugin_files(live / directory)
    ]


def live_conflict(root: Path, release_name: str) -> str | None:
    """Explain why HEAD may not contain the live release, or return None."""
    match = RELEASE_NAME.match(release_name)
    if match is None:
        return "its name records no revision"
    if match["dirty"]:
        return "it was deployed with uncommitted changes"
    revision = match["revision"]
    ancestor = command(
        "git", "-C", str(root), "merge-base", "--is-ancestor", revision, "HEAD"
    )
    if ancestor.returncode != 0:
        return f"its revision {revision} is not part of HEAD"
    return None


def may_replace_live(
    root: Path, current: Path, directories: list[str], replace_live: bool
) -> bool:
    """Print what this deploy changes and refuse to drop work HEAD lacks."""
    if not current.is_symlink():
        return True
    live = current.parent / os.readlink(current)
    changed = changed_plugins(root, live, directories)
    print(
        f"Live release {live.name}; this deploy changes: "
        + (", ".join(changed) or "no plugin files")
    )
    conflict = live_conflict(root, live.name)
    if conflict is None or replace_live:
        return True
    print(
        f"Refusing to replace live release {live.name}: {conflict}. It may hold "
        "another thread's unmerged preview, which this deploy would remove. Rerun "
        "with --replace-live once that work is merged or its owner agrees.",
        file=sys.stderr,
    )
    return False


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
        "--replace-live",
        action="store_true",
        help="replace a live release that HEAD does not contain, such as a preview",
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

    dms_plugin_dir = plugin_dir()
    deployment_root = Path(
        os.environ.get(
            "DMS_PLUGIN_DEPLOY_ROOT",
            Path.home() / ".local" / "share" / "dms-plugins" / "beasty-dms-plugins",
        )
    ).expanduser()
    current = deployment_root / "current"
    if not may_replace_live(root, current, plugin_paths, args.replace_live):
        return 1
    dms_plugin_dir.mkdir(parents=True, exist_ok=True)
    deployment_root.mkdir(parents=True, exist_ok=True)

    revision = git_output(root, "rev-parse", "--short=12", "HEAD")
    release = deployment_root / new_release_name(revision, bool(dirty))
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=deployment_root))
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
