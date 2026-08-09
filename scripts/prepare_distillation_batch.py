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
    """Infer routing metadata without excluding any readable material.

    Folder names are hints only. They never establish a clinical or business
    outcome. The model must still inspect the actual transcript and cite
    evidence before treating a label as confirmed.
    """
    value = str(path).lower()
    outcome = "待确认"
    outcome_provenance = "unknown"
    if any(x in value for x in ["未到", "未约", "爽约", "未预约", "流失", "失联"]):
        outcome = "未到/未预约（路径标签，待确认）"
        outcome_provenance = "folder_or_filename_label_only"
    elif any(x in value for x in ["已到", "已约", "到院", "成交", "预约成功"]):
        outcome = "已约/已到（路径标签，待确认）"
        outcome_provenance = "folder_or_filename_label_only"

    training_signals = [
        "培训", "内训", "会议", "策略", "复盘会", "课件", "课堂", "直播",
        "方法论", "手册", "sop", "讲解", "产品打磨", "商务", "会议录音",
    ]
    positive_signals = ["优秀", "销冠", "红板", "好录音", "正向", "成功", "已到", "到院", "成交", "预约成功"]
    negative_signals = ["失败", "黑板", "未到", "未约", "爽约", "流失", "失联", "投诉", "拒绝"]
    comparison_signals = ["普通", "对照", "一般", "待改进", "复盘"]
    patient_signals = ["患者", "咨询", "电话", "微信", "私信", "录音", "聊天", "对话", "线索"]

    is_training = any(x in value for x in training_signals)
    is_patient = any(x in value for x in patient_signals)
    if is_training:
        source_nature = "team_training_or_strategy"
        sample_role = "methodology_reference"
        analysis_route = "methodology_and_management"
        result_weight = "context_only"
        sample_weight = "context_only"
    elif any(x in value for x in positive_signals):
        source_nature = "patient_consultation" if is_patient else "unknown_material"
        sample_role = "positive_reference"
        analysis_route = "patient_case"
        result_weight = "medium" if outcome_provenance != "unknown" else "low"
        sample_weight = "high"
    elif any(x in value for x in negative_signals):
        source_nature = "patient_consultation" if is_patient else "unknown_material"
        sample_role = "negative_reference"
        analysis_route = "patient_case"
        result_weight = "medium" if outcome_provenance != "unknown" else "low"
        sample_weight = "high"
    elif any(x in value for x in comparison_signals):
        source_nature = "patient_consultation" if is_patient else "unknown_material"
        sample_role = "comparison_case"
        analysis_route = "patient_case"
        result_weight = "medium"
        sample_weight = "medium"
    elif is_patient:
        source_nature = "patient_consultation"
        sample_role = "unknown_case"
        analysis_route = "patient_case"
        result_weight = "low"
        sample_weight = "low"
    else:
        source_nature = "unknown_material"
        sample_role = "unknown_case"
        analysis_route = "general_material_review"
        result_weight = "low"
        sample_weight = "low"

    medium = "OCR文本" if "ocr" in value or "截图" in value else "转写文本"
    return medium, outcome, outcome_provenance, source_nature, sample_role, result_weight, sample_weight, analysis_route


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
        medium, outcome, outcome_provenance, source_nature, sample_role, result_weight, sample_weight, analysis_route = infer(source)
        safe = redact(raw)
        truncated = len(safe) > args.max_chars
        if truncated:
            safe = safe[:args.max_chars] + "\n[文本已截断，需回到原始证据继续核对]"
        rel = str(source.relative_to(root))
        source_id = "batch-" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]
        rows.append({
            "case_id": source_id,
            "source_id": source_id,
            "source_nature": source_nature,
            "sample_role": sample_role,
            "analysis_route": analysis_route,
            "result_weight": result_weight,
            "sample_weight": sample_weight,
            "source_hash": sha256(source),
            "source_path": rel,
            "medium": medium,
            "outcome": outcome,
            "outcome_provenance": outcome_provenance,
            "text": safe,
            "redaction_status": "automatic_pattern_redaction_review_required",
            "review_status": "human_review_required",
            "inclusion_status": "included_pending_review",
            "split": "candidate",
            "truncated": truncated,
        })
    with io.open(str(output), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"cases": len(rows), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
