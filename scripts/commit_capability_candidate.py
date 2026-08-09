#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge a structured distillation candidate into an institution capability package.

The base Skill is never modified. This script writes a versioned, workspace-
local package and optionally switches the runtime pointer to the new version.
"""

import argparse
import datetime
import hashlib
import io
import json
import re
import sys
from pathlib import Path

from compat import ensure_dir, expand_path


PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WECHAT = re.compile(r"(微信号|微信|wxid)\s*[:：]?\s*[A-Za-z][A-Za-z0-9_-]{5,}", re.I)

LIST_FIELDS = (
    "facts", "sales_logic", "rules", "objections", "faq_100", "training_200",
    "practice_scenarios", "counterexamples",
)
DELTA_KEYS = {
    "facts": "facts_upsert",
    "sales_logic": "sales_logic_upsert",
    "rules": "rules_upsert",
    "objections": "objections_upsert",
    "faq_100": "faq_100_upsert",
    "training_200": "training_200_upsert",
    "practice_scenarios": "practice_scenarios_upsert",
    "counterexamples": "counterexamples_upsert",
}


def load_json(path, default):
    if not path.is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


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


def contains_sensitive(value):
    text = json.dumps(value, ensure_ascii=False)
    return bool(PHONE.search(text) or ID_CARD.search(text) or EMAIL.search(text) or WECHAT.search(text))


def package_root(workspace):
    return workspace / "咨询转化工作区" / "_系统" / "当前能力包"


def active_path(workspace):
    return package_root(workspace) / "active.json"


def load_active_package(workspace):
    active = load_json(active_path(workspace), {})
    package_path = active.get("package_path")
    if package_path:
        package = load_json(expand_path(package_path), {})
        if package:
            return active, package
    return active, {"schema_version": "1.0", "facts": [], "sales_logic": [], "rules": [],
                    "objections": [], "faq_100": [], "training_200": [],
                    "practice_scenarios": [], "counterexamples": []}


def item_key(item):
    if not isinstance(item, dict):
        return str(item)
    return item.get("id") or item.get("rule_id") or item.get("faq_id") or item.get("name") or item.get("question") or json.dumps(item, ensure_ascii=False, sort_keys=True)


def merge_items(base, updates):
    merged = list(base or [])
    positions = {item_key(item): index for index, item in enumerate(merged)}
    for item in updates or []:
        key = item_key(item)
        if key in positions:
            merged[positions[key]] = item
        else:
            positions[key] = len(merged)
            merged.append(item)
    return merged


def apply_delta(base, candidate):
    result = dict(base or {})
    result.setdefault("schema_version", "1.0")
    result.setdefault("analysis_method", "consultation-eight-step")
    result.setdefault("sample_policy", "include_all_readable_materials_weight_by_nature_and_outcome")
    for field in LIST_FIELDS:
        result.setdefault(field, [])
    delta = candidate.get("delta") or {}
    for field, delta_key in DELTA_KEYS.items():
        result[field] = merge_items(result.get(field), delta.get(delta_key, []))
    for rule_id in delta.get("deprecate_rule_ids", []):
        for item in result.get("rules", []):
            if item_key(item) == rule_id:
                item["status"] = "deprecated"
                item["deprecated_at"] = datetime.datetime.now().isoformat()
    if candidate.get("scope"):
        result["scope"] = candidate["scope"]
    result["last_source_run_id"] = candidate.get("source_run_id")
    result["last_change_summary"] = candidate.get("change_summary", [])
    return result


def next_version(active):
    value = str(active.get("active_version") or "v0.0")
    match = re.match(r"v(\d+)\.(\d+)", value)
    if not match:
        return "v0.1"
    return "v{0}.{1}".format(match.group(1), int(match.group(2)) + 1)


def validate_candidate(candidate):
    errors = []
    if not isinstance(candidate, dict):
        return ["candidate must be a JSON object"]
    if "delta" not in candidate:
        errors.append("missing delta")
    if contains_sensitive(candidate):
        errors.append("candidate contains possible phone, ID, email or WeChat identifier")
    delta = candidate.get("delta") or {}
    for field, key in DELTA_KEYS.items():
        value = delta.get(key, [])
        if not isinstance(value, list):
            errors.append("{0} must be a list".format(key))
        for item in value if isinstance(value, list) else []:
            if isinstance(item, dict) and field in ("sales_logic", "rules", "objections"):
                evidence = item.get("evidence_refs") or item.get("evidence")
                if not evidence:
                    errors.append("{0} item {1} has no evidence_refs".format(field, item_key(item)))
    promotion = candidate.get("promotion") or {}
    if promotion.get("requested") and not promotion.get("evaluation_passed"):
        errors.append("promotion requested without evaluation_passed=true")
    if promotion.get("requested") and not promotion.get("coverage_gate_passed"):
        errors.append("promotion requested without coverage_gate_passed=true")
    return errors


def render_runtime(package, version):
    scope = package.get("scope") or {}
    lines = [
        "# 当前机构咨询能力包 {0}".format(version),
        "",
        "这是一份机构工作空间内的运行时能力，不是公共 Skill 默认知识。",
        "适用范围：机构={0}；科室={1}；病种/项目={2}；渠道={3}".format(
            scope.get("institution", "待确认"), scope.get("department", "待确认"),
            scope.get("disease_or_project", "待确认"), scope.get("channel", "通用")),
        "",
        "## 机构事实",
    ]
    for item in package.get("facts", []):
        if item.get("status", "confirmed") != "deprecated":
            lines.append("- {0}：{1}".format(item.get("name", item.get("id", "事实")), item.get("claim", item.get("value", "待确认"))))
    lines.extend(["", "## 销冠完整销售逻辑"])
    for item in package.get("sales_logic", []):
        if item.get("status", "active") == "deprecated":
            continue
        lines.append("### {0}".format(item.get("stage", item.get("name", "阶段"))))
        for label in ("goal", "signals", "judgment", "actions", "entry_condition", "exit_condition"):
            value = item.get(label)
            if value:
                lines.append("- {0}：{1}".format(label, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)))
    lines.extend(["", "## 当前有效规则"])
    for item in package.get("rules", []):
        if item.get("status", "active") == "deprecated":
            continue
        lines.append("- **{0}**：当 {1}，执行 {2}。不要 {3}".format(
            item.get("name", item_key(item)), item.get("when", "满足适用条件"),
            item.get("do", item.get("action", "按阶段推进")), item.get("avoid", "跳过阶段判断")))
    lines.extend(["", "## 顾虑处理"])
    for item in package.get("objections", []):
        lines.append("- **{0}**：{1}".format(item.get("name", item_key(item)), item.get("response_structure", item.get("response", "待补充"))))
    lines.extend(["", "## 运行约束", "- 先读取机构事实和当前能力包，再分析当前对话。",
                  "- 默认使用咨询转化八步法；已到/未到结果只改变证据权重，不决定是否分析。",
                  "- 内训/策略资料可提炼方法参考，但不能作为患者结果证据。",
                  "- 未确认的医生、价格、地址、疗效、周期和流程不能补写。",
                  "- 当前能力包提供机构经验，不替代医生判断，也不把单个成功案例当成普遍规则。"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Commit and optionally publish an institution capability candidate.")
    parser.add_argument("workspace_root")
    parser.add_argument("candidate", help="structured candidate JSON produced by the distillation Agent")
    parser.add_argument("--publish", action="store_true", help="switch active.json to the new version")
    parser.add_argument("--version", help="explicit version such as v0.1; defaults to the next version")
    args = parser.parse_args()

    workspace = expand_path(args.workspace_root)
    candidate_path = expand_path(args.candidate)
    candidate = load_json(candidate_path, None)
    errors = validate_candidate(candidate)
    if args.publish:
        promotion = (candidate or {}).get("promotion") or {}
        if not promotion.get("evaluation_passed") or not promotion.get("coverage_gate_passed"):
            errors.append("--publish requires evaluation_passed=true and coverage_gate_passed=true")
    if errors:
        print(json.dumps({"status": "rejected", "errors": errors}, ensure_ascii=False))
        return 2
    root = package_root(workspace)
    active, base = load_active_package(workspace)
    version = args.version or next_version(active)
    merged = apply_delta(base, candidate)
    merged.update({"version": version, "created_at": datetime.datetime.now().isoformat(),
                   "candidate_hash": sha256(candidate_path)})
    version_dir = root / "versions" / version
    ensure_dir(version_dir)
    package_path = version_dir / "package.json"
    runtime_path = version_dir / "runtime-context.md"
    save_json(package_path, merged)
    with io.open(str(runtime_path), "w", encoding="utf-8") as handle:
        handle.write(render_runtime(merged, version))
    candidate_archive = root.parent / "蒸馏候选" / version
    ensure_dir(candidate_archive)
    save_json(candidate_archive / "candidate.json", candidate)
    save_json(candidate_archive / "merged-preview.json", merged)

    published = False
    if args.publish:
        active_payload = {
            "schema_version": "1.0",
            "status": "active",
            "active_version": version,
            "package_path": str(package_path),
            "runtime_context_path": str(runtime_path),
            "published_at": datetime.datetime.now().isoformat(),
            "source_run_id": candidate.get("source_run_id"),
            "scope": merged.get("scope", {}),
        }
        save_json(active_path(workspace), active_payload)
        published = True
    else:
        active_payload = active

    visible_dir = workspace / "咨询转化工作区" / "07_我的产出" / "03_销冠蒸馏能力包"
    ensure_dir(visible_dir)
    visible_name = version + ("_机构专属咨询能力包.md" if published else "_候选机构专属咨询能力包.md")
    with io.open(str(visible_dir / visible_name), "w", encoding="utf-8") as handle:
        handle.write(render_runtime(merged, version))
    print(json.dumps({"status": "published" if published else "candidate_saved",
                      "version": version, "active_version": active_payload.get("active_version"),
                      "package_path": str(package_path), "runtime_context_path": str(runtime_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
