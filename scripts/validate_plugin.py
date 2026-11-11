#!/usr/bin/env python3
"""Validate DMS plugin manifests against the installed DMS schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
from plugin_doctor import load_schema


def main(paths: list[str]) -> int:
    try:
        schema = load_schema()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
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
