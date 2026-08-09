#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rollback all three runtime components to a previously published release."""

import argparse
import datetime
import json
import sys

from compat import expand_path
from release_utils import atomic_save_json, load_release_active, load_release_file, pointer_path, release_root


def main():
    parser = argparse.ArgumentParser(description="Rollback the atomic AI咨询转化飞轮 release.")
    parser.add_argument("workspace_root")
    parser.add_argument("--version", help="target release version")
    parser.add_argument("--previous", action="store_true", help="rollback to the previous release")
    args = parser.parse_args()
    workspace = expand_path(args.workspace_root)
    active = load_release_active(workspace)
    current_version = active.get("release_version")
    target_version = args.version
    if args.previous:
        current = load_release_file(workspace, current_version) if current_version else None
        previous_id = (current or {}).get("previous_release_id")
        if previous_id:
            root = release_root(workspace) / "versions"
            for path in root.iterdir() if root.is_dir() else []:
                candidate = load_release_file(workspace, path.name)
                if candidate and candidate.get("release_id") == previous_id:
                    target_version = path.name
                    break
    if not target_version or target_version == current_version:
        print(json.dumps({"status": "rejected", "reason": "target_release_not_found_or_same", "current": current_version}, ensure_ascii=False))
        return 2
    target = load_release_file(workspace, target_version)
    if not target or target.get("status") != "active":
        print(json.dumps({"status": "rejected", "reason": "release_missing", "version": target_version}, ensure_ascii=False))
        return 2
    for component, snapshot in (target.get("components") or {}).items():
        pointer = snapshot.get("pointer")
        if not pointer:
            print(json.dumps({"status": "rejected", "reason": "component_pointer_missing", "component": component}, ensure_ascii=False))
            return 2
        atomic_save_json(pointer_path(workspace, component), dict(pointer, rolled_back_at=datetime.datetime.now().isoformat()))
    active.update({
        "status": "active",
        "release_id": target.get("release_id"),
        "release_version": target_version,
        "release_path": str(release_root(workspace) / "versions" / target_version / "release.json"),
        "rolled_back_at": datetime.datetime.now().isoformat(),
    })
    atomic_save_json(release_root(workspace) / "active.json", active)
    print(json.dumps({"status": "rolled_back", "release_id": target.get("release_id"), "release_version": target_version}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
