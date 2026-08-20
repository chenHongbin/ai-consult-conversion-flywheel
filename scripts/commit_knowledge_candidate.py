#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commit a structured institution-knowledge candidate.

Knowledge extracted from conversations is never treated as fact automatically.
The script keeps pending and conflicting items in a versioned workspace-local
knowledge package and only renders manager-confirmed items into runtime context.
"""

import argparse
import datetime
import hashlib
import io
import json
import re
from pathlib import Path

from compat import ensure_dir, expand_path
from workspace_paths import locate_workspace
from approval_ledger import validate_approval
from release_utils import validate_version
from privacy_guard import scan_value


PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WECHAT = re.compile(r"(微信号|微信|wxid)\s*[:：]?\s*[A-Za-z][A-Za-z0-9_-]{5,}", re.I)

HIGH_RISK_TERMS = (
    "价格", "费用", "收费", "医生", "主任", "职称", "地址", "电话", "出诊",
    "活动", "奖项", "资质", "认证", "治愈", "疗效", "成功率", "周期", "保证",
    "案例结果", "前后对比", "患者", "病历", "联系方式",
)


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
    return bool(scan_value(value))


def knowledge_root(workspace):
    return locate_workspace(workspace) / "_系统" / "当前机构知识"


def active_path(workspace):
    return knowledge_root(workspace) / "active.json"


def load_active_knowledge(workspace):
    active = load_json(active_path(workspace), {})
    package_path = active.get("package_path")
    if package_path:
        package = load_json(expand_path(package_path), {})
        if package:
            return active, package
    return active, {"schema_version": "1.0-knowledge", "facts": [], "conflicts": []}


def fact_key(item):
    if not isinstance(item, dict):
        return str(item)
    if item.get("id"):
        return item["id"]
    scope = item.get("scope") or {}
    domain = item.get("domain", "general")
    name = item.get("name") or item.get("title") or item.get("key") or "fact"
    return "{0}:{1}:{2}:{3}".format(
        scope.get("department", ""), scope.get("disease_or_project", ""), domain, name
    ).lower()


def fact_claim(item):
    return item.get("claim", item.get("value", item.get("description", "")))


def is_high_risk(item):
    text = json.dumps(item, ensure_ascii=False).lower()
    return any(term.lower() in text for term in HIGH_RISK_TERMS)


def is_manager_confirmed(item):
    return item.get("manager_confirmed") is True or item.get("review_status") in (
        "manager_confirmed", "approved",
    )


def render_runtime(package, version):
    scope = package.get("scope") or {}
    lines = [
        "# 当前机构知识包 {0}".format(version),
        "",
        "这是一份工作空间内的机构知识，不是公共 Skill 默认知识。",
        "适用范围：机构={0}；科室={1}；病种/项目={2}；渠道={3}".format(
            scope.get("institution", "待确认"), scope.get("department", "待确认"),
            scope.get("disease_or_project", "待确认"), scope.get("channel", "通用")),
        "",
        "## 已确认机构知识",
    ]
    grouped = {}
    for item in package.get("facts", []):
        if item.get("status") not in ("active", "approved", "confirmed"):
            continue
        grouped.setdefault(item.get("domain", "机构信息"), []).append(item)
    for domain in sorted(grouped):
        lines.append("### {0}".format(domain))
        for item in grouped[domain]:
            lines.append("- {0}：{1}".format(item.get("name", item.get("id", "知识")), fact_claim(item)))
    pending = sum(1 for item in package.get("facts", []) if item.get("status") in ("pending_review", "conflict"))
    lines.extend([
        "",
        "## 运行约束",
        "- 只引用已确认机构知识；待确认或冲突信息不能直接对患者承诺。",
        "- 价格、医生、地址、活动、资质、疗效、周期和案例结果必须核对生效时间。",
        "- 录音中的机构说法只能作为证据候选，不能自动等同于机构事实。",
        "- 当前仍有 {0} 条机构知识待确认或冲突。".format(pending),
    ])
    return "\n".join(lines) + "\n"


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
    delta = candidate.get("delta") or {}
    facts = delta.get("facts_upsert", [])
    if not isinstance(facts, list):
        errors.append("facts_upsert must be a list")
        facts = []
    if contains_sensitive(candidate):
        errors.append("candidate contains possible phone, ID, email or WeChat identifier")
    for item in facts:
        if not isinstance(item, dict):
            errors.append("each fact must be an object")
            continue
        if not item.get("name") and not item.get("id"):
            errors.append("fact missing name or id")
        if not fact_claim(item):
            errors.append("fact {0} missing claim/value/description".format(fact_key(item)))
        if not (item.get("evidence_refs") or item.get("source_refs")):
            errors.append("fact {0} has no evidence_refs or source_refs".format(fact_key(item)))
    return errors


def merge_facts(base, updates):
    merged = list(base or [])
    positions = {fact_key(item): index for index, item in enumerate(merged)}
    conflicts = []
    for original in updates or []:
        item = dict(original)
        key = fact_key(item)
        item["id"] = item.get("id") or key
        item["status"] = "approved" if is_manager_confirmed(item) else "pending_review"
        if is_high_risk(item) and not is_manager_confirmed(item):
            item["status"] = "pending_review"
        if key in positions:
            previous = merged[positions[key]]
            if fact_claim(previous) and fact_claim(previous) != fact_claim(item):
                item["status"] = "conflict"
                conflict_id = "{0}#conflict-{1}".format(
                    key, hashlib.sha1(fact_claim(item).encode("utf-8")).hexdigest()[:8]
                )
                item["id"] = conflict_id
                item["conflicts_with"] = [previous.get("id", key)]
                merged.append(item)
                conflicts.append({
                    "fact_id": key,
                    "existing_claim": fact_claim(previous),
                    "new_claim": fact_claim(item),
                    "status": "manager_review_required",
                })
            else:
                merged[positions[key]] = item
        else:
            positions[key] = len(merged)
            merged.append(item)
    return merged, conflicts


def main():
    parser = argparse.ArgumentParser(description="Commit an institution knowledge candidate.")
    parser.add_argument("workspace_root")
    parser.add_argument("candidate", help="knowledge candidate JSON")
    parser.add_argument("--publish", action="store_true", help="publish the version pointer")
    parser.add_argument("--approval-id", help="independent manager approval receipt bound to this candidate")
    parser.add_argument("--version", help="explicit version such as v0.1")
    args = parser.parse_args()

    workspace = expand_path(args.workspace_root)
    candidate_path = expand_path(args.candidate)
    candidate = load_json(candidate_path, None)
    errors = validate_candidate(candidate)
    approval = None
    if args.publish:
        try:
            approval = validate_approval(workspace, "knowledge", candidate_path, args.approval_id)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        print(json.dumps({"status": "rejected", "errors": errors}, ensure_ascii=False))
        return 2

    root = knowledge_root(workspace)
    ensure_dir(root / "versions")
    active, base = load_active_knowledge(workspace)
    try:
        version = validate_version(args.version or next_version(active))
    except ValueError as exc:
        print(json.dumps({"status": "rejected", "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    facts, conflicts = merge_facts(base.get("facts", []), (candidate.get("delta") or {}).get("facts_upsert", []))
    for fact_id in (candidate.get("delta") or {}).get("deprecate_fact_ids", []):
        for item in facts:
            if item.get("id") == fact_id:
                item["status"] = "deprecated"
                item["deprecated_at"] = datetime.datetime.now().isoformat()
    merged = {
        "schema_version": "1.0-knowledge",
        "version": version,
        "created_at": datetime.datetime.now().isoformat(),
        "candidate_hash": sha256(candidate_path),
        "scope": candidate.get("scope") or base.get("scope", {}),
        "facts": facts,
        "conflicts": conflicts,
        "last_source_run_id": candidate.get("source_run_id"),
        "last_change_summary": candidate.get("change_summary", []),
        "approval_id": approval.get("approval_id") if approval else None,
    }
    version_dir = root / "versions" / version
    ensure_dir(version_dir)
    package_path = version_dir / "knowledge.json"
    runtime_path = version_dir / "knowledge-runtime.md"
    save_json(package_path, merged)
    with io.open(str(runtime_path), "w", encoding="utf-8") as handle:
        handle.write(render_runtime(merged, version))
    archive_dir = root.parent / "机构知识候选" / version
    save_json(archive_dir / "knowledge-candidate.json", candidate)
    save_json(archive_dir / "merged-preview.json", merged)

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
            "approval_id": approval.get("approval_id"),
            "scope": merged.get("scope", {}),
            "approved_fact_count": sum(1 for item in facts if item.get("status") in ("active", "approved", "confirmed")),
            "pending_fact_count": sum(1 for item in facts if item.get("status") in ("pending_review", "conflict")),
        }
        save_json(active_path(workspace), active_payload)
        published = True
    else:
        active_payload = active

    visible_dir = locate_workspace(workspace) / "07_我的产出" / "05_机构知识更新"
    ensure_dir(visible_dir)
    visible_name = version + ("_机构知识更新.md" if published else "_机构知识候选.md")
    with io.open(str(visible_dir / visible_name), "w", encoding="utf-8") as handle:
        handle.write(render_runtime(merged, version))
    print(json.dumps({
        "status": "published" if published else "candidate_saved",
        "version": version,
        "active_version": active_payload.get("active_version"),
        "approved_fact_count": sum(1 for item in facts if item.get("status") in ("active", "approved", "confirmed")),
        "pending_fact_count": sum(1 for item in facts if item.get("status") in ("pending_review", "conflict")),
        "package_path": str(package_path),
        "runtime_context_path": str(runtime_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
