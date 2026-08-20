#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append-only content feedback and hash-bound manager review for V2.1.3."""

import argparse
import datetime
import hashlib
import io
import json
import os
import sys
from pathlib import Path

from compat import ensure_dir
from approval_ledger import create_approval
from medical_safety import validate_patient_facing_text
from privacy_guard import scan_value
from workspace_paths import assert_within, locate_workspace

try:
    import fcntl
except ImportError:
    fcntl = None


CONTENT_TYPES = ("followup", "moments", "education", "private_message")
EVENT_STATUSES = ("draft", "revised", "sent", "skipped")
OUTCOME_VALUES = ("unknown", "yes", "no")
REPLY_QUALITIES = ("unknown", "positive", "neutral", "negative")
REVIEW_DECISIONS = ("approve", "revise", "reject")
def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def content_root(selected):
    root = locate_workspace(selected) / "_系统" / "内容资产"
    ensure_dir(root)
    return root


def append_jsonl(path, row):
    ensure_dir(path.parent)
    with io.open(str(path), "a", encoding="utf-8") as handle:
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_jsonl(path, rows):
    ensure_dir(path.parent)
    temporary = path.with_name(path.name + ".{0}.tmp".format(os.getpid()))
    with io.open(str(temporary), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path)) if hasattr(os, "replace") else os.rename(str(temporary), str(path))


def load_jsonl(path):
    rows = []
    if not path.is_file():
        return rows
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    rows.append({"_invalid": True, "_line": line_number})
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except IOError:
        return []
    return rows


def load_json(path, default=None):
    if not path.is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def split_refs(values):
    return sorted(set(str(item).strip() for item in (values or []) if str(item).strip()))


def make_id(prefix, *values):
    raw = "|".join(str(value or "") for value in values) + "|" + datetime.datetime.now().isoformat()
    return prefix + "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def contains_sensitive(value):
    return bool(scan_value(value))


def output_path(workspace, output_ref):
    if not output_ref:
        raise ValueError("output_ref is required")
    root = locate_workspace(workspace)
    candidate = Path(output_ref)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = assert_within(candidate, root, "output_ref")
    if not candidate.is_file():
        raise ValueError("output_ref does not exist inside workspace")
    return candidate


def evidence_path(workspace, evidence_ref):
    base = str(evidence_ref or "").split("#", 1)[0].split("@", 1)[0].strip()
    if not base:
        raise ValueError("empty evidence_ref")
    root = locate_workspace(workspace)
    candidate = Path(base)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = assert_within(candidate, root, "evidence_ref")
    if not candidate.is_file():
        raise ValueError("evidence_ref does not resolve to an existing workspace file")
    return candidate


def load_profile(workspace):
    root = locate_workspace(workspace)
    return load_json(root / "_系统" / "首次设置" / "confirmed-profile.json", {}) or {}


def group_events(rows):
    grouped = {}
    for row in rows:
        if row.get("_invalid") or not row.get("asset_id"):
            continue
        grouped.setdefault(row["asset_id"], []).append(row)
    for asset_id in grouped:
        grouped[asset_id].sort(key=lambda item: item.get("created_at") or "")
    return grouped


def latest_reviews(rows):
    result = {}
    for row in rows:
        asset_id = row.get("asset_id")
        if not asset_id or row.get("_invalid"):
            continue
        previous = result.get(asset_id)
        if previous is None or (row.get("created_at") or "") >= (previous.get("created_at") or ""):
            result[asset_id] = row
    return result


def asset_summary(asset_id, events, review=None):
    latest = events[-1]
    sent = any(item.get("status") == "sent" for item in events)
    positive = any(item.get("reply_quality") == "positive" or item.get("appointed") == "yes" or item.get("arrived") == "yes" for item in events)
    evidence_refs = split_refs(
        ref for item in events for ref in item.get("evidence_refs", [])
    )
    knowledge_refs = split_refs(
        ref for item in events for ref in item.get("knowledge_refs", [])
    )
    candidate = sent and positive and bool(evidence_refs)
    return {
        "asset_id": asset_id,
        "content_type": latest.get("content_type"),
        "current_status": latest.get("status"),
        "case_id": latest.get("case_id"),
        "consultant_id": latest.get("consultant_id"),
        "channel": latest.get("channel"),
        "patient_stage": latest.get("patient_stage"),
        "concern": latest.get("concern"),
        "voice_scope": latest.get("voice_scope"),
        "output_ref": latest.get("output_ref"),
        "knowledge_refs": knowledge_refs,
        "evidence_refs": evidence_refs,
        "sent": sent,
        "positive_feedback": positive,
        "candidate": candidate,
        "review_decision": (review or {}).get("decision"),
        "reviewer": (review or {}).get("reviewer"),
        "event_count": len(events),
        "updated_at": latest.get("created_at"),
    }


