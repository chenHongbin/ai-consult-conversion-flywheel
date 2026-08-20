#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Switch the workspace runtime pointer to an existing capability version."""

import argparse
import datetime
import io
import json
import re
import sys

from compat import ensure_dir, expand_path
from workspace_paths import locate_workspace


def load_json(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path, value):
    ensure_dir(path.parent)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def version_number(value):
    match = re.match(r"v(\d+)\.(\d+)", value)
    return (int(match.group(1)), int(match.group(2))) if match else (-1, -1)


def main():
    parser = argparse.ArgumentParser(description="Rollback the active institution capability package.")
    parser.add_argument("workspace_root")
    parser.add_argument("--version", help="version such as v0.1")
    parser.add_argument("--previous", action="store_true", help="switch to the highest version below the current one")
    args = parser.parse_args()
    workspace = expand_path(args.workspace_root)
    root = locate_workspace(workspace) / "_系统" / "当前能力包"
    active_path = root / "active.json"
    active = load_json(active_path) if active_path.is_file() else {}
    current = active.get("active_version")
    versions_root = root / "versions"
    versions = sorted([path.name for path in versions_root.iterdir() if path.is_dir() and path.name.startswith("v")], key=version_number) if versions_root.is_dir() else []
    target = args.version
    if args.previous:
        eligible = [value for value in versions if version_number(value) < version_number(str(current))]
        target = eligible[-1] if eligible else None
    if not target or target not in versions:
        print(json.dumps({"status": "rejected", "current": current, "available_versions": versions}, ensure_ascii=False))
        return 2
    package_path = root / "versions" / target / "package.json"
    runtime_path = root / "versions" / target / "runtime-context.md"
    if not package_path.is_file() or not runtime_path.is_file():
        print(json.dumps({"status": "rejected", "reason": "version_artifacts_missing", "version": target}, ensure_ascii=False))
        return 2
    active.update({"status": "active", "active_version": target,
                   "package_path": str(package_path), "runtime_context_path": str(runtime_path),
                   "rolled_back_at": datetime.datetime.now().isoformat()})
    save_json(active_path, active)
    print(json.dumps({"status": "rolled_back", "active_version": target, "active_path": str(active_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
