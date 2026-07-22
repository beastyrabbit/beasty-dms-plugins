#!/usr/bin/env python3
"""Validate the repository and its live DMS plugin deployment."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any

import jsonschema


INVENTORY_NAME = "plugins.json"
SCHEMA = Path("/usr/share/quickshell/dms/PLUGINS/plugin-schema.json")
IGNORED_FILE_NAMES = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc"}


@dataclass(frozen=True)
class Plugin:
    directory: str
    plugin_id: str


def run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def repository_root() -> Path:
    result = run_git(Path.cwd(), "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise RuntimeError("not inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def parse_inventory(raw: str) -> list[Plugin]:
    payload = json.loads(raw)
    entries = payload.get("plugins") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("plugins.json must contain a non-empty plugins array")

    plugins: list[Plugin] = []
    seen_directories: set[str] = set()
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("every plugins.json entry must be an object")
        directory = entry.get("directory")
        plugin_id = entry.get("id")
        if (
            not isinstance(directory, str)
            or not directory
            or Path(directory).name != directory
        ):
            raise ValueError(f"invalid plugin directory: {directory!r}")
        if not isinstance(plugin_id, str) or not plugin_id:
            raise ValueError(f"invalid plugin id for {directory}: {plugin_id!r}")
        if directory in seen_directories:
            raise ValueError(f"duplicate plugin directory: {directory}")
        if plugin_id in seen_ids:
            raise ValueError(f"duplicate plugin id: {plugin_id}")
        seen_directories.add(directory)
        seen_ids.add(plugin_id)
        plugins.append(Plugin(directory, plugin_id))
    return plugins


def load_inventory(root: Path) -> list[Plugin]:
    return parse_inventory((root / INVENTORY_NAME).read_text(encoding="utf-8"))


def referenced_paths(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value[2:]] if value.startswith("./") else []
    if isinstance(value, dict):
        paths: list[str] = []
        for nested in value.values():
            paths.extend(referenced_paths(nested))
        return paths
    if isinstance(value, list):
        paths = []
        for nested in value:
            paths.extend(referenced_paths(nested))
        return paths
    return []


def load_schema() -> dict[str, Any]:
    if not SCHEMA.is_file():
        raise RuntimeError(f"DMS plugin schema not found: {SCHEMA}")
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def validate_manifest(
    manifest: dict[str, Any], plugin: Plugin, file_exists: Callable[[str], bool]
) -> list[str]:
    errors: list[str] = []
    try:
        jsonschema.validate(manifest, load_schema())
    except jsonschema.ValidationError as error:
        errors.append(f"{plugin.directory}/plugin.json: {error.message}")
    if manifest.get("id") != plugin.plugin_id:
        errors.append(
            f"{plugin.directory}/plugin.json has id {manifest.get('id')!r}; "
            f"expected {plugin.plugin_id!r}"
        )
    for relative_path in referenced_paths(manifest):
        if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
            errors.append(
                f"{plugin.directory}/plugin.json has unsafe path {relative_path!r}"
            )
        elif not file_exists(relative_path):
            errors.append(
                f"{plugin.directory}/plugin.json references missing file {relative_path}"
            )
    return errors


def repository_errors(root: Path, plugins: list[Plugin]) -> list[str]:
    errors: list[str] = []
    tracked_result = run_git(root, "ls-files", "-z")
    if tracked_result.returncode != 0:
        return [tracked_result.stderr.strip() or "could not inspect tracked files"]
    tracked = {path for path in tracked_result.stdout.split("\0") if path}
    if INVENTORY_NAME not in tracked:
        errors.append(f"{INVENTORY_NAME} is not tracked by Git")

    for plugin in plugins:
        directory = root / plugin.directory
        manifest_path = directory / "plugin.json"
        if not manifest_path.is_file():
            errors.append(f"{plugin.directory}/plugin.json is missing")
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            errors.append(f"{plugin.directory}/plugin.json: {error}")
            continue
        errors.extend(
            validate_manifest(
                manifest,
                plugin,
                lambda relative, base=directory: (base / relative).is_file(),
            )
        )
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if any(part in IGNORED_FILE_NAMES for part in path.parts):
                continue
            if path.suffix in IGNORED_SUFFIXES:
                continue
            relative = path.relative_to(root).as_posix()
            if relative not in tracked:
                errors.append(f"plugin file is not tracked by Git: {relative}")
    return errors


def head_errors(root: Path) -> list[str]:
    inventory_result = run_git(root, "show", f"HEAD:{INVENTORY_NAME}")
    if inventory_result.returncode != 0:
        return [f"HEAD does not contain {INVENTORY_NAME}"]
    try:
        plugins = parse_inventory(inventory_result.stdout)
    except (json.JSONDecodeError, ValueError) as error:
        return [f"HEAD:{INVENTORY_NAME}: {error}"]

    errors: list[str] = []
    for plugin in plugins:
        manifest_name = f"{plugin.directory}/plugin.json"
        manifest_result = run_git(root, "show", f"HEAD:{manifest_name}")
        if manifest_result.returncode != 0:
            errors.append(f"HEAD does not contain {manifest_name}")
            continue
        try:
            manifest = json.loads(manifest_result.stdout)
        except json.JSONDecodeError as error:
            errors.append(f"HEAD:{manifest_name}: {error}")
            continue

        def exists_in_head(relative: str, directory: str = plugin.directory) -> bool:
            result = run_git(root, "cat-file", "-e", f"HEAD:{directory}/{relative}")
            return result.returncode == 0

        errors.extend(validate_manifest(manifest, plugin, exists_in_head))
    return errors


def deployment_errors(
    root: Path,
    plugins: list[Plugin],
    dms_plugin_dir: Path,
    deployment_root: Path,
) -> list[str]:
    del root
    errors: list[str] = []
    if not dms_plugin_dir.is_dir():
        return [f"DMS plugin directory is missing: {dms_plugin_dir}"]

    for entry in dms_plugin_dir.iterdir():
        if not entry.is_symlink():
            continue
        raw_target = os.readlink(entry)
        if "orca/workspaces" in raw_target:
            errors.append(f"{entry} points into a disposable Orca worktree")
        if not entry.exists():
            errors.append(f"dangling DMS plugin symlink: {entry} -> {raw_target}")

    for plugin in plugins:
        link = dms_plugin_dir / plugin.directory
        expected = deployment_root / "current" / plugin.directory
        if not link.is_symlink():
            errors.append(f"DMS plugin link is missing: {link}")
            continue
        if link.resolve(strict=False) != expected.resolve(strict=False):
            errors.append(
                f"{link} points to {os.readlink(link)}; expected stable deployment {expected}"
            )
        if not (link / "plugin.json").is_file():
            errors.append(f"deployed manifest is missing through {link}")
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-only", action="store_true", help="skip live DMS deployment checks"
    )
    parser.add_argument(
        "--head",
        action="store_true",
        help="validate committed HEAD instead of the index",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = repository_root()
        if args.head:
            errors = head_errors(root)
        else:
            plugins = load_inventory(root)
            errors = repository_errors(root, plugins)
            if not args.repo_only:
                dms_plugin_dir = Path(
                    os.environ.get(
                        "DMS_PLUGIN_DIR",
                        Path.home() / ".config" / "DankMaterialShell" / "plugins",
                    )
                ).expanduser()
                deployment_root = Path(
                    os.environ.get(
                        "DMS_PLUGIN_DEPLOY_ROOT",
                        Path.home()
                        / ".local"
                        / "share"
                        / "dms-plugins"
                        / "beasty-dms-plugins",
                    )
                ).expanduser()
                errors.extend(
                    deployment_errors(root, plugins, dms_plugin_dir, deployment_root)
                )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        errors = [str(error)]

    if errors:
        print("DMS plugin doctor found problems:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    scope = "committed HEAD" if args.head else "repository"
    if not args.repo_only and not args.head:
        scope += " and live deployment"
    print(f"DMS plugin doctor: {scope} healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
