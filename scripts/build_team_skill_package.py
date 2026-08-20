#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optionally export an offline frontline bundle with an approved local runtime.

The normal product path is one public Core plus a private local workspace. This
compatibility command is only for frontline devices that cannot load the same
workspace. Raw recordings, patient chats and manager-only files are never copied.
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from compat import ensure_dir, expand_path
from release_utils import component_snapshot, load_release_active, load_release_file, sha256 as release_sha256, validate_version
from workspace_paths import assert_within, locate_workspace
from privacy_guard import scan_value
from project_version import core_version, core_version_tag


PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WECHAT = re.compile(r"(微信号|微信|wxid)\s*[:：]?\s*[A-Za-z][A-Za-z0-9_-]{5,}", re.I)
SKIP_DIRS = {".git", "output", "__pycache__", ".venv", "node_modules", "咨询转化工作区"}
SKIP_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".amr", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".html", ".htm", ".xlsx", ".xls", ".csv", ".jsonl"}

# Team members need deterministic material processing and runtime loading, but
# must not receive the manager's distillation, publish, rollback, or team
# reporting toolchain. Keep this allowlist explicit so a new manager script is
# not silently shipped to frontline users.
FRONTLINE_SCRIPTS = {
    "compat.py",
    "detect_environment.py",
    "init_consult_workspace.py",
    "inventory_workspace.py",
    "batch_transcribe_younavi.py",
    "slice_long_images.py",
    "ocr_long_images.py",
    "extract_text_sources.py",
    "prepare_distillation_batch.py",
    "load_active_capability.py",
    "release_utils.py",
    "ima_sync.py",
    "personal_growth.py",
    "route_consultation.py",
    "select_visual_asset.py",
    "record_visual_feedback.py",
    "content_feedback.py",
    "map_content_knowledge.py",
    "verify_consult_workspace.py",
    "privacy_guard.py",
    "project_version.py",
    "product_feedback.py",
    "medical_safety.py",
    "workspace_paths.py",
}

FRONTLINE_REFERENCES = {
    "analysis-and-coaching.md",
    "base-runtime.md",
    "consultant-front-door.md",
    "consultation-base.md",
    "consultation-eight-step-method.md",
    "consultation-visual-content-loop.md",
    "content-action-runtime.md",
    "content-knowledge-mapping.json",
    "distribution-and-feedback.md",
    "knowledge-model.md",
    "naming.md",
    "open-source-runtime.md",
    "patient-decision-insights.md",
    "perspective-lenses.md",
    "practice-coach.md",
    "safety-and-sanitization.md",
    "source-ingestion.md",
    "specialist-routing.json",
    "visual-asset-catalog.json",
    "visual-decision-matrix.json",
    "visual-creative.md",
    "workspace-initialization-contract.md",
    "workspace-onboarding.md",
    "frontline-runtime.md",
    "v2.1-case-report-contract.md",
}

FRONTLINE_RUNTIME_FILES = {"base-runtime.json"}


def load_json(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path, value):
    ensure_dir(path.parent)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sensitive(text):
    return bool(scan_value(text))


def safe_name(value):
    value = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", str(value or "").strip())
    return value.strip("_") or "机构"


def approved_knowledge(package):
    package = dict(package or {})
    package["facts"] = [
        item for item in package.get("facts", [])
        if item.get("status") in ("active", "approved", "confirmed")
    ]
    package["pending_facts_excluded"] = True
    return package


def approved_patient_insights(package):
    package = dict(package or {})
    for field in ("decision_states", "doubt_intents", "practice_scenarios"):
        package[field] = [
            item for item in package.get(field, [])
            if item.get("review_status") in ("active", "approved", "confirmed")
        ]
    package["contains_patient_level_profiles"] = False
    package["contains_raw_patient_material"] = False
    return package


