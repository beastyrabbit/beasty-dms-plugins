#!/usr/bin/env python3
"""Validate DMS plugin manifests against the installed DMS schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema


SCHEMA = Path("/usr/share/quickshell/dms/PLUGINS/plugin-schema.json")


def main(paths: list[str]) -> int:
    if not SCHEMA.is_file():
        print(f"DMS plugin schema not found: {SCHEMA}", file=sys.stderr)
        return 1
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    failed = False
    for raw_path in paths:
        path = Path(raw_path)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            jsonschema.validate(manifest, schema)
        except (OSError, json.JSONDecodeError, jsonschema.ValidationError) as error:
            print(f"{path}: {error}", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
