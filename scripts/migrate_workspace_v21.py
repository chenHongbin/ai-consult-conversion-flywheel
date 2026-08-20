#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plan or apply the additive, idempotent V2.1 workspace migration."""

import argparse
import io
import json

from compat import ensure_dir
from daily_review import review_root, stable_id
from management_data import SAMPLES_FILE, load_json, load_jsonl, locate_workspace, save_json
from project_version import core_version, WORKSPACE_SCHEMA_VERSION


def migration_plan(root):
    return {
        "create_directories": [
            "_系统/每日复盘/tasks",
            "_系统/每日复盘/leases",
            "_系统/每日复盘/cases",
            "_系统/每日复盘/projections",
            "_系统/每日复盘/grouping",
            "_系统/内容资产/events",
            "_系统/内容资产/candidates",
            "_系统/内容资产/reviews",
            "_系统/审核账本/approvals",
            "07_我的产出/07_内容行动工作台/01_朋友圈内容",
            "07_我的产出/07_内容行动工作台/02_患者跟进文案",
            "07_我的产出/07_内容行动工作台/03_私信承接文案",
            "07_我的产出/07_内容行动工作台/04_素材库",
        ],
        "update_files": ["_系统/工作区清单.json", "_系统/来源配置.json", "_系统/自动化配置.json"],
        "preserve": ["全部原始资料", "现有报告", "现有能力包", "现有机构知识", "V2.0 管理记录"],
        "legacy_samples": len(load_jsonl(root / "_系统" / "管理工作台" / SAMPLES_FILE)),
    }


def apply_migration(root):
    _, store = review_root(root)
    plan = migration_plan(root)
    for relative in plan["create_directories"]:
        ensure_dir(root / relative)
    manifest_path = root / "_系统" / "工作区清单.json"
    manifest = load_json(manifest_path, {}) or {}
    previous_version = manifest.get("layout_version") or "unknown"
    manifest["layout_version"] = WORKSPACE_SCHEMA_VERSION
    manifest["workspace_schema_version"] = WORKSPACE_SCHEMA_VERSION
    manifest["last_migrated_by_core_version"] = core_version()
    manifest["data_ownership"] = "local_user_controlled"
    manifest["upstream_sync"] = "disabled"
    manifest["institution_binding"] = "one_workspace_one_institution"
    if previous_version != WORKSPACE_SCHEMA_VERSION:
        manifest["migrated_from"] = manifest.get("migrated_from") or previous_version
    manifest["daily_review_root"] = "_系统/每日复盘"
    save_json(manifest_path, manifest)

    profile_path = root / "_系统" / "来源配置.json"
    profile = load_json(profile_path, {}) or {}
    profile["version"] = "2.1.3"
    profile["daily_review"] = {
        "enabled": True,
        "contract": "2.1-case-report",
        "mode": "full_standard_plus_priority_deep_review",
        "patient_grouping": "suggest_then_manager_confirm",
        "outcomes": "unknown_until_observed",
        "promise": "behavior_improvement_and_management_efficiency",
        "batch_size": 20,
        "concurrency": 4,
        "lease_minutes": 30,
        "max_attempts": 3,
    }
    automation = profile.setdefault("automation", {})
    automation["schedule"] = automation.get("schedule") or "22:30"
    automation["worker_script"] = "scripts/daily_review.py"
    automation["worker_flow"] = ["claim", "complete_or_fail", "aggregate"]
    save_json(profile_path, profile)

    automation_path = root / "_系统" / "自动化配置.json"
    automation_config = load_json(automation_path, {}) or {}
    automation_config["worker_script"] = "scripts/daily_review.py"
    automation_config["worker_flow"] = ["claim", "complete_or_fail", "aggregate"]
    automation_config["retry_next_run"] = True
    save_json(automation_path, automation_config)

    projections = []
    legacy = load_jsonl(root / "_系统" / "管理工作台" / SAMPLES_FILE)
    for row in legacy:
        if row.get("_invalid"):
            continue
        source_hash = row.get("source_hash") or stable_id("HASH", row.get("sample_id"), row.get("source"))
        employee_id = row.get("employee_id") or "unknown"
        work_date = str(row.get("date") or "unknown")[:10]
        conversation_id = row.get("conversation_id") or stable_id("CONV", employee_id, source_hash)
        projection = {
            "schema_version": "2.1-legacy-sample-projection",
            "legacy_sample_id": row.get("sample_id"),
            "artifact_id": row.get("artifact_id") or stable_id("ART", source_hash),
            "conversation_id": conversation_id,
            "patient_case_id": row.get("patient_case_id") or stable_id("PC", conversation_id),
            "consultant_day_id": row.get("consultant_day_id") or stable_id("CD", employee_id, work_date),
            "team_day_id": row.get("team_day_id") or stable_id("TD", work_date),
            "grouping_state": row.get("grouping_state") or "ungrouped",
            "source_hash": source_hash,
            "employee_id": employee_id,
            "work_date": work_date,
        }
        projections.append(projection)
    projection_path = store / "projections" / "legacy-communication-samples.jsonl"
    ensure_dir(projection_path.parent)
    with io.open(str(projection_path), "w", encoding="utf-8") as handle:
        for row in projections:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"legacy_samples_projected": len(projections), "projection": str(projection_path)}


def main():
    parser = argparse.ArgumentParser(description="Dry-run or apply the AI咨询转化飞轮 V2.1 migration.")
    parser.add_argument("workspace_root")
    parser.add_argument("--apply", action="store_true", help="apply additive changes; default is dry-run")
    args = parser.parse_args()
    try:
        root = locate_workspace(args.workspace_root)
    except ValueError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    plan = migration_plan(root)
    if not args.apply:
        print(json.dumps({"status": "dry_run", "workspace": str(root), "plan": plan}, ensure_ascii=False))
        return 0
    result = apply_migration(root)
    print(json.dumps({"status": "migrated", "workspace": str(root), "plan": plan, "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