def cmd_record(args):
    root = content_root(args.workspace_root)
    created_at = now_iso()
    asset_id = args.asset_id or make_id("content", args.content_type, args.case_id, args.output_ref)
    grouped = group_events(load_jsonl(root / "content-events.jsonl"))
    existing = grouped.get(asset_id, [])
    if not existing and args.status != "draft":
        return {"status": "rejected", "reason": "first_event_must_be_draft", "asset_id": asset_id}, 2
    if existing:
        first = existing[0]
        immutable = {
            "content_type": args.content_type,
            "case_id": args.case_id,
            "consultant_id": args.consultant_id,
            "channel": args.channel,
            "output_ref": args.output_ref,
        }
        for field, value in immutable.items():
            if value not in (None, "") and first.get(field) not in (None, "", value):
                return {"status": "rejected", "reason": "immutable_asset_field_changed", "field": field}, 2
        previous_status = existing[-1].get("status")
        allowed = {
            "draft": ("revised", "sent", "skipped"),
            "revised": ("revised", "sent", "skipped"),
            "sent": ("sent",),
            "skipped": (),
        }
        if args.status not in allowed.get(previous_status, ()):
            return {"status": "rejected", "reason": "invalid_status_transition", "from": previous_status, "to": args.status}, 2
        for field in ("content_type", "case_id", "consultant_id", "channel", "output_ref"):
            if not getattr(args, field) and first.get(field):
                setattr(args, field, first.get(field))
    user_fields = {
        "asset_id": asset_id,
        "output_ref": args.output_ref,
        "case_id": args.case_id,
        "consultant_id": args.consultant_id,
        "channel": args.channel,
        "patient_stage": args.patient_stage,
        "concern": args.concern,
        "knowledge_refs": args.knowledge_ref,
        "evidence_refs": args.evidence_ref,
        "note": args.note,
    }
    if contains_sensitive(user_fields):
        return {
            "status": "rejected",
            "reason": "possible_personal_identifier",
            "message": "内容资产日志只保存脱敏引用，不得写入手机号、身份证、邮箱、微信号或原始患者文本。",
        }, 2
    row = {
        "schema_version": "2.1.2-content-event",
        "event_id": make_id("ce", asset_id, args.status),
        "created_at": created_at,
        "asset_id": asset_id,
        "content_type": args.content_type,
        "status": args.status,
        "case_id": args.case_id or None,
        "consultant_id": args.consultant_id or None,
        "channel": args.channel or None,
        "patient_stage": args.patient_stage or None,
        "concern": args.concern or None,
        "voice_scope": args.voice_scope,
        "output_ref": args.output_ref,
        "knowledge_refs": split_refs(args.knowledge_ref),
        "evidence_refs": split_refs(args.evidence_ref),
        "replied": args.replied,
        "appointed": args.appointed,
        "arrived": args.arrived,
        "reply_quality": args.reply_quality,
        "note": args.note,
        "contains_raw_patient_material": False,
    }
    path = root / "content-events.jsonl"
    append_jsonl(path, row)
    return {
        "status": "recorded",
        "asset_id": asset_id,
        "event_status": args.status,
        "path": str(path),
        "record": row,
    }, 0


