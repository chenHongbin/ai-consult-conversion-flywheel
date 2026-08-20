#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check Core integrity, workspace compatibility and update-safe separation."""

import argparse
import io
import json
import os
import sys
from pathlib import Path

from project_version import core_version, WORKSPACE_SCHEMA_VERSION, SUPPORTED_WORKSPACE_SCHEMAS
from workspace_paths import locate_workspace


ROOT = Path(__file__).resolve().parents[1]


def load_json(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def inside(path, parent):
    path = Path(os.path.realpath(str(path)))
    parent = Path(os.path.realpath(str(parent)))
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser(description="Check the AI consultation flywheel Core and local workspace.")
    parser.add_argument("workspace_root", nargs="?", default="")
    args = parser.parse_args()

    checks = {
        "skill_entry": (ROOT / "SKILL.md").is_file(),
        "base_runtime": (ROOT / "runtime" / "base-runtime.json").is_file(),
        "version_source": (ROOT / "VERSION").is_file(),
    }
    result = {
        "status": "ready" if all(checks.values()) else "blocked",
        "core_version": core_version(),
        "workspace_schema_current": WORKSPACE_SCHEMA_VERSION,
        "checks": checks,
        "telemetry": "disabled_by_default",
    }
    if not args.workspace_root:
        result["workspace"] = {"status": "not_checked", "message": "未提供工作区；公共 Core 可以直接安装。"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ready" else 2

    try:
        workspace = locate_workspace(args.workspace_root)
    except (IOError, ValueError) as exc:
        result["status"] = "blocked"
        result["workspace"] = {"status": "not_found", "message": str(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    if not (workspace / "_系统").is_dir():
        result["status"] = "blocked"
        result["workspace"] = {
            "status": "not_found",
            "path": str(workspace),
            "message": "还没有标准工作区；请先说‘开始设置’。",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    manifest_path = workspace / "_系统" / "工作区清单.json"
    manifest = load_json(manifest_path) if manifest_path.is_file() else {}
    schema = manifest.get("layout_version")
    separate = not inside(workspace, ROOT)
    workspace_status = "compatible"
    actions = []
    if not separate:
        workspace_status = "unsafe_location"
        actions.append("把工作区迁移到 Skill 安装目录之外；Core 更新不得覆盖用户数据。")
    if schema not in SUPPORTED_WORKSPACE_SCHEMAS:
        workspace_status = "migration_required"
        actions.append("先备份并运行受支持的工作区迁移工具。")
    if not manifest.get("workspace_id"):
        workspace_status = "migration_required"
        actions.append("工作区缺少稳定 workspace_id；运行 V2.1.3 迁移。")
    result["workspace"] = {
        "status": workspace_status,
        "path": str(workspace),
        "workspace_id": manifest.get("workspace_id"),
        "institution": manifest.get("institution") or "由初始化档案确定",
        "schema_version": schema,
        "outside_core_directory": separate,
        "one_workspace_one_institution": True,
        "actions": actions,
    }
    if workspace_status != "compatible":
        result["status"] = "blocked"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    sys.exit(main())
