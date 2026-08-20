#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maintain a quota-aware IMA material manifest and retrieval queue.

The IMA API adapter is intentionally kept outside this package. It provides
the JSON listing, while this script owns deterministic prioritisation,
checkpointing and quota state. No API key is read or stored here.
"""

import argparse
import datetime
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

from compat import ensure_dir, expand_path
from workspace_paths import assert_within, locate_workspace

try:
    import fcntl
except ImportError:
    fcntl = None


POSITIVE = ("已到", "已约", "预约成功", "到院", "成交", "微现到", "微跟到", "优秀", "销冠", "红板")
NEGATIVE = ("未到", "未约", "未预约", "爽约", "流失", "失联", "失败", "黑板", "投诉")
METHOD = ("培训", "内训", "会议", "策略", "复盘", "课件", "方法", "SOP")
CONCERN = ("费用", "价格", "效果", "信任", "距离", "家人", "爽约", "回访")
CHANNEL = ("抖音", "快手", "竞价", "大数据", "全媒体", "私信", "微信", "转介绍", "医生IP", "矩阵")
QUOTA_TEXT = ("获取次数已达上限", "资料获取次数", "请求频率超限", "rate limit", "quota")

STATUSES = {
    "discovered", "queued", "retrieval_pending", "retrieved", "transcribed_or_ocr",
    "standardized", "distilled", "quota_blocked", "permission_failed", "quality_insufficient",
    "failed", "excluded",
}


def now():
    return datetime.datetime.now().isoformat()


def paths(workspace):
    root = locate_workspace(workspace) / "_系统" / "IMA同步"
    return {
        "root": root,
        "manifest": root / "ima-manifest.jsonl",
        "state": root / "retrieval-state.json",
        "events": root / "quota-events.jsonl",
        "cache": root / "cache-index.jsonl",
        "queue": root / "retrieval-queue.jsonl",
    }


def load_json(path, default):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def load_jsonl(path):
    rows = []
    path = Path(path)
    if not path.is_file():
        return rows
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except IOError:
        return rows
    return rows


def write_jsonl(path, rows):
    path = Path(path)
    ensure_dir(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".ima-", suffix=".jsonl", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path)) if hasattr(os, "replace") else os.rename(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_jsonl(path, row):
    path = Path(path)
    ensure_dir(path.parent)
    with io.open(str(path), "a", encoding="utf-8") as handle:
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_input(path):
    with io.open(str(expand_path(path)), "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if isinstance(value, list):
        return value
    data = value.get("data", value) if isinstance(value, dict) else {}
    for key in ("knowledge_list", "info_list", "items", "results"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    if isinstance(data, list):
        return data
    return []


def classify(title):
    text = str(title or "")
    lower = text.lower()
    positive = any(token.lower() in lower for token in POSITIVE)
    negative = any(token.lower() in lower for token in NEGATIVE)
    method = any(token.lower() in lower for token in METHOD)
    if positive and not negative:
        return "positive_reference", "路径标签线索"
    if negative and not positive:
        return "negative_reference", "路径标签线索"
    if method:
        return "methodology_reference", "文件名/文件夹线索"
    if positive and negative:
        return "unknown_case", "标签冲突待确认"
    return "unknown_case", "未提供结果证据"


def score(row):
    role = row.get("sample_role")
    value = {"positive_reference": 60, "negative_reference": 48,
             "comparison_case": 42, "unknown_case": 24,
             "methodology_reference": 12}.get(role, 20)
    title = str(row.get("title") or "")
    value += min(len(title) // 20, 8)
    value += min(sum(1 for token in CHANNEL if token.lower() in title.lower()), 2) * 4
    value += min(sum(1 for token in CONCERN if token.lower() in title.lower()), 2) * 3
    return value


def source_id(media_id, knowledge_base, title):
    raw = "|".join((str(knowledge_base or ""), str(media_id or ""), str(title or "")))
    return "ima-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def normalise_item(item, knowledge_base, source_label):
    media_type = item.get("media_type", item.get("type"))
    # IMA folders are listed beside media items. Keep folders out of the
    # retrieval queue; they remain visible in the source metadata.
    if str(media_type) in ("99", "folder") or str(item.get("media_id", "")).startswith("folder_"):
        return None
    media_id = item.get("media_id") or item.get("id")
    title = item.get("title") or item.get("name") or "未命名资料"
    if not media_id:
        return None
    role, provenance = classify(title)
    row = {
        "source_id": source_id(media_id, knowledge_base, title),
        "source": "IMA",
        "knowledge_base": knowledge_base,
        "source_label": source_label or knowledge_base,
        "media_id": str(media_id),
        "folder_id": item.get("parent_folder_id") or item.get("folder_id"),
        "title": str(title),
        "media_type": media_type,
        "sample_role": role,
        "outcome_provenance": provenance,
        "priority_score": 0,
        "status": "discovered",
        "retrieval_attempts": 0,
        "created_at": now(),
        "updated_at": now(),
    }
    row["priority_score"] = score(row)
    return row


def merge_inventory(existing, incoming):
    by_id = {row.get("source_id"): dict(row) for row in existing if row.get("source_id")}
    for row in incoming:
        old = by_id.get(row["source_id"])
        if old:
            for key in ("knowledge_base", "source_label", "folder_id", "title", "media_type", "sample_role", "outcome_provenance", "priority_score"):
                if row.get(key) is not None:
                    old[key] = row[key]
            old["updated_at"] = now()
        else:
            by_id[row["source_id"]] = row
    return sorted(by_id.values(), key=lambda row: (row.get("priority_score", 0) * -1, row.get("source_id", "")))


def command_inventory(args):
    target = paths(args.workspace_root)
    incoming = []
    for item in read_input(args.input):
        row = normalise_item(item, args.knowledge_base, args.source_label)
        if row:
            incoming.append(row)
    rows = merge_inventory(load_jsonl(target["manifest"]), incoming)
    write_jsonl(target["manifest"], rows)
    state = load_json(target["state"], {})
    state.update({"schema_version": "1.0", "source": "IMA", "knowledge_base": args.knowledge_base,
                  "last_inventory_at": now(), "last_inventory_count": len(incoming)})
    ensure_dir(target["state"].parent)
    with io.open(str(target["state"]), "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"status": "inventoried", "added_or_seen": len(incoming), "total": len(rows),
                      "manifest": str(target["manifest"])}, ensure_ascii=False))


def bucket_rows(rows):
    buckets = {"positive_reference": [], "negative_reference": [], "other": []}
    for row in rows:
        role = row.get("sample_role")
        if role in ("positive_reference",):
            buckets["positive_reference"].append(row)
        elif role in ("negative_reference", "comparison_case"):
            buckets["negative_reference"].append(row)
        else:
            buckets["other"].append(row)
    for value in buckets.values():
        value.sort(key=lambda row: (-row.get("priority_score", 0), row.get("source_id", "")))
    return buckets


def command_queue(args):
    target = paths(args.workspace_root)
    rows = load_jsonl(target["manifest"])
    eligible = [row for row in rows if row.get("status") in ("discovered", "quota_blocked", "failed")]
    buckets = bucket_rows(eligible)
    limit = max(1, args.limit)
    quotas = {"positive_reference": int(math.ceil(limit * 0.50)),
              "negative_reference": int(math.ceil(limit * 0.30))}
    selected = []
    used = set()
    for name in ("positive_reference", "negative_reference"):
        for row in buckets[name][:quotas[name]]:
            selected.append(row)
            used.add(row.get("source_id"))
    for row in buckets["other"]:
        if len(selected) >= limit:
            break
        selected.append(row)
        used.add(row.get("source_id"))
    for name in ("positive_reference", "negative_reference"):
        for row in buckets[name]:
            if len(selected) >= limit:
                break
            if row.get("source_id") not in used:
                selected.append(row)
                used.add(row.get("source_id"))
    selected = selected[:limit]
    selected_ids = {row.get("source_id") for row in selected}
    for row in rows:
        if row.get("source_id") in selected_ids:
            row["status"] = "queued"
            row["queued_at"] = now()
            row["updated_at"] = now()
    write_jsonl(target["manifest"], rows)
    write_jsonl(target["queue"], selected)
    print(json.dumps({"status": "queued", "selected": len(selected),
                      "queue": str(target["queue"]),
                      "roles": {name: sum(1 for row in selected if row.get("sample_role") == name)
                                for name in ("positive_reference", "negative_reference", "unknown_case", "methodology_reference")}},
                     ensure_ascii=False))


def command_record(args):
    target = paths(args.workspace_root)
    workspace = locate_workspace(args.workspace_root)
    rows = load_jsonl(target["manifest"])
    found = None
    for row in rows:
        if row.get("source_id") == args.source_id or row.get("media_id") == args.media_id:
            found = row
            break
    if found is None:
        print(json.dumps({"status": "not_found"}, ensure_ascii=False))
        return 2
    status = "quota_blocked" if args.quota_error else args.status
    if status not in STATUSES:
        print(json.dumps({"status": "invalid_status", "allowed": sorted(STATUSES)}, ensure_ascii=False))
        return 2
    found.update({"status": status, "updated_at": now()})
    found["retrieval_attempts"] = int(found.get("retrieval_attempts", 0)) + 1
    if args.error:
        found["last_error"] = args.error
    if args.derived_path:
        derived = assert_within(args.derived_path, workspace, "IMA derived_path")
        if not derived.is_file():
            raise ValueError("IMA derived_path does not exist: {0}".format(derived))
        found["derived_path"] = str(derived)
    if args.cache_path:
        cache_path = assert_within(args.cache_path, workspace, "IMA cache_path")
        if not cache_path.is_file():
            raise ValueError("IMA cache_path does not exist: {0}".format(cache_path))
        found["cache_path"] = str(cache_path)
        found["content_hash"] = file_sha256(cache_path)
    if args.quality:
        found["quality"] = args.quality
    write_jsonl(target["manifest"], rows)
    event = {"event_id": "ima-event-" + hashlib.sha1((found["source_id"] + now()).encode("utf-8")).hexdigest()[:12],
             "created_at": now(), "source_id": found["source_id"], "media_id": found.get("media_id"),
             "status": status, "error": args.error or ""}
    if args.quota_error or (args.error and any(text.lower() in args.error.lower() for text in QUOTA_TEXT)):
        append_jsonl(target["events"], event)
    if args.cache_path:
        append_jsonl(target["cache"], {"source_id": found["source_id"], "media_id": found.get("media_id"),
                                       "cache_path": found["cache_path"],
                                       "content_hash": found["content_hash"], "created_at": now(),
                                       "mode": args.cache_mode})
    print(json.dumps({"status": "recorded", "source_id": found["source_id"], "material_status": status}, ensure_ascii=False))


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def command_status(args):
    target = paths(args.workspace_root)
    rows = load_jsonl(target["manifest"])
    counts = {}
    for row in rows:
        counts[row.get("status", "unknown")] = counts.get(row.get("status", "unknown"), 0) + 1
    print(json.dumps({"status": "ok", "total": len(rows), "status_counts": counts,
                      "manifest": str(target["manifest"])}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Quota-aware IMA inventory and retrieval queue")
    sub = parser.add_subparsers(dest="command")
    inventory = sub.add_parser("inventory", help="merge an IMA listing response into the manifest")
    inventory.add_argument("workspace_root")
    inventory.add_argument("--input", required=True, help="JSON file containing IMA listing response")
    inventory.add_argument("--knowledge-base", required=True)
    inventory.add_argument("--source-label", default="")
    inventory.set_defaults(handler=command_inventory)
    queue = sub.add_parser("queue", help="select a quota-aware retrieval batch")
    queue.add_argument("workspace_root")
    queue.add_argument("--limit", type=int, default=20)
    queue.set_defaults(handler=command_queue)
    record = sub.add_parser("record", help="record one retrieval result")
    record.add_argument("workspace_root")
    identity = record.add_mutually_exclusive_group(required=True)
    identity.add_argument("--source-id")
    identity.add_argument("--media-id")
    record.add_argument("--status", default="retrieved")
    record.add_argument("--quota-error", action="store_true")
    record.add_argument("--error", default="")
    record.add_argument("--derived-path", default="")
    record.add_argument("--cache-path", default="")
    record.add_argument("--cache-mode", choices=("online", "controlled"), default="controlled")
    record.add_argument("--quality", default="")
    record.set_defaults(handler=command_record)
    status = sub.add_parser("status", help="show manifest status counts")
    status.add_argument("workspace_root")
    status.set_defaults(handler=command_status)
    args = parser.parse_args()
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    if args.command == "status" or not fcntl:
        return args.handler(args) or 0
    target = paths(args.workspace_root)
    ensure_dir(target["root"])
    with io.open(str(target["root"] / ".sync.lock"), "a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return args.handler(args) or 0
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    sys.exit(main())
