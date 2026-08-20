#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Atomically publish all four trusted runtime components."""

import argparse
import datetime
import hashlib
import json
import sys

from compat import ensure_dir, expand_path
from content_runtime import compile_content_runtime
from release_utils import atomic_save_json, component_snapshot, load_release_active, release_root, scope_conflicts, sha256, validate_version


def main():
    parser = argparse.ArgumentParser(description="Publish one atomic AI咨询转化飞轮 runtime release.")
    parser.add_argument("workspace_root")
    parser.add_argument("--version", help="human-readable release version, e.g. v1.8")
    args = parser.parse_args()
    workspace = expand_path(args.workspace_root)
    try:
        snapshots = [component_snapshot(workspace, name) for name in ("capability", "knowledge", "patient_insight")]
    except ValueError as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False))
        return 2
    if snapshots[0].get("status") != "active":
        print(json.dumps({"status": "rejected", "reason": "capability_component_not_active"}, ensure_ascii=False))
        return 2
    conflicts = scope_conflicts(snapshots)
    if conflicts:
        print(json.dumps({"status": "rejected", "reason": "component_scope_conflict", "errors": conflicts}, ensure_ascii=False))
        return 2
    active_before = load_release_active(workspace)
    now = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        version = validate_version(args.version or "r" + now)
    except ValueError as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False))
        return 2
    release_root_path = release_root(workspace)
    release_dir = release_root_path / "versions" / version
    if (release_dir / "release.json").is_file():
        print(json.dumps({"status": "rejected", "reason": "release_version_already_exists", "version": version}, ensure_ascii=False))
        return 2
    ensure_dir(release_dir)
    try:
        snapshots.append(compile_content_runtime(workspace, release_dir, version))
    except ValueError as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False))
        return 2
    conflicts = scope_conflicts(snapshots)
    if conflicts:
        print(json.dumps({"status": "rejected", "reason": "component_scope_conflict", "errors": conflicts}, ensure_ascii=False))
        return 2
    component_hashes = {item["component"]: (item["package_hash"], item["runtime_hash"]) for item in snapshots}
    release_id = "release-" + hashlib.sha256(json.dumps({"version": version, "components": component_hashes}, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    release = {
        "schema_version": "2.1.3-release",
        "release_id": release_id,
        "release_version": version,
        "status": "active",
        "created_at": datetime.datetime.now().isoformat(),
        "previous_release_id": active_before.get("release_id"),
        "scope": next((item.get("scope") for item in snapshots if item.get("status") == "active" and item.get("scope")), {}),
        "components": {item["component"]: item for item in snapshots},
        "component_hashes": component_hashes,
        "contains_raw_patient_material": False,
        "contains_patient_level_profiles": False,
    }
    release_path = release_dir / "release.json"
    atomic_save_json(release_path, release)
    atomic_save_json(release_root_path / "active.json", {
        "schema_version": "1.0",
        "status": "active",
        "release_id": release_id,
        "release_version": version,
        "release_path": str(release_path),
        "release_hash": sha256(release_path),
        "published_at": datetime.datetime.now().isoformat(),
        "scope": release["scope"],
    })
    print(json.dumps({"status": "published", "release_id": release_id,
                      "release_version": version, "release_path": str(release_dir / "release.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
