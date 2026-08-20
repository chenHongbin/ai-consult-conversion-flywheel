#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Store and optionally publish a redacted patient-decision insight candidate.

This is an aggregate, evidence-linked layer. It never stores patient identity
or raw conversations and it does not accept self-declared approval as enough
for publication.
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
URL = re.compile(r"https?://|www\.", re.I)

FORBIDDEN_PROFILE_TERMS = (
    "高价值患者", "低价值患者", "难成交患者", "成交潜力", "支付能力",
    "收入", "职业", "学历", "性格", "心理疾病", "依从性", "忠诚度",
    "容易被恐惧", "容易被稀缺", "容易被优惠刺激", "患者画像事实",
)


def load_json(path, default):
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
    return bool(scan_value(value) or URL.search(text))


def has_any(value, terms):
    return any(term in json.dumps(value, ensure_ascii=False) for term in terms)


def insight_root(workspace):
    return locate_workspace(workspace) / "_系统" / "患者洞察"


def active_path(workspace):
    return insight_root(workspace) / "active.json"


def load_active(workspace):
    active = load_json(active_path(workspace), {})
    package_path = active.get("package_path")
    package = load_json(expand_path(package_path), {}) if package_path else {}
    if not package:
        package = {"schema_version": "1.0-patient-insight", "decision_states": [], "doubt_intents": [], "practice_scenarios": []}
    return active, package


def item_key(item):
    return item.get("state_id") or item.get("intent_id") or item.get("scenario_id") or item.get("id") or item.get("name")


def merge_items(base, updates):
    merged = list(base or [])
    positions = {item_key(item): index for index, item in enumerate(merged) if isinstance(item, dict)}
    for item in updates or []:
        key = item_key(item)
        if not key:
            continue
        if key in positions:
            merged[positions[key]] = item
        else:
            positions[key] = len(merged)
            merged.append(item)
    return merged


def validate_item(kind, item):
    errors = []
    if not isinstance(item, dict):
        return ["{0} item must be an object".format(kind)]
    if not item_key(item):
        errors.append("{0} item missing stable id".format(kind))
    if not (item.get("evidence_refs") or item.get("support_case_ids")):
        errors.append("{0} {1} missing evidence_refs or support_case_ids".format(kind, item_key(item)))
    if not item.get("counterexample_ids"):
        errors.append("{0} {1} missing counterexample_ids".format(kind, item_key(item)))
    if not item.get("scope") and kind != "practice_scenarios":
        errors.append("{0} {1} missing scope".format(kind, item_key(item)))
    if not item.get("independent_dedup_clusters"):
        errors.append("{0} {1} missing independent_dedup_clusters".format(kind, item_key(item)))
    if kind == "practice_scenarios":
        if item.get("real_patient_content") is not False:
            errors.append("practice scenario {0} must set real_patient_content=false".format(item_key(item)))
        if not item.get("source_state_id") or not item.get("source_intent_id"):
            errors.append("practice scenario {0} missing source state/intent".format(item_key(item)))
    return errors


def validate_candidate(candidate, publish=False):
    errors = []
    if not isinstance(candidate, dict):
        return ["candidate must be a JSON object"]
    if candidate.get("schema_version") != "1.0-patient-insight-candidate":
        errors.append("unsupported schema_version")
    if contains_sensitive(candidate):
        errors.append("candidate contains possible identifier or external URL")
    if has_any(candidate, FORBIDDEN_PROFILE_TERMS):
        errors.append("candidate contains forbidden patient profiling term")
    summary = candidate.get("evidence_summary") or {}
    if not summary.get("independent_dedup_clusters") or not summary.get("denominator"):
        errors.append("evidence_summary needs independent_dedup_clusters and denominator")
    delta = candidate.get("delta") or {}
    for kind, key in (("decision_states", "decision_states_upsert"), ("doubt_intents", "doubt_intents_upsert"), ("practice_scenarios", "practice_scenarios_upsert")):
        value = delta.get(key, [])
        if not isinstance(value, list):
            errors.append("{0} must be a list".format(key))
            continue
        for item in value:
            errors.extend(validate_item(kind, item))
            if publish and isinstance(item, dict) and item.get("review_status") not in ("approved", "active", "confirmed"):
                errors.append("--publish requires {0} {1} review_status=approved/active/confirmed".format(kind, item_key(item)))
    return errors


def next_version(active):
    match = re.match(r"v(\d+)\.(\d+)", str(active.get("active_version") or "v1.4"))
    if not match:
        return "v1.5"
    return "v{0}.{1}".format(match.group(1), int(match.group(2)) + 1)


