#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inventory consultation materials across a workspace.

This is the deterministic front door for first-run full distillation and later
incremental updates. It never moves or edits source files. Derived transcripts
and OCR text are paired through --derived-dir and tracked by source hash.
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path

from compat import ensure_dir, expand_path


AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".amr"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
CHAT_EXTS = {".html", ".htm"}
TEXT_EXTS = {".txt", ".md", ".json", ".jsonl", ".csv"}
DOC_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
SUPPORTED_EXTS = AUDIO_EXTS | IMAGE_EXTS | CHAT_EXTS | TEXT_EXTS | DOC_EXTS

PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WECHAT = re.compile(r"(微信号|微信|wxid)\s*[:：]?\s*[A-Za-z][A-Za-z0-9_-]{5,}", re.I)

IGNORED_PARTS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
    "_系统", "output", "缓存", "cache", "tmp", "temp",
}
GENERATED_OUTPUT_PARTS = {
    "07_我的产出", "04_团队报告", "03_个人报告", "04_团队自动化",
}
SIGNAL_WORDS = (
    "咨询", "录音", "到院", "未到", "未预约", "爽约", "优秀", "销冠",
    "微信", "聊天", "患者", "客资", "痛风", "银屑", "白癜", "科室",
    "病种", "复盘", "回访", "话术", "机构", "医院", "知识库", "流程",
    "标准", "价格", "医生", "项目", "治疗",
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def redact(value):
    value = PHONE.sub("[手机号]", value)
    value = ID_CARD.sub("[证件号]", value)
    value = EMAIL.sub("[邮箱]", value)
    return WECHAT.sub("[微信号]", value)


def load_json(path, default):
    if not path.is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def load_jsonl(path):
    rows = []
    if not path.is_file():
        return rows
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except IOError:
        return []
    return rows


def relative(path, root):
    return str(path.relative_to(root)).replace(os.sep, "/")


def is_ignored(path, root):
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    if any(part in IGNORED_PARTS for part in parts):
        return True
    # Generated reports are not source evidence. Keep raw material folders in
    # 08_团队管理 and 01-06, but never feed prior reports back into the loop.
    if "咨询转化工作区" in parts and any(part in GENERATED_OUTPUT_PARTS for part in parts):
        return True
    if path.name.startswith("~") or path.suffix.lower() in {".tmp", ".part", ".crdownload"}:
        return True
    return False


def has_signal(path, root):
    value = (relative(path, root) + " " + path.name).lower()
    return any(word.lower() in value for word in SIGNAL_WORDS)


def material_type(path):
    suffix = path.suffix.lower()
    if suffix in AUDIO_EXTS:
        return "audio"
    if suffix in IMAGE_EXTS:
        return "chat_image_or_attachment"
    if suffix in CHAT_EXTS:
        return "wechat_html_or_chat_export"
    if suffix in DOC_EXTS:
        return "document_or_data"
    return "text_or_structured_text"


def scope_for(path, root):
    parts = path.relative_to(root).parts
    if "咨询转化工作区" in parts:
        return "consultation_material"
    if has_signal(path, root):
        return "consultation_or_institution_material"
    return "other_supported_document"


def collect_files(root, output_dir):
    rows = []
    output_dir = output_dir.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in SUPPORTED_EXTS:
            continue
        if is_ignored(path, root):
            continue
        if path.resolve() == output_dir or output_dir in path.resolve().parents:
            continue
        try:
            stat = path.stat()
            digest = sha256(path)
        except (IOError, OSError) as exc:
            rows.append({
                "source_id": "unreadable-" + hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12],
                "source_path": relative(path, root),
                "display_path": redact(relative(path, root)),
                "status": "unreadable",
                "error": str(exc),
            })
            continue
        rows.append({
            "source_id": "src-" + digest[:16],
            "source_hash": digest,
            "source_path": relative(path, root),
            "display_path": redact(relative(path, root)),
            "filename": redact(path.name),
            "extension": path.suffix.lower(),
            "material_type": material_type(path),
            "scope": scope_for(path, root),
            "size_bytes": stat.st_size,
            "modified_at": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return rows


def derived_files(derived_dirs):
    result = {}
    for directory in derived_dirs:
        if not directory.exists():
            continue
        for path in directory.rglob("*.txt"):
            if path.is_file() and path.stat().st_size > 0:
                result.setdefault(path.stem.lower(), []).append(str(path))
    return result


def pair_derived(row, source_path, derived):
    stem = source_path.stem.lower()
    matches = list(derived.get(stem, []))
    # A same-stem sidecar is also a valid transcript/OCR result.
    sidecar = source_path.with_suffix(".txt")
    if sidecar.is_file() and sidecar.stat().st_size > 0:
        matches.append(str(sidecar))
    if not matches:
        return row
    row["derived_text_paths"] = sorted(set(matches))
    row["derived_status"] = "available"
    return row


def processing_status(row):
    if row.get("status") == "unreadable":
        return "failed_to_inventory"
    if row.get("scope") == "other_supported_document":
        return "review_scope"
    kind = row.get("material_type")
    if kind == "audio":
        return "ready_for_standardization" if row.get("derived_status") == "available" else "pending_transcription"
    if kind == "chat_image_or_attachment":
        return "ready_for_standardization" if row.get("derived_status") == "available" else "pending_ocr"
    if kind == "document_or_data":
        return "pending_document_extraction"
    return "ready_for_standardization"


def build_report(rows, mode, root, run_at):
    candidates = [row for row in rows if row.get("scope") not in ("other_supported_document", "derived_text_sidecar") and row.get("status") != "unreadable"]
    counts = {}
    for row in candidates:
        status = row.get("processing_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    pending = sum(counts.get(key, 0) for key in (
        "pending_transcription", "pending_ocr", "pending_document_extraction"))
    failed = counts.get("failed_to_inventory", 0)
    ready = counts.get("ready_for_standardization", 0)
    full_ready = pending == 0 and failed == 0 and len(candidates) > 0
    return {
        "run_at": run_at,
        "workspace_root": str(root),
        "mode": mode,
        "candidate_total": len(candidates),
        "supported_but_out_of_scope": sum(1 for row in rows if row.get("scope") in ("other_supported_document", "derived_text_sidecar")),
        "status_counts": counts,
        "ready_for_standardization": ready,
        "pending": pending,
        "failed": failed,
        "full_processing_ready": full_ready,
        "gate": "ready_for_agent_distillation" if full_ready else "partial_ready_pending_processing",
        "rule": "Never label a partial batch as a full institutional champion package.",
    }


def main():
    parser = argparse.ArgumentParser(description="Inventory all eligible consultation materials in a workspace.")
    parser.add_argument("workspace_root")
    parser.add_argument("--output-dir", help="backend index directory; defaults to 咨询转化工作区/_系统/资料索引")
    parser.add_argument("--derived-dir", action="append", default=[], help="directory containing transcripts/OCR text; repeatable")
    parser.add_argument("--mode", choices=("auto", "full", "incremental"), default="auto")
    args = parser.parse_args()

    root = expand_path(args.workspace_root)
    if not root.is_dir():
        print("ERROR: workspace root is not a directory: {0}".format(root), file=sys.stderr)
        return 2
    output = expand_path(args.output_dir) if args.output_dir else root / "咨询转化工作区" / "_系统" / "资料索引"
    ensure_dir(output)
    state_path = output / "distillation_state.json"
    state = load_json(state_path, {})
    if args.mode == "auto":
        mode = "incremental" if state.get("processing_complete") else "first_full"
    else:
        mode = "first_full" if args.mode == "full" else "incremental"

    previous_path = output / "workspace-inventory.jsonl"
    previous = {row.get("source_hash"): row for row in load_jsonl(previous_path) if row.get("source_hash")}
    rows = collect_files(root, output)
    source_by_parent_and_stem = {}
    for row in rows:
        if row.get("material_type") in ("audio", "chat_image_or_attachment"):
            source_path = root / row["source_path"]
            source_by_parent_and_stem[(source_path.parent, source_path.stem.lower())] = row
    for row in rows:
        if row.get("material_type") != "text_or_structured_text":
            continue
        source_path = root / row["source_path"]
        paired_source = source_by_parent_and_stem.get((source_path.parent, source_path.stem.lower()))
        if paired_source:
            row["scope"] = "derived_text_sidecar"
            row["derived_for_source_id"] = paired_source.get("source_id")
    derived = derived_files([expand_path(value) for value in args.derived_dir])
    for row in rows:
        source_path = root / row["source_path"]
        if row.get("status") != "unreadable":
            pair_derived(row, source_path, derived)
            row["processing_status"] = "derived_text_sidecar" if row.get("scope") == "derived_text_sidecar" else processing_status(row)
            old = previous.get(row.get("source_hash"))
            row["change_status"] = "unchanged" if old else "new"
        else:
            row["processing_status"] = "failed_to_inventory"
            row["change_status"] = "new"
        if row.get("scope") == "derived_text_sidecar":
            row["queue_status"] = "derived_sidecar"
        elif mode == "incremental" and row.get("change_status") == "unchanged":
            row["queue_status"] = "skip_unchanged"
        else:
            row["queue_status"] = "queue_for_processing"

    run_at = datetime.datetime.now().isoformat()
    report = build_report(rows, mode, root, run_at)
    report["new_or_changed"] = sum(1 for row in rows if row.get("queue_status") == "queue_for_processing")
    report["unchanged"] = sum(1 for row in rows if row.get("change_status") == "unchanged")
    report["derived_dirs"] = [str(expand_path(value)) for value in args.derived_dir]

    inventory_path = output / "workspace-inventory.jsonl"
    with io.open(str(inventory_path), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with io.open(str(output / "coverage-report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with io.open(str(output / "coverage-report.md"), "w", encoding="utf-8") as handle:
        handle.write("# 资料处理覆盖率\n\n")
        handle.write("- 扫描模式：`{0}`\n".format(mode))
        handle.write("- 资料候选：{0}\n".format(report["candidate_total"]))
        handle.write("- 可进入标准化：{0}\n".format(report["ready_for_standardization"]))
        handle.write("- 待处理：{0}\n".format(report["pending"]))
        handle.write("- 失败：{0}\n".format(report["failed"]))
        handle.write("- 新增或变化：{0}\n".format(report["new_or_changed"]))
        handle.write("- 未变化：{0}\n".format(report["unchanged"]))
        handle.write("- 闸门：**{0}**\n\n".format(report["gate"]))
        handle.write("只有所有候选资料完成转写、OCR或文档提取后，才能生成“全量销冠能力包”；否则只能输出部分样本候选分析。\n")

    state.update({
        "last_scan_at": run_at,
        "last_mode": mode,
        "inventory_path": str(inventory_path),
        "coverage_path": str(output / "coverage-report.json"),
        "processing_complete": bool(report["full_processing_ready"]),
    })
    with io.open(str(state_path), "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(dict(report, inventory=str(inventory_path)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
