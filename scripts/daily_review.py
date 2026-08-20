#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V2.1 daily consultation review queue, case reports and aggregations.

The Python layer owns deterministic state, stable IDs, leases, report storage
and projections.  The Agent host owns OCR/transcription and semantic analysis,
then commits a structured report through this command.
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from compat import ensure_dir, expand_path
from approval_ledger import require_manager
from medical_safety import validate_analysis_safety
from privacy_guard import scan_value
from management_data import (
    SAMPLES_FILE,
    TRAINING_FILE,
    append_jsonl,
    latest_by,
    load_json,
    load_jsonl,
    locate_workspace,
    management_root,
    now_iso,
    save_json,
    team_breakpoint,
)


REPORT_CONTRACT = "2.1-case-report"
TASK_CONTRACT = "2.1-analysis-task"
MAX_ATTEMPTS = 3
CONSULT_TASK_TYPES = {
    "audio_transcription_and_consult_analysis",
    "image_slice_ocr_and_chat_analysis",
    "chat_or_text_analysis",
}
PATIENT_ID_RE = re.compile(
    r"(?:患者id|病人id|patient[_ -]?id|case[_ -]?id)\s*[:：_-]?\s*([A-Za-z0-9_-]{2,40})",
    re.I,
)
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WECHAT_RE = re.compile(r"(?:微信号|wxid)\s*[:：]?\s*[A-Za-z][A-Za-z0-9_-]{5,}", re.I)


def stable_id(prefix, *parts):
    value = "\n".join(str(part or "") for part in parts)
    return "{}-{}".format(prefix, hashlib.sha1(value.encode("utf-8")).hexdigest()[:16])


def safe_name(value):
    value = str(value or "unknown").strip()
    for char in "/\\:*?\"<>|":
        value = value.replace(char, "_")
    return value or "unknown"


def review_root(workspace):
    root = locate_workspace(workspace)
    path = root / "_系统" / "每日复盘"
    for child in ("tasks", "leases", "cases", "projections", "grouping", "indexes/by-date", "outbox"):
        ensure_dir(path / child)
    return root, path


def atomic_save_json(path, value):
    ensure_dir(path.parent)
    descriptor, temp_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_name, str(path)) if hasattr(os, "replace") else os.rename(temp_name, str(path))


def validate_date(value):
    try:
        parsed = datetime.datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError:
        raise ValueError("date must use YYYY-MM-DD")
    if parsed.strftime("%Y-%m-%d") != str(value):
        raise ValueError("date must use YYYY-MM-DD")
    return str(value)


def append_event(store, event, task=None):
    row = dict(event)
    row.setdefault("schema_version", "2.1-review-event")
    row.setdefault("created_at", now_iso())
    if task:
        for field in (
            "analysis_task_id", "artifact_id", "conversation_id", "patient_case_id",
            "consultant_day_id", "team_day_id", "employee_id", "work_date",
        ):
            if task.get(field) not in (None, ""):
                row.setdefault(field, task.get(field))
    append_jsonl(store / "events.jsonl", row)


def employee_from_source(source, original_source=""):
    for value in (original_source, source):
        parts = Path(str(value or "")).parts
        for marker in ("01_成员", "团队档案"):
            if marker in parts:
                index = parts.index(marker)
                if index + 1 < len(parts):
                    folder = parts[index + 1]
                    employee_id = folder.split("_", 1)[0]
                    employee_name = folder.split("_", 1)[1] if "_" in folder else folder
                    return employee_id, employee_name, folder
    return "unknown", "待确认", "unknown"


def patient_group_suggestion(employee_id, original_source, source):
    """Return only an unconfirmed suggestion based on explicit structural hints."""
    value = str(original_source or source or "")
    parts = Path(value).parts
    explicit = None
    reason = None
    match = PATIENT_ID_RE.search(Path(value).stem)
    if match:
        explicit = match.group(1)
        reason = "文件名包含明确患者内部编号"
    elif "01_今天放这里" in parts:
        index = parts.index("01_今天放这里")
        if index + 2 < len(parts):
            explicit = parts[index + 1]
            reason = "材料位于同一患者子文件夹"
    if not explicit:
        return {
            "grouping_state": "ungrouped",
            "suggested_group_id": None,
            "suggestion_reason": None,
        }
    return {
        "grouping_state": "suggested",
        "suggested_group_id": stable_id("PG", employee_id, explicit),
        "suggestion_reason": reason,
    }


def task_path(store, task_id):
    return store / "tasks" / (safe_name(task_id) + ".json")


def load_tasks(store, work_date=None):
    rows = []
    paths = []
    if work_date:
        work_date = validate_date(work_date)
        index_path = store / "indexes" / "by-date" / (work_date + ".jsonl")
        if index_path.is_file():
            ids = sorted(set(row.get("analysis_task_id") for row in load_jsonl(index_path)
                             if row.get("analysis_task_id")))
            paths = [task_path(store, task_id) for task_id in ids]
    if not paths:
        paths = sorted((store / "tasks").glob("*.json"))
    for path in paths:
        value = load_json(path, {}) or {}
        if value.get("analysis_task_id") and (not work_date or value.get("work_date") == work_date):
            rows.append(value)
    return rows


def index_task(store, task):
    work_date = validate_date(task.get("work_date"))
    index_path = store / "indexes" / "by-date" / (work_date + ".jsonl")
    if not index_path.is_file():
        for existing_path in sorted((store / "tasks").glob("*.json")):
            existing = load_json(existing_path, {}) or {}
            if existing.get("analysis_task_id") and existing.get("work_date") == work_date:
                append_jsonl(index_path, {"analysis_task_id": existing["analysis_task_id"],
                                          "work_date": work_date})
    indexed = set(row.get("analysis_task_id") for row in load_jsonl(index_path))
    if task.get("analysis_task_id") not in indexed:
        append_jsonl(index_path, {"analysis_task_id": task.get("analysis_task_id"),
                                  "work_date": work_date})