def copy_base(source_root, staging):
    for path in source_root.rglob("*"):
        relative = path.relative_to(source_root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.is_dir() or path.is_symlink():
            continue
        if not should_copy_frontline(relative):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES and relative.parts[:2] != ("references", "test-set"):
            continue
        if path.name.endswith(".skill"):
            continue
        target = staging / relative
        ensure_dir(target.parent)
        shutil.copy2(str(path), str(target))


def should_copy_frontline(relative):
    """Return whether a base file belongs in the frontline runtime package."""
    parts = relative.parts
    if not parts:
        return False
    if parts[0] in ("agents", "skills"):
        return True
    if relative.as_posix() in ("VERSION", "SKILL.md", "LICENSE"):
        return True
    if parts[0] == "scripts":
        return len(parts) == 2 and parts[1] in FRONTLINE_SCRIPTS
    if parts[0] == "references":
        return len(parts) == 2 and parts[1] in FRONTLINE_REFERENCES
    if parts[0] == "runtime":
        return len(parts) == 2 and parts[1] in FRONTLINE_RUNTIME_FILES
    return False


def write_frontline_agent_metadata(staging):
    """Give the team package a frontline-first default prompt."""
    path = Path(staging) / "agents" / "openai.yaml"
    ensure_dir(path.parent)
    text = '''interface:
  display_name: "AI咨询转化飞轮"
  short_description: "分析咨询、生成下一步内容、学习销冠并持续提升个人转化能力"
  default_prompt: "使用 AI咨询转化飞轮分析我的电话、微信或私信，并按患者当前阶段生成跟进、朋友圈、科普或私信内容；需要时先做 YouNavi 转录、长图切片 OCR 和去重。优先使用团队已发布能力，并把我的使用反馈沉淀到个人层。不要执行团队蒸馏、内容资产批准、候选发布、版本回滚或读取其他员工资料。"

policy:
  allow_implicit_invocation: true
'''
    with io.open(str(path), "w", encoding="utf-8") as handle:
        handle.write(text)


def write_frontline_skill(staging):
    """Replace the public manager router with a self-contained frontline entry."""
    path = Path(staging) / "SKILL.md"
    text = '''---
name: medical-consult-conversion-coach
description: 医疗咨询一线离线运行入口。分析本人获准处理的电话、微信、私信或截图，生成安全回复、回访、内容和单动作陪练；加载包内已审核机构运行时，不处理主管发布、团队数据或其他员工资料。
---

# AI咨询转化飞轮（一线离线运行包）

这是公共 Core 的可选离线运行快照，只供无法共享机构工作区的一线设备使用。公共 Core 与机构运行时版本相互独立，边界见 [open-source-runtime.md](references/open-source-runtime.md)。

只处理当前用户获准访问的个人材料：分析这一条、帮我回复、安排回访、生成下一步内容、陪我练一遍。分析读取 [consultation-eight-step-method.md](references/consultation-eight-step-method.md)、[analysis-and-coaching.md](references/analysis-and-coaching.md) 和 [safety-and-sanitization.md](references/safety-and-sanitization.md)；内容读取 [consultant-front-door.md](references/consultant-front-door.md) 与 [content-action-runtime.md](references/content-action-runtime.md)；陪练读取 [practice-coach.md](references/practice-coach.md)。

优先加载包内已审核机构能力，再叠加当前用户本地个人成长层。不得执行团队蒸馏、候选写回、内容审核、机构发布、回滚、主管报表或读取其他员工资料。没有确认的医生、价格、地址、项目、疗效、周期或活动时明确标记待确认；不自动发送或发布内容，不做临床诊断和疗效保证。
'''
    with io.open(str(path), "w", encoding="utf-8") as handle:
        handle.write(text)


def main():
    parser = argparse.ArgumentParser(description="Export an optional offline frontline runtime bundle.")
    parser.add_argument("workspace_root")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--institution", default="")
    parser.add_argument("--department", default="")
    parser.add_argument("--channel", default="通用")
    parser.add_argument("--version", help="defaults to the active capability version")
    args = parser.parse_args()

    workspace = locate_workspace(args.workspace_root)
    source_root = Path(__file__).resolve().parents[1]
    release_active = load_release_active(workspace)
    if release_active.get("status") != "active" or not release_active.get("release_version"):
        print(json.dumps({"status": "rejected", "reason": "unified_release_missing"}, ensure_ascii=False))
        return 2
    release = load_release_file(workspace, release_active.get("release_version"))
    if (not release or release.get("release_id") != release_active.get("release_id")
            or release_sha256(release_active.get("release_path")) != release_active.get("release_hash")):
        print(json.dumps({"status": "rejected", "reason": "unified_release_invalid"}, ensure_ascii=False))
        return 2
    try:
        for component in ("capability", "knowledge", "patient_insight"):
            current_snapshot = component_snapshot(workspace, component)
            release_snapshot = (release.get("components") or {}).get(component, {})
            if (current_snapshot.get("version") != release_snapshot.get("version")
                    or current_snapshot.get("status") != release_snapshot.get("status")
                    or current_snapshot.get("package_hash") != release_snapshot.get("package_hash")
                    or current_snapshot.get("runtime_hash") != release_snapshot.get("runtime_hash")):
                print(json.dumps({"status": "rejected", "reason": "component_not_bound_to_release", "component": component}, ensure_ascii=False))
                return 2
    except ValueError as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False))
        return 2
    content_snapshot = (release.get("components") or {}).get("content_runtime", {})
    try:
        content_path = assert_within(content_snapshot.get("package_path"), workspace, "content_runtime_package")
        content_runtime_path = assert_within(content_snapshot.get("runtime_context_path"), workspace, "content_runtime_context")
    except ValueError as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False))
        return 2
    if (release_sha256(content_path) != content_snapshot.get("package_hash")
            or release_sha256(content_runtime_path) != content_snapshot.get("runtime_hash")):
        print(json.dumps({"status": "rejected", "reason": "component_not_bound_to_release", "component": "content_runtime"}, ensure_ascii=False))
        return 2
    content_runtime = load_json(content_path)
    with io.open(str(content_runtime_path), "r", encoding="utf-8") as handle:
        content_runtime_text = handle.read()
    package_root = workspace / "_系统" / "当前能力包"
    active_path = package_root / "active.json"
    if not active_path.is_file():
        print(json.dumps({"status": "rejected", "reason": "active_capability_missing"}, ensure_ascii=False))
        return 2
    active = load_json(active_path)
    if active.get("status") != "active" or not active.get("package_path"):
        print(json.dumps({"status": "rejected", "reason": "capability_not_published", "active": active}, ensure_ascii=False))
        return 2
    capability_path = expand_path(active["package_path"])
    runtime_path = expand_path(active.get("runtime_context_path", ""))
    if not capability_path.is_file() or not runtime_path.is_file():
        print(json.dumps({"status": "rejected", "reason": "capability_artifacts_missing"}, ensure_ascii=False))
        return 2
    capability = load_json(capability_path)
    with io.open(str(runtime_path), "r", encoding="utf-8") as handle:
        runtime_text = handle.read()
    serialized = json.dumps(capability, ensure_ascii=False) + "\n" + runtime_text
    if sensitive(serialized):
        print(json.dumps({"status": "rejected", "reason": "capability_contains_possible_personal_identifier"}, ensure_ascii=False))
        return 2

    knowledge_active_path = workspace / "_系统" / "当前机构知识" / "active.json"
    knowledge_active = load_json(knowledge_active_path) if knowledge_active_path.is_file() else {}
    knowledge_path = expand_path(knowledge_active.get("package_path", "")) if knowledge_active.get("package_path") else None
    knowledge_runtime_path = expand_path(knowledge_active.get("runtime_context_path", "")) if knowledge_active.get("runtime_context_path") else None
    knowledge = approved_knowledge(load_json(knowledge_path) if knowledge_path and knowledge_path.is_file() else {})
    knowledge_runtime_text = ""
    if knowledge_runtime_path and knowledge_runtime_path.is_file():
        with io.open(str(knowledge_runtime_path), "r", encoding="utf-8") as handle:
            knowledge_runtime_text = handle.read()
    if not knowledge_runtime_text:
        knowledge_runtime_text = "# 当前机构知识\n\n当前发布包没有已确认的机构知识。\n"
    knowledge_serialized = json.dumps(knowledge, ensure_ascii=False) + "\n" + knowledge_runtime_text
    if sensitive(knowledge_serialized):
        print(json.dumps({"status": "rejected", "reason": "knowledge_contains_possible_personal_identifier"}, ensure_ascii=False))
        return 2

    insight_active_path = workspace / "_系统" / "患者洞察" / "active.json"
    insight_active = load_json(insight_active_path) if insight_active_path.is_file() else {}
    insight_path = expand_path(insight_active.get("package_path", "")) if insight_active.get("package_path") else None
    insight_runtime_path = expand_path(insight_active.get("runtime_context_path", "")) if insight_active.get("runtime_context_path") else None
    insight = approved_patient_insights(load_json(insight_path) if insight_path and insight_path.is_file() else {})
    insight_runtime_text = ""
    if insight_runtime_path and insight_runtime_path.is_file():
        with io.open(str(insight_runtime_path), "r", encoding="utf-8") as handle:
            insight_runtime_text = handle.read()
    insight_serialized = json.dumps(insight, ensure_ascii=False) + "\n" + insight_runtime_text
    if sensitive(insight_serialized):
        print(json.dumps({"status": "rejected", "reason": "patient_insight_contains_possible_personal_identifier"}, ensure_ascii=False))
        return 2

    try:
        version = validate_version(args.version or active.get("active_version"))
    except ValueError as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False))
        return 2
    institution = args.institution or capability.get("scope", {}).get("institution", "当前机构")
    department = args.department or capability.get("scope", {}).get("department", "当前科室")
    release_scope = release.get("scope") or {}
    if ((release_scope.get("institution") and institution != release_scope.get("institution"))
            or (release_scope.get("department") and department != release_scope.get("department"))):
        print(json.dumps({"status": "rejected", "reason": "package_scope_must_match_release"}, ensure_ascii=False))
        return 2
    filename = "AI咨询转化飞轮_离线团队运行包_{0}_{1}_{2}.skill".format(
        safe_name(institution), safe_name(department), safe_name(version))
    output_dir = expand_path(args.output_dir)
    ensure_dir(output_dir)
    output = output_dir / filename
    staging = Path(tempfile.mkdtemp(prefix="ai-flywheel-team-package-"))
    try:
        copy_base(source_root, staging)
        write_frontline_skill(staging)
        write_frontline_agent_metadata(staging)
        pack_dir = staging / "institution-pack"
        ensure_dir(pack_dir)
        save_json(pack_dir / "package.json", capability)
        with io.open(str(pack_dir / "runtime-context.md"), "w", encoding="utf-8") as handle:
            handle.write(runtime_text)
        save_json(pack_dir / "knowledge.json", knowledge)
        with io.open(str(pack_dir / "knowledge-runtime.md"), "w", encoding="utf-8") as handle:
            handle.write(knowledge_runtime_text)
        save_json(pack_dir / "content-runtime.json", content_runtime)
        with io.open(str(pack_dir / "content-runtime.md"), "w", encoding="utf-8") as handle:
            handle.write(content_runtime_text)
        manifest = {
            "package_type": "offline_team_runtime_bundle",
            "base_skill_name": "AI咨询转化飞轮",
            "base_skill_version": core_version_tag(),
            "base_core_version": core_version(),
            "distribution_model": "optional_offline_export",
            "runtime_role": "frontline",
            "frontline_capabilities": [
                "scan_personal_materials", "transcribe_audio", "ocr_long_images",
                "analyse_personal_consultations", "learn_from_team_examples",
                "personal_growth_overlay", "practice_coaching", "content_action", "feedback_capture",
            ],
            "manager_capabilities_excluded": [
                "team_distillation", "candidate_commit", "release_publish",
                "release_rollback", "team_reporting", "manager_workspace_access",
            ],
            "release_id": release.get("release_id"),
            "capability_version": version,
            "institution": institution,
            "department": department,
            "channel": args.channel,
            "published_at": datetime.datetime.now().isoformat(),
            "capability_hash": sha256(capability_path),
            "runtime_hash": sha256(runtime_path),
            "knowledge_version": knowledge.get("version"),
            "knowledge_hash": sha256(pack_dir / "knowledge.json"),
            "knowledge_runtime_hash": sha256(pack_dir / "knowledge-runtime.md"),
            "patient_insight_version": insight.get("version"),
            "patient_insight_hash": "",
            "patient_insight_runtime_hash": "",
            "content_runtime_version": content_runtime.get("version"),
            "content_runtime_hash": sha256(pack_dir / "content-runtime.json"),
            "content_runtime_context_hash": sha256(pack_dir / "content-runtime.md"),
            "approved_content_asset_count": content_runtime.get("asset_count", 0),
            "contains_patient_level_profiles": False,
            "contains_unreviewed_knowledge": False,
            "contains_raw_patient_material": False,
            "contains_manager_workspace": False,
            "update_rule": "公共 Core 独立升级；仅在无法共享机构工作区时重新导出本地运行时快照",
        }
        insight_dir = staging / "patient-insight-pack"
        ensure_dir(insight_dir)
        save_json(insight_dir / "package.json", insight)
        with io.open(str(insight_dir / "runtime-context.md"), "w", encoding="utf-8") as handle:
            handle.write(insight_runtime_text or "# 患者决策洞察\n\n当前团队包暂无已审核的患者决策洞察。\n")
        insight_manifest = {
            "package_type": "patient_decision_insight_runtime",
            "insight_version": insight.get("version"),
            "scope": insight.get("scope", {}),
            "contains_patient_level_profiles": False,
            "contains_raw_patient_material": False,
            "approved_state_count": len(insight.get("decision_states", [])),
            "approved_intent_count": len(insight.get("doubt_intents", [])),
            "approved_scenario_count": len(insight.get("practice_scenarios", [])),
        }
        save_json(insight_dir / "manifest.json", insight_manifest)
        manifest["patient_insight_hash"] = sha256(insight_dir / "package.json")
        manifest["patient_insight_runtime_hash"] = sha256(insight_dir / "runtime-context.md")
        manifest["package_fingerprint"] = "package-" + hashlib.sha256((
            manifest["capability_hash"] + manifest["knowledge_hash"] + manifest["patient_insight_hash"] + manifest["content_runtime_hash"]
        ).encode("utf-8")).hexdigest()[:16]
        save_json(pack_dir / "manifest.json", manifest)
        save_json(pack_dir / "release.json", release)
        with io.open(str(pack_dir / "团队更新说明.md"), "w", encoding="utf-8") as handle:
            handle.write("# 离线团队运行包说明\n\n")
            handle.write("这是 {0} / {1} 的本地咨询运行时快照 {2}。\n\n".format(institution, department, version))
            handle.write("普通情况下请安装官方公共 Core 并读取机构工作区；只有设备无法共享工作区时才使用本导出包。\n")
            handle.write("公共 Core 升级与本机构运行时升级相互独立。\n")
            handle.write("本包不包含原始患者录音、微信聊天、姓名、电话、病历或主管私有工作区。\n")
            handle.write("患者洞察仅包含经过审核的群体决策状态和合成陪练索引，不包含患者个人画像。\n")
        if output.exists():
            output.unlink()
        with zipfile.ZipFile(str(output), "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(str(path), str(path.relative_to(staging)))
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)

    release_dir = workspace / "_系统" / "团队发布包" / str(version)
    ensure_dir(release_dir)
    shutil.copy2(str(output), str(release_dir / filename))
    save_json(release_dir / "release-manifest.json", manifest)
    print(json.dumps({"status": "built", "package": str(output), "version": version,
                      "release_manifest": str(release_dir / "release-manifest.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
