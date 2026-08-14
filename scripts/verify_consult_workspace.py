#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify that a local workspace follows the canonical v1.9/v2.0 layout."""

import argparse
import io
import json
import sys
from pathlib import Path

from compat import expand_path
from init_consult_workspace import SYSTEM_FOLDERS, VISIBLE_FOLDERS


MANIFEST_NAME = "工作区清单.json"
WORKSPACE_NAME = "咨询转化工作区"


def load_json(path, default=None):
    if not Path(path).is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def locate_workspace(selected):
    selected = expand_path(selected)
    if (selected / "_系统" / MANIFEST_NAME).is_file():
        return selected, "selected_folder"
    child = selected / WORKSPACE_NAME
    if (child / "_系统" / MANIFEST_NAME).is_file():
        return child, "child_workspace"
    # A partially created target is still the canonical target to verify.
    if child.is_dir():
        return child, "child_workspace_partial"
    return child, "missing_workspace"


def verify(selected):
    root, location = locate_workspace(selected)
    missing_visible = [name for name, _ in VISIBLE_FOLDERS if not (root / name).is_dir()]
    manifest_path = root / "_系统" / MANIFEST_NAME
    manifest = load_json(manifest_path, {}) or {}
    manifest_version = manifest.get("layout_version")
    missing_system = [name for name in SYSTEM_FOLDERS if not (root / "_系统" / name).is_dir()]
    manifest_ok = (
        manifest.get("product") == "AI咨询转化飞轮"
        and manifest_version in ("v1.9", "v2.0")
        and manifest.get("workspace_root") == str(root)
        and manifest.get("visible_folders") == [name for name, _ in VISIBLE_FOLDERS]
    )
    if not missing_visible and not missing_system and manifest_ok:
        status = "canonical"
    elif location == "missing_workspace":
        status = "missing"
    else:
        status = "needs_repair"
    return {
        "status": status,
        "workspace": str(root),
        "location": location,
        "manifest": str(manifest_path),
        "missing_visible_folders": missing_visible,
        "missing_system_folders": missing_system,
        "manifest_ok": manifest_ok,
        "layout_version": manifest_version,
        "expected_container": WORKSPACE_NAME,
        "message": {
            "canonical": "工作区符合 AI咨询转化飞轮标准目录，可由v2.0按需补充管理工作台数据。",
            "missing": "还没有创建标准咨询转化工作区。",
            "needs_repair": "工作区存在但目录或清单不完整，请运行初始化脚本补齐；不要手工改名。",
        }[status],
    }


def main():
    parser = argparse.ArgumentParser(description="Verify the canonical AI咨询转化飞轮 workspace layout.")
    parser.add_argument("workspace_root", help="the user-selected source workspace root")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    result = verify(args.workspace_root)
    if args.format == "markdown":
        sys.stdout.write("# 工作区结构检查\n\n")
        sys.stdout.write("状态：{0}\n\n位置：{1}\n\n{2}\n".format(
            result["status"], result["workspace"], result["message"]))
        if result["missing_visible_folders"] or result["missing_system_folders"]:
            sys.stdout.write("\n缺少目录：\n")
            for item in result["missing_visible_folders"] + result["missing_system_folders"]:
                sys.stdout.write("- {0}\n".format(item))
    else:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result["status"] == "canonical" else 1


if __name__ == "__main__":
    raise SystemExit(main())