def register_source_task(workspace, source_task, work_date):
    """Register one consultation artifact without claiming it was analyzed."""
    root, store = review_root(workspace)
    work_date = validate_date(work_date)
    source = source_task.get("source") or ""
    original = source_task.get("original_source") or ""
    source_hash = source_task.get("source_hash") or stable_id("HASH", source)
    inferred_id, inferred_name, inferred_folder = employee_from_source(source, original)
    employee_id = source_task.get("employee_id") or inferred_id
    employee_name = source_task.get("employee_name") or inferred_name
    employee_folder = source_task.get("employee_folder") or inferred_folder
    artifact_id = stable_id("ART", source_hash)
    conversation_id = stable_id("CONV", employee_id, source_hash)
    analysis_task_id = stable_id("AT", conversation_id, REPORT_CONTRACT)
    path = task_path(store, analysis_task_id)
    register_lock = store / "leases" / (safe_name(analysis_task_id) + ".register.lock")
    try:
        lock_descriptor = os.open(str(register_lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(lock_descriptor)
    except OSError:
        existing = load_json(path, {}) or {}
        if existing:
            return existing, False
        raise ValueError("task registration is already in progress")
    existing = load_json(path, {}) or {}
    if existing:
        try:
            register_lock.unlink()
        except OSError:
            pass
        return existing, False
    grouping = patient_group_suggestion(employee_id, original, source)
    patient_case_id = stable_id("PC", conversation_id)
    task = {
        "schema_version": TASK_CONTRACT,
        "analysis_contract": REPORT_CONTRACT,
        "analysis_task_id": analysis_task_id,
        "legacy_task_id": source_task.get("task_id"),
        "artifact_id": artifact_id,
        "conversation_id": conversation_id,
        "patient_case_id": patient_case_id,
        "consultant_day_id": stable_id("CD", employee_id, work_date),
        "team_day_id": stable_id("TD", work_date),
        "source": source,
        "original_source": original,
        "source_hash": source_hash,
        "task_type": source_task.get("task_type"),
        "employee_id": employee_id,
        "employee_name": employee_name,
        "employee_folder": employee_folder,
        "work_date": work_date,
        "status": "prepared",
        "attempts": 0,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "result_ready": False,
        "outcome": "unknown",
        "outcome_provenance": "missing",
    }
    task.update(grouping)
    try:
        atomic_save_json(path, task)
        index_task(store, task)
        append_event(store, {"event": "task_prepared"}, task)
        if task.get("suggested_group_id"):
            append_event(store, {
                "event": "patient_group_suggested",
                "suggested_group_id": task.get("suggested_group_id"),
                "reason": task.get("suggestion_reason"),
            }, task)
    finally:
        try:
            register_lock.unlink()
        except OSError:
            pass
    return task, True


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def register_direct_task(workspace, source, employee_id, employee_name, work_date, medium, source_hash=""):
    root = locate_workspace(workspace)
    source_path = expand_path(source)
    if not source_hash:
        if not source_path.is_file():
            raise ValueError("direct source file not found; provide a readable file or source_hash")
        source_hash = file_sha256(source_path)
    try:
        source_value = str(source_path.relative_to(root))
    except ValueError:
        source_value = str(source_path)
    task_types = {
        "audio": "audio_transcription_and_consult_analysis",
        "image": "image_slice_ocr_and_chat_analysis",
        "wechat": "image_slice_ocr_and_chat_analysis",
        "text": "chat_or_text_analysis",
        "chat": "chat_or_text_analysis",
    }
    folder = None
    member_root = root / "08_团队管理" / "01_成员"
    for candidate in member_root.glob(safe_name(employee_id) + "_*") if member_root.is_dir() else []:
        if candidate.is_dir():
            folder = candidate.name
            break
    folder = folder or ("{}_{}".format(safe_name(employee_id), safe_name(employee_name)) if employee_id != "unknown" else "unknown")
    source_task = {
        "task_id": stable_id("DIRECT", source_value, source_hash),
        "source": source_value,
        "source_hash": source_hash,
        "task_type": task_types[medium],
        "employee_id": employee_id,
        "employee_name": employee_name,
        "employee_folder": folder,
        "work_date": work_date,
    }
    return register_source_task(root, source_task, work_date)


def parse_iso(value):
    try:
        return datetime.datetime.strptime(str(value)[:19], "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None


def lease_expired(task, now):
    expires = parse_iso(task.get("lease_expires_at"))
    return expires is None or expires <= now


def active_training_for(root, employee_id):
    store = management_root(root)
    latest = latest_by(load_jsonl(store / TRAINING_FILE), "action_id")
    active = [row for row in latest.values()
              if row.get("scope") == "employee"
              and row.get("target_id") == employee_id
              and row.get("status") != "closed"]
    if not active:
        return None
    return sorted(active, key=lambda row: row.get("updated_at") or row.get("created_at") or "", reverse=True)[0]


def minimal_context_pack(root, task, current_training):
    release = load_json(root / "_系统" / "发布" / "active.json", {}) or {}
    capability = load_json(root / "_系统" / "当前能力包" / "active.json", {}) or {}
    knowledge = load_json(root / "_系统" / "当前机构知识" / "active.json", {}) or {}
    return {
        "schema_version": "2.1-analysis-context-pack",
        "analysis_contract": REPORT_CONTRACT,
        "scope": {
            "employee_id": task.get("employee_id"),
            "work_date": task.get("work_date"),
            "patient_case_id": task.get("patient_case_id"),
        },
        "references": [
            "references/v2.1-case-report-contract.md",
            "references/consultation-eight-step-method.md",
            "references/safety-and-sanitization.md",
        ],
        "release_version": release.get("release_version") or "base_only",
        "capability_runtime": capability.get("runtime_context_path"),
        "institution_knowledge_runtime": knowledge.get("runtime_context_path"),
        "current_training": current_training,
        "outcome_status": "unknown",
    }


def claim_tasks(workspace, owner, batch_size=20, lease_minutes=30, work_date=None, task_id=None):
    root, store = review_root(workspace)
    now = datetime.datetime.now().replace(microsecond=0)
    claimed = []
    for task in sorted(load_tasks(store, work_date), key=lambda item: (item.get("work_date") or "", item.get("created_at") or "")):
        if len(claimed) >= batch_size:
            break
        if task_id and task.get("analysis_task_id") != task_id:
            continue
        if work_date and task.get("work_date") != work_date:
            continue
        status = task.get("status")
        retryable = status == "failed" and int(task.get("attempts") or 0) < MAX_ATTEMPTS
        expired = status in ("claimed", "processing") and lease_expired(task, now)
        if status != "prepared" and not retryable and not expired:
            continue
        lease_path = store / "leases" / (safe_name(task["analysis_task_id"]) + ".lock")
        if lease_path.exists():
            lease = load_json(lease_path, {}) or {}
            if not lease_expired(lease, now):
                continue
            try:
                lease_path.unlink()
            except OSError:
                continue
        try:
            descriptor = os.open(str(lease_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            continue
        expires = now + datetime.timedelta(minutes=lease_minutes)
        lease = {
            "analysis_task_id": task["analysis_task_id"],
            "owner": owner,
            "claimed_at": now.isoformat(),
            "lease_expires_at": expires.isoformat(),
            "lease_token": uuid.uuid4().hex,
        }
        os.write(descriptor, json.dumps(lease, ensure_ascii=False).encode("utf-8"))
        os.close(descriptor)
        task["status"] = "claimed"
        task["lease_owner"] = owner
        task["lease_expires_at"] = expires.isoformat()
        task["lease_token"] = lease["lease_token"]
        task["attempts"] = int(task.get("attempts") or 0) + 1
        task["current_training"] = active_training_for(root, task.get("employee_id"))
        task["context_pack"] = minimal_context_pack(root, task, task.get("current_training"))
        task["updated_at"] = now_iso()
        atomic_save_json(task_path(store, task["analysis_task_id"]), task)
        append_event(store, {"event": "task_claimed", "owner": owner, "attempt": task["attempts"]}, task)
        claimed.append(task)
    return claimed


def validate_analysis(analysis):
    required = (
        "summary", "material_quality", "stage", "patient_concern", "breakpoint",
        "patient_facts", "consultant_actions", "strengths", "verified_strength",
        "missed_opportunities", "champion_comparison", "next_service_action",
        "safe_response_draft", "training_action", "evidence", "risk_level",
    )
    missing = [field for field in required if analysis.get(field) in (None, "", [])]
    if missing:
        raise ValueError("analysis missing fields: {}".format(", ".join(missing)))
    for field in ("patient_facts", "consultant_actions", "strengths", "missed_opportunities", "champion_comparison"):
        if not isinstance(analysis.get(field), list):
            raise ValueError("{} must be a list".format(field))
    evidence = analysis.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    if not evidence:
        raise ValueError("at least one evidence item is required")
    for index, item in enumerate(evidence):
        if not isinstance(item, dict) or not item.get("locator"):
            raise ValueError("evidence[{}] must include locator".format(index))
    training = analysis.get("training_action")
    if not isinstance(training, dict) or not training.get("key_action") or not training.get("pass_criteria"):
        raise ValueError("training_action must include key_action and pass_criteria")
    privacy_findings = scan_value(analysis)
    if privacy_findings:
        raise ValueError("analysis contains possible personal identifier: {0}".format(", ".join(privacy_findings)))
    safety_errors = validate_analysis_safety(analysis)
    if safety_errors:
        raise ValueError("; ".join(safety_errors))


def deep_analysis_reasons(analysis):
    reasons = []
    if analysis.get("risk_level") == "P0":
        reasons.append("P0_risk")
    signals = analysis.get("case_signals") or []
    if isinstance(signals, str):
        signals = [signals]
    selected = {
        "rejected", "lost", "no_reply", "complaint", "high_intent_stalled",
        "key_stage_stalled", "representative_positive", "representative_negative",
    }
    reasons.extend(sorted(selected.intersection(set(signals))))
    if analysis.get("novel_issue"):
        reasons.append("novel_issue")
    return reasons


def value_lines(value):
    if value in (None, "", []):
        return ["- 未知"]
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                result.append("- {}".format(item.get("text") or item.get("action") or json.dumps(item, ensure_ascii=False)))
            else:
                result.append("- {}".format(item))
        return result or ["- 未知"]
    return ["- {}".format(value)]


def render_case_report(task, analysis):
    evidence_lines = []
    for item in analysis.get("evidence") or []:
        evidence_lines.append("- `{}`：{}".format(item.get("locator"), item.get("quote") or item.get("claim") or "证据"))
    training = analysis.get("training_action") or {}
    lines = [
        "# {}".format("患者跨渠道组合复盘" if task.get("is_patient_bundle") else "咨询逐案复盘"),
        "",
        "- 案例 ID：`{}`".format(task.get("patient_case_id")),
        "- 对话 ID：`{}`".format(task.get("conversation_id")),
        "- 员工：{}（{}）".format(task.get("employee_name"), task.get("employee_id")),
        "- 日期：{}".format(task.get("work_date")),
        "- 材料质量：{}".format(analysis.get("material_quality") or "待确认"),
        "- 风险等级：{}".format(analysis.get("risk_level")),
        "",
        "## 三行结论",
        "",
    ]
    if task.get("is_patient_bundle"):
        lines.insert(6, "- 组合对话：{}".format("、".join(task.get("conversation_ids") or [])))
    lines.extend(value_lines(analysis.get("summary")))
    lines.extend(["", "## 患者决策与阶段", "", "- 当前阶段：{}".format(analysis.get("stage")),
                  "- 主要顾虑：{}".format(analysis.get("patient_concern")),
                  "- 核心断点：{}".format(analysis.get("breakpoint")), ""])
    for title, field in (
        ("做得好的地方", "strengths"),
        ("错失机会", "missed_opportunities"),
        ("销冠/最佳实践对照", "champion_comparison"),
    ):
        lines.extend(["## " + title, ""])
        lines.extend(value_lines(analysis.get(field)))
        lines.append("")
    lines.extend([
        "## 下一步",
        "",
        "- 服务动作：{}".format(analysis.get("next_service_action")),
        "- 安全表达：{}".format(analysis.get("safe_response_draft") or "需结合机构已确认事实生成"),
        "",
        "## 本次只训练一个动作",
        "",
        "- 动作：{}".format(training.get("key_action")),
        "- 通过标准：{}".format(training.get("pass_criteria")),
        "- 复查场景：{}".format(training.get("review_scenario") or "下一条同类患者咨询"),
        "",
        "## 原始证据",
        "",
    ])
    lines.extend(evidence_lines)
    lines.extend(["", "## 未知与待确认", ""])
    lines.extend(value_lines(analysis.get("unknowns")))
    lines.append("")
    return "\n".join(lines)


def report_paths(root, store, task):
    internal = store / "cases" / task["work_date"] / safe_name(task["employee_id"]) / task["conversation_id"]
    member_base = root / "08_团队管理" / "01_成员" / task.get("employee_folder", "unknown")
    if member_base.is_dir():
        visible = member_base / "03_个人报告" / "逐案" / task["work_date"]
    else:
        visible = root / "07_我的产出" / "04_咨询分析与陪练" / "逐案" / task["work_date"]
    ensure_dir(internal)
    ensure_dir(visible)
    name = "{}-{}.md".format(task["work_date"], task["conversation_id"])
    return internal / "analysis.json", internal / "report.md", visible / name


def remove_lease(store, task_id):
    lease = store / "leases" / (safe_name(task_id) + ".lock")
    try:
        lease.unlink()
    except OSError:
        pass


def sample_projection(task, analysis):
    evidence_refs = [item.get("locator") for item in analysis.get("evidence") or [] if item.get("locator")]
    training = analysis.get("training_action") or {}
    return {
        "schema_version": "2.1.3-communication-sample",
        "analysis_task_id": task.get("analysis_task_id"),
        "sample_id": task.get("conversation_id"),
        "artifact_id": task.get("artifact_id"),
        "conversation_id": task.get("conversation_id"),
        "patient_case_id": task.get("patient_case_id"),
        "consultant_day_id": task.get("consultant_day_id"),
        "team_day_id": task.get("team_day_id"),
        "source": task.get("source"),
        "source_hash": task.get("source_hash"),
        "employee_id": task.get("employee_id"),
        "employee_name": task.get("employee_name"),
        "date": task.get("work_date"),
        "medium": analysis.get("medium") or task.get("task_type"),
        "stage": analysis.get("stage"),
        "patient_facts": analysis.get("patient_facts") or [],
        "patient_uncertainty": analysis.get("patient_concern"),
        "uncertainties": analysis.get("unknowns") or [],
        "breakpoint": analysis.get("breakpoint"),
        "consultant_actions": analysis.get("consultant_actions") or [],
        "evidence_refs": evidence_refs,
        "next_patient_service_action": analysis.get("next_service_action"),
        "patient_next_action": analysis.get("next_service_action"),
        "employee_gap": analysis.get("breakpoint"),
        "verified_strength": analysis.get("verified_strength") or ((analysis.get("strengths") or [""])[0] if isinstance(analysis.get("strengths"), list) else analysis.get("strengths")),
        "team_candidate_pattern": analysis.get("team_candidate_pattern") or analysis.get("breakpoint"),
        "team_pattern_candidate": analysis.get("team_candidate_pattern") or analysis.get("breakpoint"),
        "outcome": "unknown",
        "outcome_provenance": "missing",
        "response_draft": analysis.get("safe_response_draft") or "",
        "training_key_action": training.get("key_action"),
        "risk_level": analysis.get("risk_level"),
        "report_path": task.get("report_path"),
        "created_at": analysis.get("completed_at"),
        "updated_at": analysis.get("completed_at"),
    }


def finalize_projections(root, store, task, analysis):
    """Idempotently materialize downstream projections from a completed task."""
    outbox_path = store / "outbox" / (safe_name(task["analysis_task_id"]) + ".json")
    if not task.get("is_patient_bundle"):
        sample = sample_projection(task, analysis)
        current = latest_by(load_jsonl(management_root(root) / SAMPLES_FILE), "sample_id").get(sample["sample_id"])
        if not current or current.get("analysis_task_id") != task.get("analysis_task_id"):
            append_jsonl(management_root(root) / SAMPLES_FILE, sample)
        update_training_followup(root, task, analysis)
    atomic_save_json(outbox_path, {
        "schema_version": "2.1.3-projection-outbox",
        "analysis_task_id": task.get("analysis_task_id"),
        "status": "completed",
        "completed_at": now_iso(),
    })


def commit_analysis(workspace, task_id, analysis, owner="agent", lease_token=""):
    root, store = review_root(workspace)
    path = task_path(store, task_id)
    task = load_json(path, {}) or {}
    if not task:
        raise ValueError("analysis task not found")
    if task.get("status") == "completed":
        stored_analysis = load_json(root / task.get("analysis_json", ""), {}) or {}
        if stored_analysis:
            finalize_projections(root, store, task, stored_analysis)
        return task
    if task.get("status") not in ("claimed", "processing"):
        raise ValueError("task must be claimed before completion")
    if task.get("lease_owner") and task.get("lease_owner") != owner:
        raise ValueError("task is leased by another owner")
    if not lease_token or task.get("lease_token") != lease_token:
        raise ValueError("valid lease token is required")
    if lease_expired(task, datetime.datetime.now().replace(microsecond=0)):
        raise ValueError("lease expired; claim the task again")
    validate_analysis(analysis)
    if task.get("current_training") and not task.get("is_patient_bundle") and not isinstance(analysis.get("training_followup"), dict):
        raise ValueError("training_followup is required when the employee has an active training action")
    deep_reasons = deep_analysis_reasons(analysis)
    override = task.get("manager_deep_override")
    deep_required = bool(deep_reasons) and override is not False
    if analysis.get("risk_level") == "P0":
        deep_required = True
    if override is True:
        deep_required = True
        if "manager_selected" not in deep_reasons:
            deep_reasons.append("manager_selected")
    if deep_required and not analysis.get("deep_analysis"):
        raise ValueError("deep_analysis is required for: {}".format(", ".join(deep_reasons)))
    analysis = dict(analysis)
    analysis.update({
        "schema_version": REPORT_CONTRACT,
        "analysis_task_id": task_id,
        "artifact_id": task.get("artifact_id"),
        "conversation_id": task.get("conversation_id"),
        "patient_case_id": task.get("patient_case_id"),
        "consultant_day_id": task.get("consultant_day_id"),
        "team_day_id": task.get("team_day_id"),
        "employee_id": task.get("employee_id"),
        "work_date": task.get("work_date"),
        "outcome": "unknown",
        "outcome_provenance": "missing",
        "completed_at": now_iso(),
        "deep_analysis_reasons": deep_reasons,
    })
    internal_json, internal_md, visible_md = report_paths(root, store, task)
    atomic_save_json(internal_json, analysis)
    text = render_case_report(task, analysis)
    for output in (internal_md, visible_md):
        with io.open(str(output), "w", encoding="utf-8") as handle:
            handle.write(text)
    atomic_save_json(store / "outbox" / (safe_name(task_id) + ".json"), {
        "schema_version": "2.1.3-projection-outbox",
        "analysis_task_id": task_id,
        "analysis_json": str(internal_json.relative_to(root)),
        "status": "pending",
        "created_at": now_iso(),
    })
    task.update({
        "status": "completed",
        "updated_at": now_iso(),
        "completed_at": now_iso(),
        "analysis_json": str(internal_json.relative_to(root)),
        "report_path": str(visible_md.relative_to(root)),
        "risk_level": analysis.get("risk_level"),
        "deep_analysis": bool(analysis.get("deep_analysis") or analysis.get("risk_level") == "P0"),
        "result_ready": True,
    })
    task.pop("lease_owner", None)
    task.pop("lease_expires_at", None)
    task.pop("lease_token", None)
    atomic_save_json(path, task)
    remove_lease(store, task_id)
    append_event(store, {"event": "analysis_completed", "owner": owner, "report_path": task["report_path"]}, task)
    finalize_projections(root, store, task, analysis)
    return task


def set_deep_override(workspace, task_id, selected, reviewer="manager"):
    require_manager(workspace)
    _, store = review_root(workspace)
    path = task_path(store, task_id)
    task = load_json(path, {}) or {}
    if not task:
        raise ValueError("analysis task not found")
    task["manager_deep_override"] = bool(selected)
    task["deep_override_reviewer"] = reviewer
    task["deep_override_at"] = now_iso()
    task["updated_at"] = now_iso()
    atomic_save_json(path, task)
    append_event(store, {"event": "deep_analysis_{}".format("selected" if selected else "removed"),
                         "reviewer": reviewer}, task)
    return task


def close_training(workspace, action_id, reviewer="manager", note=""):
    require_manager(workspace)
    root = locate_workspace(workspace)
    path = management_root(root) / TRAINING_FILE
    current = latest_by(load_jsonl(path), "action_id").get(action_id)
    if not current:
        raise ValueError("training action not found")
    row = dict(current)
    row["status"] = "closed"
    row["closed_by"] = reviewer
    row["closed_note"] = note
    row["updated_at"] = now_iso()
    append_jsonl(path, row)
    _, store = review_root(root)
    append_event(store, {"event": "training_closed", "action_id": action_id,
                         "reviewer": reviewer, "note": note})
    return row


def update_training_followup(root, task, analysis):
    current = task.get("current_training") or {}
    followup = analysis.get("training_followup") or {}
    if not current or not followup:
        return None
    latest = latest_by(load_jsonl(management_root(root) / TRAINING_FILE), "action_id")
    row = dict(latest.get(current.get("action_id")) or current)
    if row.get("status") == "closed":
        return row
    review_samples = list(row.get("review_samples") or [])
    if task.get("conversation_id") in review_samples:
        return row
    if task.get("conversation_id") not in review_samples:
        review_samples.append(task.get("conversation_id"))
    observed_cases = list(row.get("observed_patient_case_ids") or [])
    observed = bool(followup.get("target_action_observed"))
    if observed and task.get("patient_case_id") not in observed_cases:
        observed_cases.append(task.get("patient_case_id"))
    row["review_samples"] = review_samples
    row["observed_patient_case_ids"] = observed_cases
    row["matching_case_count"] = int(row.get("matching_case_count") or 0) + 1
    row["observed_case_count"] = len(observed_cases)
    row["latest_followup"] = {
        "conversation_id": task.get("conversation_id"),
        "patient_case_id": task.get("patient_case_id"),
        "target_action_observed": observed,
        "evidence_locator": followup.get("evidence_locator"),
        "note": followup.get("note"),
    }
    if len(observed_cases) >= 2:
        row["behavior_status"] = "stable"
        row["status"] = "passed"
    elif observed_cases:
        row["behavior_status"] = "observed"
        row["status"] = "awaiting_review"
    else:
        row["behavior_status"] = "needs_training"
        row["status"] = "in_training"
    row["updated_at"] = now_iso()
    append_jsonl(management_root(root) / TRAINING_FILE, row)
    return row


def fail_task(workspace, task_id, reason, owner="agent", lease_token="", quarantined=False):
    _, store = review_root(workspace)
    path = task_path(store, task_id)
    task = load_json(path, {}) or {}
    if not task:
        raise ValueError("analysis task not found")
    if task.get("status") == "completed":
        raise ValueError("completed task is terminal and cannot be failed")
    if task.get("status") not in ("claimed", "processing"):
        raise ValueError("task must be claimed before failure")
    if task.get("lease_owner") != owner or not lease_token or task.get("lease_token") != lease_token:
        raise ValueError("valid owner and lease token are required")
    task["status"] = "quarantined" if quarantined else "failed"
    task["failure_reason"] = str(reason)
    task["retryable"] = not quarantined and int(task.get("attempts") or 0) < MAX_ATTEMPTS
    task["updated_at"] = now_iso()
    task.pop("lease_owner", None)
    task.pop("lease_expires_at", None)
    task.pop("lease_token", None)
    atomic_save_json(path, task)
    remove_lease(store, task_id)
    append_event(store, {"event": task["status"], "owner": owner, "reason": str(reason)}, task)
    return task


def decide_group(workspace, suggestion_id, decision, reviewer="manager"):
    require_manager(workspace)
    root, store = review_root(workspace)
    matched = [task for task in load_tasks(store)
               if task.get("suggested_group_id") == suggestion_id and not task.get("is_patient_bundle")]
    if not matched:
        raise ValueError("patient grouping suggestion not found")
    patient_case_id = stable_id("PC", suggestion_id) if decision == "confirmed" else None
    for task in matched:
        task["grouping_state"] = decision
        if patient_case_id:
            task["patient_case_id"] = patient_case_id
        task["group_reviewed_by"] = reviewer
        task["group_reviewed_at"] = now_iso()
        atomic_save_json(task_path(store, task["analysis_task_id"]), task)
        analysis_path = task.get("analysis_json")
        if analysis_path:
            full = root / analysis_path
            analysis = load_json(full, {}) or {}
            if analysis and patient_case_id:
                analysis["patient_case_id"] = patient_case_id
                atomic_save_json(full, analysis)
                rendered = render_case_report(task, analysis)
                for report_file in (full.parent / "report.md", root / task.get("report_path", "")):
                    if str(report_file) != str(root):
                        ensure_dir(report_file.parent)
                        with io.open(str(report_file), "w", encoding="utf-8") as handle:
                            handle.write(rendered)
        sample_path = management_root(root) / SAMPLES_FILE
        samples = latest_by(load_jsonl(sample_path), "sample_id")
        sample = samples.get(task.get("conversation_id"))
        if sample:
            sample = dict(sample)
            sample["patient_case_id"] = task.get("patient_case_id")
            sample["grouping_state"] = decision
            sample["updated_at"] = now_iso()
            append_jsonl(sample_path, sample)
        append_event(store, {"event": "patient_group_{}".format(decision), "reviewer": reviewer,
                             "suggested_group_id": suggestion_id}, task)
    bundle_task = None
    if decision == "confirmed" and len(matched) >= 2:
        work_date = sorted(task.get("work_date") or "" for task in matched)[-1]
        employee_id = matched[0].get("employee_id")
        source_conversation_ids = sorted(task.get("conversation_id") for task in matched)
        bundle_conversation_id = stable_id("BUNDLE", patient_case_id, *source_conversation_ids)
        bundle_task_id = stable_id("AT", bundle_conversation_id, REPORT_CONTRACT)
        bundle_path = task_path(store, bundle_task_id)
        bundle_task = load_json(bundle_path, {}) or {}
        if not bundle_task:
            bundle_task = {
                "schema_version": TASK_CONTRACT,
                "analysis_contract": REPORT_CONTRACT,
                "analysis_task_id": bundle_task_id,
                "artifact_id": stable_id("ARTB", patient_case_id),
                "artifact_ids": [task.get("artifact_id") for task in matched],
                "conversation_id": bundle_conversation_id,
                "conversation_ids": source_conversation_ids,
                "patient_case_id": patient_case_id,
                "consultant_day_id": stable_id("CD", employee_id, work_date),
                "team_day_id": stable_id("TD", work_date),
                "source": "patient-case-bundle:{}".format(patient_case_id),
                "source_items": [task.get("source") for task in matched],
                "source_hash": stable_id("HASHB", *sorted(task.get("source_hash") for task in matched)),
                "task_type": "patient_case_bundle_analysis",
                "is_patient_bundle": True,
                "employee_id": employee_id,
                "employee_name": matched[0].get("employee_name"),
                "employee_folder": matched[0].get("employee_folder"),
                "work_date": work_date,
                "status": "prepared",
                "attempts": 0,
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "grouping_state": "confirmed",
                "suggested_group_id": suggestion_id,
                "result_ready": False,
                "outcome": "unknown",
                "outcome_provenance": "missing",
            }
            atomic_save_json(bundle_path, bundle_task)
            index_task(store, bundle_task)
            append_event(store, {"event": "patient_bundle_task_prepared",
                                 "source_conversation_ids": bundle_task["conversation_ids"]}, bundle_task)
    projection = {
        "schema_version": "2.1-patient-group-decision",
        "suggested_group_id": suggestion_id,
        "decision": decision,
        "patient_case_id": patient_case_id,
        "conversation_ids": [task.get("conversation_id") for task in matched],
        "bundle_analysis_task_id": bundle_task.get("analysis_task_id") if bundle_task else None,
        "reviewer": reviewer,
        "reviewed_at": now_iso(),
    }
    atomic_save_json(store / "grouping" / (safe_name(suggestion_id) + ".json"), projection)
    return projection


def top_pattern(pairs, field):
    value_cases = defaultdict(set)
    all_cases = set()
    for task, report in pairs:
        case_id = task.get("patient_case_id") or task.get("conversation_id")
        all_cases.add(case_id)
        value = report.get(field)
        if isinstance(value, list):
            value = value[0] if value else None
        if value and value not in ("unknown", "missing", "未知"):
            value_cases[str(value)].add(case_id)
    if not value_cases:
        return None
    label = sorted(value_cases, key=lambda item: (-len(value_cases[item]), item))[0]
    count = len(value_cases[label])
    ratio = float(count) / len(all_cases) if all_cases else 0.0
    return {"label": label, "count": count, "ratio": ratio,
            "status": "stable" if count >= 3 and ratio >= 0.5 else "observation"}


def member_report_root(root, employee_folder):
    member = root / "08_团队管理" / "01_成员" / employee_folder
    if member.is_dir():
        path = member / "03_个人报告"
    else:
        path = root / "07_我的产出" / "04_咨询分析与陪练" / "员工报告" / safe_name(employee_folder)
    ensure_dir(path)
    return path


def create_training(root, task, report, work_date):
    training = report.get("training_action") or {}
    if not training.get("key_action"):
        return None
    store = management_root(root)
    existing = latest_by(load_jsonl(store / TRAINING_FILE), "action_id")
    active = [row for row in existing.values()
              if row.get("scope") == "employee"
              and row.get("target_id") == task.get("employee_id")
              and row.get("status") != "closed"]
    if active:
        return sorted(active, key=lambda row: row.get("updated_at") or row.get("created_at") or "", reverse=True)[0]
    action_id = stable_id("TA", task.get("employee_id"), training.get("key_action"), work_date)
    row = {
        "schema_version": "2.1-training-action",
        "action_id": action_id,
        "learning_chain_id": stable_id("LC", task.get("employee_id"), training.get("key_action")),
        "scope": "employee",
        "target_id": task.get("employee_id"),
        "title": "今日唯一训练：{}".format(training.get("key_action")),
        "topic": training.get("key_action"),
        "reason": report.get("breakpoint"),
        "key_action": training.get("key_action"),
        "pass_criteria": training.get("pass_criteria"),
        "review_method": training.get("review_scenario") or "下一条同类患者咨询自动复查",
        "champion_refs": report.get("champion_refs") or [],
        "failure_refs": [task.get("conversation_id")],
        "review_samples": [],
        "behavior_status": "unobserved",
        "status": "pending",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    if action_id not in existing:
        append_jsonl(store / TRAINING_FILE, row)
    folder = member_report_root(root, task.get("employee_folder")) / "训练卡"
    ensure_dir(folder)
    output = folder / "{}-{}.md".format(work_date, action_id)
    text = """# 单动作训练卡

- 员工：{employee_name}（{employee_id}）
- 触发场景：{scenario}
- 当前断点：{breakpoint}
- 今天只练：{action}
- 通过标准：{criteria}
- 真实案例复查：{review}
- 当前状态：未观察
""".format(
        employee_name=task.get("employee_name"), employee_id=task.get("employee_id"),
        scenario=training.get("trigger_scenario") or report.get("patient_concern") or "同类患者顾虑",
        breakpoint=report.get("breakpoint"), action=training.get("key_action"),
        criteria=training.get("pass_criteria"), review=row["review_method"],
    )
    with io.open(str(output), "w", encoding="utf-8") as handle:
        handle.write(text)
    row["report_path"] = str(output.relative_to(root))
    return row


def patient_priority_queue(pairs, work_date):
    reason_priority = {
        "P0_risk": (0, "P0", "医疗或合规风险"),
        "high_intent_stalled": (1, "P1", "高意向停滞"),
        "complaint": (2, "P1", "投诉风险"),
        "no_reply": (3, "P1", "连续无回复"),
        "rejected": (4, "P1", "明确拒绝"),
        "lost": (4, "P1", "流失"),
        "key_stage_stalled": (5, "P2", "关键阶段停滞"),
        "novel_issue": (6, "P2", "新型问题"),
    }
    by_case = {}
    for task, report in pairs:
        reasons = list(report.get("deep_analysis_reasons") or deep_analysis_reasons(report))
        if not reasons and not report.get("deep_analysis"):
            continue
        ranked = [reason_priority[item] + (item,) for item in reasons if item in reason_priority]
        rank, priority, label, reason_id = sorted(ranked)[0] if ranked else (7, "P2", "主管重点深析", "deep_analysis")
        case_id = task.get("patient_case_id") or task.get("conversation_id")
        evidence = [item.get("locator") for item in report.get("evidence") or [] if item.get("locator")]
        item = {
            "priority_rank": rank,
            "priority": priority,
            "reason_id": reason_id,
            "reason": label,
            "patient_case_id": case_id,
            "conversation_id": task.get("conversation_id"),
            "employee_id": task.get("employee_id"),
            "employee_name": task.get("employee_name"),
            "report_path": task.get("report_path"),
            "evidence": evidence,
            "next_action": report.get("next_service_action"),
            "due_date": work_date,
        }
        previous = by_case.get(case_id)
        if previous is None or item["priority_rank"] < previous["priority_rank"]:
            by_case[case_id] = item
    return sorted(by_case.values(), key=lambda item: (item["priority_rank"], item.get("employee_id") or "", item.get("patient_case_id") or ""))


def aggregate_daily(workspace, work_date):
    root, store = review_root(workspace)
    completed = [task for task in load_tasks(store, work_date)
                 if task.get("work_date") == work_date and task.get("status") == "completed"]
    reports = []
    for task in completed:
        report = load_json(root / task.get("analysis_json", ""), {}) or {}
        if report:
            reports.append((task, report))
    canonical_by_case = {}
    for task, report in reports:
        key = (task.get("employee_id"), task.get("patient_case_id") or task.get("conversation_id"))
        previous = canonical_by_case.get(key)
        if previous is None or (task.get("is_patient_bundle") and (
                not previous[0].get("is_patient_bundle")
                or (task.get("completed_at") or "") >= (previous[0].get("completed_at") or ""))):
            canonical_by_case[key] = (task, report)
    canonical_reports = list(canonical_by_case.values())
    by_employee = defaultdict(list)
    by_employee_all = defaultdict(list)
    for task, report in canonical_reports:
        by_employee[task.get("employee_id")].append((task, report))
    for task, report in reports:
        by_employee_all[task.get("employee_id")].append((task, report))
    employee_summaries = []
    for employee_id in sorted(by_employee):
        pairs = by_employee[employee_id]
        all_pairs = by_employee_all[employee_id]
        unique_case_count = len(set(task.get("patient_case_id") or task.get("conversation_id") for task, _ in pairs))
        gap = top_pattern(pairs, "breakpoint")
        strength = top_pattern(pairs, "verified_strength")
        p0 = [task for task, report in all_pairs if report.get("risk_level") == "P0"]
        representative = None
        if gap:
            representative = next(((task, report) for task, report in pairs if report.get("breakpoint") == gap["label"]), pairs[0])
        else:
            representative = pairs[0]
        training = active_training_for(root, employee_id)
        if not training and ((gap or {}).get("status") == "stable" or p0):
            training = create_training(root, representative[0], representative[1], work_date)
        summary = {
            "employee_id": employee_id,
            "employee_name": pairs[0][0].get("employee_name"),
            "employee_folder": pairs[0][0].get("employee_folder"),
            "case_count": unique_case_count,
            "conversation_count": len([task for task, _ in all_pairs if not task.get("is_patient_bundle")]),
            "patient_bundle_report_count": len([task for task, _ in all_pairs if task.get("is_patient_bundle")]),
            "main_gap": gap,
            "main_strength": strength,
            "p0_count": len(p0),
            "training": training,
            "case_reports": [task.get("report_path") for task, _ in all_pairs],
        }
        employee_summaries.append(summary)
        output_root = member_report_root(root, summary["employee_folder"])
        output = output_root / "{}-员工日报.md".format(work_date)
        lines = [
            "# {} 员工咨询日报".format(work_date), "",
            "- 员工：{}（{}）".format(summary["employee_name"], employee_id),
            "- 已完成患者案例：{}；对话/材料：{}；患者组合报告：{}".format(
                unique_case_count, summary["conversation_count"], summary["patient_bundle_report_count"]),
            "- P0 风险：{}".format(len(p0)), "",
            "## 今日长板", "",
            "- {}：{}（{}/{} 个案例）".format(
                (strength or {}).get("label") or "证据不足",
                "稳定长板" if (strength or {}).get("status") == "stable" else "当日观察",
                (strength or {}).get("count", 0), unique_case_count), "",
            "## 今日主要短板", "",
            "- {}：{}（{}/{} 个案例）".format(
                (gap or {}).get("label") or "证据不足",
                "重复短板" if (gap or {}).get("status") == "stable" else "当日观察",
                (gap or {}).get("count", 0), unique_case_count), "",
            "## 当前唯一训练", "",
            "- {}".format((training or {}).get("key_action") or "暂不生成，等待更多证据"),
            "- 通过标准：{}".format((training or {}).get("pass_criteria") or "待确认"), "",
            "- 行为状态：{}".format({
                "unobserved": "未观察", "observed": "已出现", "stable": "已稳定",
                "needs_training": "需继续训练",
            }.get((training or {}).get("behavior_status"), "未观察")), "",
            "## 逐案报告", "",
        ]
        lines.extend("- `{}`".format(path) for path in summary["case_reports"])
        lines.append("")
        with io.open(str(output), "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        summary["report_path"] = str(output.relative_to(root))

    breakpoint_rows = [dict(report, employee_id=task.get("employee_id"),
                            patient_case_id=task.get("patient_case_id"),
                            conversation_id=task.get("conversation_id")) for task, report in canonical_reports]
    team_gap_result = team_breakpoint(breakpoint_rows)
    team_gap = team_gap_result.get("label") if team_gap_result and team_gap_result.get("status") == "stable" else None
    p0_pairs = [(task, report) for task, report in reports if report.get("risk_level") == "P0"]
    deep_pairs = [(task, report) for task, report in reports if report.get("deep_analysis")]
    patient_priorities = patient_priority_queue(canonical_reports, work_date)
    representative_positive = next(((task, report) for task, report in canonical_reports
                                    if "representative_positive" in (report.get("case_signals") or [])), None)
    representative_negative = next(((task, report) for task, report in canonical_reports
                                    if "representative_negative" in (report.get("case_signals") or [])), None)
    all_tasks_today = load_tasks(store, work_date)
    counts = Counter(task.get("status") for task in all_tasks_today)
    team_output = root / "08_团队管理" / "04_团队报告" / "01_日报" / "{}-团队咨询日报.md".format(work_date)
    ensure_dir(team_output.parent)
    lines = [
        "# {} 团队咨询日报".format(work_date), "",
        "## 今天只看这四件事", "",
        "- 分析任务进度：收到 {}，完成 {}，失败 {}，待处理 {}。".format(
            len(all_tasks_today), counts.get("completed", 0), counts.get("failed", 0) + counts.get("quarantined", 0),
            len(all_tasks_today) - counts.get("completed", 0) - counts.get("failed", 0) - counts.get("quarantined", 0)),
        "- P0 风险：{} 个。".format(len(p0_pairs)),
        "- 重点深析：{} 个。".format(len(deep_pairs)),
        "- 团队共同断点：{}。".format(team_gap or "尚未达到跨 2 人、3 个独立案例的证据门槛"),
        "- 今日训练：重复短板达到证据门槛或已有训练的员工，只执行员工日报中的一个动作。", "",
        "## 今天必须先处理的患者", "",
    ]
    if patient_priorities:
        for item in patient_priorities:
            lines.append("- {priority}｜{employee}｜`{case}`｜{reason}｜下一步：{action}｜证据：`{report}`".format(
                priority=item["priority"], employee=item.get("employee_name"), case=item.get("patient_case_id"),
                reason=item.get("reason"), action=item.get("next_action") or "主管判断", report=item.get("report_path")))
    else:
        lines.append("- 已完成材料中暂无需要优先介入的患者；仍需查看失败和待处理任务。")
    lines.extend(["",
        "## 每名员工一个重点", "",
    ])
    for item in employee_summaries:
        lines.append("- {}：{}；训练：{}。".format(
            item["employee_name"], (item.get("main_gap") or {}).get("label") or "证据不足",
            (item.get("training") or {}).get("key_action") or "待确认"))
    lines.extend(["", "## P0 风险案例", ""])
    if p0_pairs:
        lines.extend("- {}：`{}`".format(task.get("employee_name"), task.get("report_path")) for task, _ in p0_pairs)
    else:
        lines.append("- 今日未发现 P0 风险；不代表不存在，只代表已处理材料中未命中。")
    lines.extend(["", "## 今日正反代表案例", ""])
    lines.append("- 正案例：{}".format(
        "`{}`".format(representative_positive[0].get("report_path")) if representative_positive else "尚未选出"))
    lines.append("- 负案例：{}".format(
        "`{}`".format(representative_negative[0].get("report_path")) if representative_negative else "尚未选出"))
    lines.append("")
    with io.open(str(team_output), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    projection = {
        "schema_version": "2.1-team-day-projection",
        "team_day_id": stable_id("TD", work_date),
        "work_date": work_date,
        "task_counts": dict(counts),
        "received_count": len(all_tasks_today),
        "completed_count": len([task for task, _ in reports if not task.get("is_patient_bundle")]),
        "patient_bundle_report_count": len([task for task, _ in reports if task.get("is_patient_bundle")]),
        "completed_patient_case_count": len(canonical_reports),
        "p0_count": len(p0_pairs),
        "deep_analysis_count": len(deep_pairs),
        "team_breakpoint": team_gap,
        "team_breakpoint_evidence": team_gap_result,
        "patient_priorities": patient_priorities,
        "representative_positive": representative_positive[0].get("conversation_id") if representative_positive else None,
        "representative_negative": representative_negative[0].get("conversation_id") if representative_negative else None,
        "employees": employee_summaries,
        "team_report_path": str(team_output.relative_to(root)),
        "generated_at": now_iso(),
        "outcome_status": "unknown",
        "promise_boundary": "behavior_and_management_efficiency_only",
    }
    atomic_save_json(store / "projections" / (work_date + "-team-day.json"), projection)
    append_event(store, {"event": "daily_projection_built", "team_report_path": projection["team_report_path"]})
    return projection


def queue_status(workspace, work_date=None):
    _, store = review_root(workspace)
    rows = load_tasks(store, work_date)
    counts = Counter(task.get("status") for task in rows)
    suggestions = Counter(task.get("grouping_state") for task in rows)
    return {
        "schema_version": "2.1-queue-status",
        "work_date": work_date,
        "total": len(rows),
        "status_counts": dict(counts),
        "grouping_counts": dict(suggestions),
        "pending": sum(counts.get(value, 0) for value in ("prepared", "claimed", "processing")),
        "retryable_failed": len([task for task in rows if task.get("status") == "failed" and int(task.get("attempts") or 0) < MAX_ATTEMPTS]),
    }


def employee_view(workspace, employee_query, work_date):
    root, store = review_root(workspace)
    work_date = validate_date(work_date)
    projection = load_json(store / "projections" / (work_date + "-team-day.json"), {}) or {}
    query = str(employee_query or "").strip()
    matches = [item for item in projection.get("employees") or []
               if query in (str(item.get("employee_id") or ""), str(item.get("employee_name") or ""))
               or query in str(item.get("employee_name") or "")]
    if not matches:
        raise ValueError("employee not found in current daily projection")
    if len(matches) > 1:
        raise ValueError("employee query is ambiguous; use employee id")
    employee = matches[0]
    priorities = [item for item in projection.get("patient_priorities") or []
                  if item.get("employee_id") == employee.get("employee_id")]
    return {
        "schema_version": "2.1.3-employee-review",
        "work_date": work_date,
        "employee": employee,
        "patient_priorities": priorities,
        "main_strength": employee.get("main_strength"),
        "main_gap": employee.get("main_gap"),
        "training": employee.get("training"),
        "case_reports": employee.get("case_reports") or [],
    }


def audit_review(workspace, repair=False):
    root, store = review_root(workspace)
    issues = []
    repaired = []
    now = datetime.datetime.now().replace(microsecond=0)
    tasks = load_tasks(store)
    for outbox_path in sorted((store / "outbox").glob("*.json")):
        outbox = load_json(outbox_path, {}) or {}
        if outbox.get("status") != "pending":
            continue
        task_id = outbox.get("analysis_task_id")
        issues.append({"type": "projection_finalization_pending", "task_id": task_id})
        if repair:
            pending_task = load_json(task_path(store, task_id), {}) or {}
            pending_analysis = load_json(root / pending_task.get("analysis_json", ""), {}) or {}
            if pending_task and pending_analysis:
                finalize_projections(root, store, pending_task, pending_analysis)
                repaired.append(task_id)
    unconfirmed_cases = defaultdict(list)
    case_employees = defaultdict(set)
    for task in tasks:
        if task.get("patient_case_id"):
            case_employees[task.get("patient_case_id")].add(task.get("employee_id"))
        if (not task.get("is_patient_bundle") and task.get("grouping_state") != "confirmed"
                and task.get("patient_case_id")):
            unconfirmed_cases[task.get("patient_case_id")].append(task.get("conversation_id"))
    for case_id, conversation_ids in unconfirmed_cases.items():
        if len(set(conversation_ids)) > 1:
            issues.append({"type": "unconfirmed_patient_merge", "patient_case_id": case_id,
                           "conversation_ids": sorted(set(conversation_ids))})
    for case_id, employee_ids in case_employees.items():
        if len(employee_ids) > 1:
            issues.append({"type": "cross_employee_patient_case", "patient_case_id": case_id,
                           "employee_ids": sorted(employee_ids)})
    for task in tasks:
        task_id = task.get("analysis_task_id")
        if task.get("status") == "completed":
            analysis_path = root / task.get("analysis_json", "")
            report_path = root / task.get("report_path", "")
            missing = []
            if not analysis_path.is_file():
                missing.append("analysis_json")
            if not report_path.is_file():
                missing.append("report_path")
            if missing:
                issues.append({"type": "completed_artifact_missing", "task_id": task_id, "missing": missing})
                if repair:
                    task["status"] = "prepared"
                    task["result_ready"] = False
                    task["repair_reason"] = "completed_artifact_missing"
                    task["updated_at"] = now_iso()
                    atomic_save_json(task_path(store, task_id), task)
                    repaired.append(task_id)
        if task.get("status") in ("claimed", "processing") and lease_expired(task, now):
            issues.append({"type": "expired_lease", "task_id": task_id})
            if repair:
                task["status"] = "prepared"
                task.pop("lease_owner", None)
                task.pop("lease_expires_at", None)
                task.pop("lease_token", None)
                task["updated_at"] = now_iso()
                atomic_save_json(task_path(store, task_id), task)
                remove_lease(store, task_id)
                repaired.append(task_id)
        analysis_value = load_json(root / task.get("analysis_json", ""), {}) if task.get("analysis_json") else {}
        if analysis_value and analysis_value.get("outcome") not in (None, "", "unknown"):
            issues.append({"type": "v21_outcome_boundary_violation", "task_id": task_id})
    trainings = latest_by(load_jsonl(management_root(root) / TRAINING_FILE), "action_id")
    active_by_employee = defaultdict(list)
    for row in trainings.values():
        if row.get("scope") == "employee" and row.get("status") != "closed":
            active_by_employee[row.get("target_id")].append(row.get("action_id"))
    for employee_id, action_ids in active_by_employee.items():
        if len(action_ids) > 1:
            issues.append({"type": "multiple_active_trainings", "employee_id": employee_id,
                           "action_ids": sorted(action_ids)})
    if repair and repaired:
        append_event(store, {"event": "review_audit_repaired", "task_ids": sorted(set(repaired))})
    if repair:
        for task in tasks:
            if task.get("analysis_task_id") and task.get("work_date"):
                index_path = store / "indexes" / "by-date" / (task["work_date"] + ".jsonl")
                indexed = set(row.get("analysis_task_id") for row in load_jsonl(index_path))
                if task["analysis_task_id"] not in indexed:
                    append_jsonl(index_path, {"analysis_task_id": task["analysis_task_id"],
                                              "work_date": task["work_date"]})
    return {
        "schema_version": "2.1-review-audit",
        "status": "healthy" if not issues else ("repaired" if repair and repaired else "issues_found"),
        "issue_count": len(issues),
        "issues": issues,
        "repaired_task_ids": sorted(set(repaired)),
    }


def read_analysis(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("analysis JSON must be an object")
    return value


def main():
    parser = argparse.ArgumentParser(description="Run the V2.1 daily consultation review state machine.")
    sub = parser.add_subparsers(dest="command")

    register = sub.add_parser("register", help="register one direct upload in the same review queue")
    register.add_argument("workspace_root")
    register.add_argument("--source", required=True)
    register.add_argument("--source-hash", default="")
    register.add_argument("--employee-id", required=True)
    register.add_argument("--employee-name", default="")
    register.add_argument("--date", required=True)
    register.add_argument("--medium", choices=("audio", "image", "wechat", "text", "chat"), required=True)

    claim = sub.add_parser("claim", help="claim a bounded batch for an Agent host")
    claim.add_argument("workspace_root")
    claim.add_argument("--owner", required=True)
    claim.add_argument("--batch-size", type=int, default=20)
    claim.add_argument("--lease-minutes", type=int, default=30)
    claim.add_argument("--date")
    claim.add_argument("--task-id")

    complete = sub.add_parser("complete", help="commit one structured case analysis")
    complete.add_argument("workspace_root")
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--analysis-json", required=True)
    complete.add_argument("--owner", default="agent")
    complete.add_argument("--lease-token", required=True)

    fail = sub.add_parser("fail", help="record a visible, retryable failure")
    fail.add_argument("workspace_root")
    fail.add_argument("--task-id", required=True)
    fail.add_argument("--reason", required=True)
    fail.add_argument("--owner", default="agent")
    fail.add_argument("--lease-token", required=True)
    fail.add_argument("--quarantine", action="store_true")

    group = sub.add_parser("group", help="confirm or reject a patient grouping suggestion")
    group.add_argument("workspace_root")
    group.add_argument("--suggestion-id", required=True)
    group.add_argument("--decision", choices=("confirmed", "rejected"), required=True)
    group.add_argument("--reviewer", default="manager")

    prioritize = sub.add_parser("prioritize", help="manager override for priority deep analysis")
    prioritize.add_argument("workspace_root")
    prioritize.add_argument("--task-id", required=True)
    prioritize.add_argument("--deep", choices=("yes", "no"), required=True)
    prioritize.add_argument("--reviewer", default="manager")

    close = sub.add_parser("close-training", help="close the one active employee training before replacing it")
    close.add_argument("workspace_root")
    close.add_argument("--action-id", required=True)
    close.add_argument("--reviewer", default="manager")
    close.add_argument("--note", default="")

    aggregate = sub.add_parser("aggregate", help="build employee, team and training outputs")
    aggregate.add_argument("workspace_root")
    aggregate.add_argument("--date", required=True)

    status = sub.add_parser("status", help="show current queue projection")
    status.add_argument("workspace_root")
    status.add_argument("--date")

    employee = sub.add_parser("employee", help="show one consultant's daily strengths, gap, training and priority patients")
    employee.add_argument("workspace_root")
    employee.add_argument("--employee", required=True, help="employee id or exact name")
    employee.add_argument("--date", required=True)

    audit = sub.add_parser("audit", help="inspect review state and optionally repair safe queue projections")
    audit.add_argument("workspace_root")
    audit.add_argument("--repair", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "register":
            task, created = register_direct_task(args.workspace_root, args.source, args.employee_id, args.employee_name,
                                                 args.date, args.medium, args.source_hash)
            result = {"status": "registered", "created": created, "task": task}
        elif args.command == "claim":
            result = {"status": "claimed", "tasks": claim_tasks(args.workspace_root, args.owner, args.batch_size, args.lease_minutes, args.date, args.task_id)}
        elif args.command == "complete":
            result = {"status": "completed", "task": commit_analysis(args.workspace_root, args.task_id, read_analysis(expand_path(args.analysis_json)), args.owner, args.lease_token)}
        elif args.command == "fail":
            result = {"status": "recorded", "task": fail_task(args.workspace_root, args.task_id, args.reason, args.owner, args.lease_token, args.quarantine)}
        elif args.command == "group":
            result = {"status": args.decision, "group": decide_group(args.workspace_root, args.suggestion_id, args.decision, args.reviewer)}
        elif args.command == "prioritize":
            result = {"status": "updated", "task": set_deep_override(args.workspace_root, args.task_id, args.deep == "yes", args.reviewer)}
        elif args.command == "close-training":
            result = {"status": "closed", "training": close_training(args.workspace_root, args.action_id, args.reviewer, args.note)}
        elif args.command == "aggregate":
            result = {"status": "generated", "projection": aggregate_daily(args.workspace_root, args.date)}
        elif args.command == "status":
            result = queue_status(args.workspace_root, args.date)
        elif args.command == "employee":
            result = {"status": "ok", "review": employee_view(args.workspace_root, args.employee, args.date)}
        elif args.command == "audit":
            result = audit_review(args.workspace_root, args.repair)
        else:
            parser.print_help()
            return 2
    except (IOError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