def render_runtime(package, version):
    scope = package.get("scope") or {}
    lines = [
        "# 当前患者决策洞察 {0}".format(version),
        "",
        "这是群体级、脱敏后的决策状态与陪练索引，不是患者个人档案。",
        "适用范围：机构={0}；科室={1}；病种/项目={2}；渠道={3}".format(
            scope.get("institution", "待确认"), scope.get("department", "待确认"),
            scope.get("disease_or_project", "待确认"), scope.get("channel", "通用")),
        "",
        "## 决策状态（当前对话中只作为待验证假设）",
    ]
    for item in package.get("decision_states", []):
        if item.get("review_status") not in ("approved", "active", "confirmed"):
            continue
        lines.append("### {0}".format(item.get("name", item_key(item))))
        lines.append("- 可观察信号：{0}".format("；".join(item.get("observable_signals", []))))
        lines.append("- 先确认：{0}".format(item.get("validation_question", "先确认患者真实顾虑")))
        lines.append("- 下一步：{0}".format(item.get("recommended_next_action", "按确认结果推进")))
        lines.append("- 证据边界：样本簇 {0}；反例 {1}".format(item.get("independent_dedup_clusters", 0), ", ".join(item.get("counterexample_ids", []))))
    lines.extend(["", "## 疑义意图"])
    for item in package.get("doubt_intents", []):
        if item.get("review_status") not in ("approved", "active", "confirmed"):
            continue
        lines.append("- **{0}**：先问 {1}；处理结构：{2}".format(
            item.get("name", item_key(item)), item.get("validation_question", "确认顾虑"),
            item.get("response_structure", "事实 + 选择 + 下一步")))
    lines.extend(["", "## 陪练约束", "- 只使用合成患者场景，不读取真实患者隐性画像。",
                  "- 一次只练一个动作，并在复盘后要求重答。",
                  "- 未确认的医生、价格、疗效、周期和流程使用待确认标记。",
                  "- 涉及诊断、用药、检查解读和急症时停止销售推进并转执业人员。"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Commit a patient decision insight candidate.")
    parser.add_argument("workspace_root")
    parser.add_argument("candidate")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--approval-id", help="independent manager approval receipt bound to this candidate")
    parser.add_argument("--version")
    args = parser.parse_args()
    workspace = expand_path(args.workspace_root)
    candidate_path = expand_path(args.candidate)
    candidate = load_json(candidate_path, None)
    errors = validate_candidate(candidate, publish=args.publish)
    approval = None
    if args.publish:
        try:
            approval = validate_approval(workspace, "patient_insight", candidate_path, args.approval_id)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        print(json.dumps({"status": "rejected", "errors": errors}, ensure_ascii=False))
        return 2
    root = insight_root(workspace)
    ensure_dir(root / "versions")
    active, base = load_active(workspace)
    try:
        version = validate_version(args.version or next_version(active))
    except ValueError as exc:
        print(json.dumps({"status": "rejected", "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    delta = candidate.get("delta") or {}
    merged = {
        "schema_version": "1.0-patient-insight",
        "version": version,
        "created_at": datetime.datetime.now().isoformat(),
        "candidate_hash": sha256(candidate_path),
        "scope": candidate.get("scope") or base.get("scope", {}),
        "evidence_summary": candidate.get("evidence_summary", {}),
        "decision_states": merge_items(base.get("decision_states", []), delta.get("decision_states_upsert", [])),
        "doubt_intents": merge_items(base.get("doubt_intents", []), delta.get("doubt_intents_upsert", [])),
        "practice_scenarios": merge_items(base.get("practice_scenarios", []), delta.get("practice_scenarios_upsert", [])),
        "approval_id": approval.get("approval_id") if approval else None,
    }
    version_dir = root / "versions" / version
    ensure_dir(version_dir)
    package_path = version_dir / "patient-insights.json"
    runtime_path = version_dir / "patient-insights-runtime.md"
    save_json(package_path, merged)
    with io.open(str(runtime_path), "w", encoding="utf-8") as handle:
        handle.write(render_runtime(merged, version))
    archive = root.parent / "患者洞察候选" / version
    save_json(archive / "patient-insight-candidate.json", candidate)
    save_json(archive / "merged-preview.json", merged)
    published = False
    if args.publish:
        save_json(active_path(workspace), {
            "schema_version": "1.0",
            "status": "active",
            "active_version": version,
            "package_path": str(package_path),
            "runtime_context_path": str(runtime_path),
            "published_at": datetime.datetime.now().isoformat(),
            "source_run_id": candidate.get("source_run_id"),
            "approval_id": approval.get("approval_id"),
            "scope": merged.get("scope", {}),
            "approved_state_count": sum(1 for item in merged["decision_states"] if item.get("review_status") in ("approved", "active", "confirmed")),
            "approved_intent_count": sum(1 for item in merged["doubt_intents"] if item.get("review_status") in ("approved", "active", "confirmed")),
        })
        published = True
    visible = locate_workspace(workspace) / "07_我的产出" / "05_患者决策洞察与陪练"
    ensure_dir(visible)
    name = version + ("_患者决策洞察与陪练.md" if published else "_患者决策洞察候选.md")
    with io.open(str(visible / name), "w", encoding="utf-8") as handle:
        handle.write(render_runtime(merged, version))
    print(json.dumps({"status": "published" if published else "candidate_saved", "version": version,
                      "active_version": (version if published else active.get("active_version")),
                      "package_path": str(package_path), "runtime_context_path": str(runtime_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
