#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepare a redacted, evidence-linked batch manifest for Skill distillation."""

import argparse
import hashlib
import io
import json
import os
import re
from pathlib import Path

from compat import ensure_dir, expand_path


PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WECHAT = re.compile(r"(微信号|微信|wxid)\s*[:：]?\s*[A-Za-z][A-Za-z0-9_-]{5,}", re.I)


def redact(text):
    text = PHONE.sub("[手机号已脱敏]", text)
    text = ID_CARD.sub("[证件号已脱敏]", text)
    text = EMAIL.sub("[邮箱已脱敏]", text)
    text = WECHAT.sub("[微信号已脱敏]", text)
    return text


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def infer(path):
    value = str(path).lower()
    outcome = "待确认"
    if any(x in value for x in ["未到", "未约", "爽约", "未预约"]):
        outcome = "未到/未预约（路径标签，待确认）"
    elif any(x in value for x in ["已到", "已约", "到院"]):
        outcome = "已约/已到（路径标签，待确认）"
    medium = "OCR文本" if "ocr" in value or "截图" in value else "转写文本"
    return medium, outcome


def main():
    parser = argparse.ArgumentParser(description="Build a batch manifest from transcripts and OCR text.")
    parser.add_argument("input", help="workspace or directory containing txt files")
    parser.add_argument("--output", required=True, help="distillation-batch.jsonl path")
    parser.add_argument("--max-chars", type=int, default=30000)
    args = parser.parse_args()
    root = expand_path(args.input)
    output = expand_path(args.output)
    ensure_dir(output.parent)
    rows = []
    for source in sorted(root.rglob("*.txt")):
        if source.resolve() == output:
            continue
        with io.open(str(source), "r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read()
        medium, outcome = infer(source)
        safe = redact(raw)
        truncated = len(safe) > args.max_chars
        if truncated:
            safe = safe[:args.max_chars] + "\n[文本已截断，需回到原始证据继续核对]"
        rel = str(source.relative_to(root))
        source_id = "batch-" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]
        rows.append({
            "case_id": source_id,
            "source_id": source_id,
            "source_nature": "real_material_pending_review",
            "source_hash": sha256(source),
            "source_path": rel,
            "medium": medium,
            "outcome": outcome,
            "outcome_provenance": "folder_or_filename_label_only",
            "text": safe,
            "redaction_status": "automatic_pattern_redaction_review_required",
            "review_status": "human_review_required",
            "split": "candidate",
            "truncated": truncated,
        })
    with io.open(str(output), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"cases": len(rows), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
