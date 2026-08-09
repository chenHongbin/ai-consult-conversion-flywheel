#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Load the workspace-local active capability for runtime consultation analysis."""

import argparse
import io
import json
import sys
from pathlib import Path

from compat import expand_path
from release_utils import component_snapshot, load_release_active, load_release_file


def load_json(path, default):
    if not path.is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def load_runtime(path):
    if not path or not Path(path).is_file():
        return ""
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return handle.read()
    except IOError:
        return ""
def main():
    parser = argparse.ArgumentParser(description="Load the active institution capability package.")
    parser.add_argument("workspace_root")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    workspace = expand_path(args.workspace_root)
    release_active = load_release_active(workspace)
    release = load_release_file(workspace, release_active.get("release_version")) if release_active.get("status") == "active" else None
    release_valid = True
    release_error = None
    if release_active.get("status") == "active":
        if not release or release.get("release_id") != release_active.get("release_id"):
            release_valid = False
            release_error = "unified_release_invalid"
        else:
            for component in ("capability", "knowledge", "patient_insight"):
                try:
                    current = component_snapshot(workspace, component)
                except ValueError as exc:
                    release_valid = False
                    release_error = str(exc)
                    break
                expected = (release.get("components") or {}).get(component, {})
                if current.get("status") != expected.get("status") or current.get("version") != expected.get("version") or current.get("package_hash") != expected.get("package_hash"):
                    release_valid = False
                    release_error = "component_not_bound_to_release:{0}".format(component)
                    break
    if not release_valid:
        sys.stdout.write(json.dumps({"status": "safe_mode", "message": "统一发布版本校验失败，已停止读取机构专属运行时。", "reason": release_error,
                                     "release": release_active}, ensure_ascii=False, indent=2) + "\n")
        return 0
    active_path = workspace / "咨询转化工作区" / "_系统" / "当前能力包" / "active.json"
    active = load_json(active_path, {"status": "base_only", "active_version": None})
    package_path = active.get("package_path")
    knowledge_active_path = workspace / "咨询转化工作区" / "_系统" / "当前机构知识" / "active.json"
    knowledge_active = load_json(knowledge_active_path, {"status": "base_only", "active_version": None})
    knowledge_path = knowledge_active.get("package_path")
    insight_active_path = workspace / "咨询转化工作区" / "_系统" / "患者洞察" / "active.json"
    insight_active = load_json(insight_active_path, {"status": "base_only", "active_version": None})
    insight_path = insight_active.get("package_path")
    embedded_root = Path(__file__).resolve().parent.parent / "institution-pack"
    embedded_insight_root = Path(__file__).resolve().parent.parent / "patient-insight-pack"
    embedded_manifest = embedded_root / "manifest.json"
    # Team release packages carry an approved institution pack. A manager's
    # workspace-local active package takes precedence over this embedded pack.
    if not package_path and embedded_manifest.is_file():
        manifest = load_json(embedded_manifest, {})
        package_path = str(embedded_root / "package.json")
        active = {
            "status": "embedded_active",
            "active_version": manifest.get("capability_version"),
            "package_path": package_path,
            "runtime_context_path": str(embedded_root / "runtime-context.md"),
            "scope": {"institution": manifest.get("institution"), "department": manifest.get("department")},
        }
    if not knowledge_path and embedded_manifest.is_file():
        manifest = load_json(embedded_manifest, {})
        embedded_knowledge = embedded_root / "knowledge.json"
        if embedded_knowledge.is_file():
            knowledge_path = str(embedded_knowledge)
            knowledge_active = {
                "status": "embedded_active",
                "active_version": manifest.get("knowledge_version"),
                "package_path": knowledge_path,
                "runtime_context_path": str(embedded_root / "knowledge-runtime.md"),
                "scope": {"institution": manifest.get("institution"), "department": manifest.get("department")},
            }
    if not insight_path and (embedded_insight_root / "package.json").is_file():
        insight_manifest = load_json(embedded_insight_root / "manifest.json", {})
        insight_path = str(embedded_insight_root / "package.json")
        insight_active = {
            "status": "embedded_active",
            "active_version": insight_manifest.get("insight_version"),
            "package_path": insight_path,
            "runtime_context_path": str(embedded_insight_root / "runtime-context.md"),
            "scope": insight_manifest.get("scope", {}),
        }
    if not package_path and not knowledge_path and not insight_path:
        payload = {"status": "base_only", "message": "当前工作区还没有机构专属能力包，请先执行首次蒸馏。"}
    elif args.format == "markdown":
        runtime_text = load_runtime(active.get("runtime_context_path"))
        knowledge_text = load_runtime(knowledge_active.get("runtime_context_path"))
        insight_text = load_runtime(insight_active.get("runtime_context_path"))
        if runtime_text or knowledge_text or insight_text:
            if runtime_text:
                sys.stdout.write(runtime_text)
            if knowledge_text:
                sys.stdout.write("\n" + knowledge_text)
            if insight_text:
                sys.stdout.write("\n" + insight_text)
            return 0
        payload = {"status": "active", "active": active, "message": "运行时能力文件缺失，请重建能力包。"}
    else:
        package = load_json(expand_path(package_path), {}) if package_path else {}
        knowledge = load_json(expand_path(knowledge_path), {}) if knowledge_path else {}
        insights = load_json(expand_path(insight_path), {}) if insight_path else {}
        payload = {
            "status": active.get("status", "active"),
            "active": active,
            "package": package,
            "knowledge_active": knowledge_active,
            "knowledge": knowledge,
            "patient_insights_active": insight_active,
            "patient_insights": insights,
            "release": release_active if release_active.get("status") == "active" else {"status": "unbound"},
        }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