def cmd_review(args):
    if args.expires_at:
        try:
            datetime.datetime.strptime(args.expires_at, "%Y-%m-%d")
        except ValueError:
            return {"status": "blocked", "blocked_reasons": ["expires_at must use YYYY-MM-DD"]}, 2
    workspace = locate_workspace(args.workspace_root)
    role = load_json(workspace / "_系统" / "运行时角色.json", {}) or {}
    if role.get("role") != "manager":
        return {
            "status": "manager_confirmation_required",
            "asset_id": args.asset_id,
            "message": "内容资产批准只允许主管端执行；一线可以记录使用结果并提交候选。",
        }, 3
    root = content_root(args.workspace_root)
    events_path = root / "content-events.jsonl"
    reviews_path = root / "content-reviews.jsonl"
    approved_path = root / "approved-assets.jsonl"
    grouped = group_events(load_jsonl(events_path))
    events = grouped.get(args.asset_id, [])
    if not events:
        return {"status": "not_found", "asset_id": args.asset_id, "message": "未找到内容资产。"}, 2
    summary = asset_summary(args.asset_id, events)
    review_evidence = split_refs(args.evidence_ref)
    combined_evidence = split_refs(summary["evidence_refs"] + review_evidence)
    blocked_reasons = []
    if args.decision == "approve":
        if not summary["sent"]:
            blocked_reasons.append("批准为正式资产前必须有已发送记录")
        if not summary["positive_feedback"]:
            blocked_reasons.append("批准为正式资产前至少需要回复、预约或到院中的一个正向结果")
        if not combined_evidence:
            blocked_reasons.append("批准为正式资产前必须提供结果或原始证据引用")
        if not args.reviewer.strip():
            blocked_reasons.append("批准为正式资产前必须记录主管审核人")
        try:
            frozen_output = output_path(args.workspace_root, summary.get("output_ref"))
        except ValueError as exc:
            blocked_reasons.append(str(exc))
            frozen_output = None
        for reference in combined_evidence:
            try:
                evidence_path(args.workspace_root, reference)
            except ValueError as exc:
                blocked_reasons.append(str(exc) + "：" + reference)
        profile = load_profile(args.workspace_root)
        if not profile.get("institution") or not profile.get("department"):
            blocked_reasons.append("批准前必须完成机构和部门首次设置")
    if blocked_reasons:
        return {
            "status": "blocked",
            "asset_id": args.asset_id,
            "decision": args.decision,
            "blocked_reasons": blocked_reasons,
        }, 2
    approval = None
    approved_candidate = None
    if args.decision == "approve":
        with io.open(str(frozen_output), "r", encoding="utf-8") as handle:
            body = handle.read()
        if scan_value(body):
            return {"status": "blocked", "asset_id": args.asset_id,
                    "blocked_reasons": ["内容正文包含可能的个人身份或病历信息"]}, 2
        safety_errors = validate_patient_facing_text(body)
        if safety_errors:
            return {"status": "blocked", "asset_id": args.asset_id,
                    "blocked_reasons": safety_errors}, 2
        profile = load_profile(args.workspace_root)
        approved_candidate = dict(summary)
        approved_candidate.update({
            "schema_version": "2.1.3-content-asset-candidate",
            "scope": {"institution": profile.get("institution"), "department": profile.get("department")},
            "projects": profile.get("projects") or [],
            "effective_from": now_iso(),
            "expires_at": args.expires_at or None,
            "content_body": body,
            "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "output_ref": str(frozen_output.relative_to(
                assert_within(locate_workspace(args.workspace_root), locate_workspace(args.workspace_root), "workspace"))),
            "evidence_refs": combined_evidence,
        })
        candidate_dir = root / "candidates"
        ensure_dir(candidate_dir)
        candidate_path = candidate_dir / (args.asset_id + "-" + approved_candidate["content_hash"][:12] + ".json")
        with io.open(str(candidate_path), "w", encoding="utf-8") as handle:
            json.dump(approved_candidate, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        try:
            approval = create_approval(args.workspace_root, "content_asset", candidate_path,
                                       args.reviewer, args.note, [])
        except ValueError as exc:
            return {"status": "blocked", "asset_id": args.asset_id,
                    "blocked_reasons": [str(exc)]}, 2
    row = {
        "schema_version": "2.1.3-content-review",
        "review_id": make_id("cr", args.asset_id, args.decision, args.reviewer),
        "created_at": now_iso(),
        "asset_id": args.asset_id,
        "decision": args.decision,
        "reviewer": args.reviewer.strip(),
        "note": args.note,
        "evidence_refs": combined_evidence,
        "approval_id": approval.get("approval_id") if approval else None,
        "candidate_ref": str(candidate_path) if approval else None,
    }
    append_jsonl(reviews_path, row)
    latest = latest_reviews(load_jsonl(reviews_path))
    approved_rows = []
    for asset_id, asset_events in sorted(grouped.items()):
        review = latest.get(asset_id) or {}
        if review.get("decision") != "approve":
            continue
        asset = asset_summary(asset_id, asset_events, review)
        candidate_ref = review.get("candidate_ref")
        matching_candidate = load_json(Path(candidate_ref), {}) if candidate_ref else None
        if not matching_candidate:
            continue
        matching_candidate.update({
            "schema_version": "2.1.3-approved-content-asset",
            "approved_at": review.get("created_at"),
            "review_id": review.get("review_id"),
            "reviewer": review.get("reviewer"),
            "approval_id": review.get("approval_id"),
            "candidate_ref": str(assert_within(candidate_ref, root / "candidates", "candidate_ref")),
        })
        approved_rows.append(matching_candidate)
    atomic_write_jsonl(approved_path, approved_rows)
    return {
        "status": "reviewed",
        "asset_id": args.asset_id,
        "decision": args.decision,
        "review_path": str(reviews_path),
        "approved_path": str(approved_path) if args.decision == "approve" else None,
    }, 0


def cmd_status(args):
    root = content_root(args.workspace_root)
    grouped = group_events(load_jsonl(root / "content-events.jsonl"))
    reviews = latest_reviews(load_jsonl(root / "content-reviews.jsonl"))
    assets = [asset_summary(asset_id, grouped[asset_id], reviews.get(asset_id)) for asset_id in sorted(grouped)]
    if args.asset_id:
        assets = [item for item in assets if item["asset_id"] == args.asset_id]
        if not assets:
            return {"status": "not_found", "asset_id": args.asset_id}, 2
    counts = {
        "total": len(assets),
        "sent": len([item for item in assets if item["sent"]]),
        "candidates": len([item for item in assets if item["candidate"] and item["review_decision"] is None]),
        "approved": len([item for item in assets if item["review_decision"] == "approve"]),
        "needs_revision": len([item for item in assets if item["review_decision"] == "revise"]),
        "rejected": len([item for item in assets if item["review_decision"] == "reject"]),
    }
    return {"status": "ok", "counts": counts, "assets": assets, "root": str(root)}, 0


def build_parser():
    parser = argparse.ArgumentParser(description="Record and review V2.1.3 consultation content assets.")
    subparsers = parser.add_subparsers(dest="command")

    record = subparsers.add_parser("record", help="append a content usage/result event")
    record.add_argument("workspace_root")
    record.add_argument("--asset-id", default="")
    record.add_argument("--content-type", choices=CONTENT_TYPES, required=True)
    record.add_argument("--status", choices=EVENT_STATUSES, required=True)
    record.add_argument("--output-ref", required=True, help="path or stable reference to the generated content; do not pass raw patient text")
    record.add_argument("--case-id", default="")
    record.add_argument("--consultant-id", default="")
    record.add_argument("--channel", default="")
    record.add_argument("--patient-stage", default="")
    record.add_argument("--concern", default="")
    record.add_argument("--voice-scope", choices=("generic", "personal", "institution"), default="generic")
    record.add_argument("--knowledge-ref", action="append", default=[])
    record.add_argument("--evidence-ref", action="append", default=[])
    record.add_argument("--replied", choices=OUTCOME_VALUES, default="unknown")
    record.add_argument("--reply-quality", choices=REPLY_QUALITIES, default="unknown",
                        help="positive means the reply advances understanding or the next service step")
    record.add_argument("--appointed", choices=OUTCOME_VALUES, default="unknown")
    record.add_argument("--arrived", choices=OUTCOME_VALUES, default="unknown")
    record.add_argument("--note", default="")

    review = subparsers.add_parser("review", help="manager review for a content candidate")
    review.add_argument("workspace_root")
    review.add_argument("--asset-id", required=True)
    review.add_argument("--decision", choices=REVIEW_DECISIONS, required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--evidence-ref", action="append", default=[])
    review.add_argument("--note", default="")
    review.add_argument("--expires-at", default="", help="optional YYYY-MM-DD expiry for time-bound content")

    status = subparsers.add_parser("status", help="show current content asset state")
    status.add_argument("workspace_root")
    status.add_argument("--asset-id", default="")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "record":
        result, code = cmd_record(args)
    elif args.command == "review":
        result, code = cmd_review(args)
    elif args.command == "status":
        result, code = cmd_status(args)
    else:
        parser.print_help()
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
