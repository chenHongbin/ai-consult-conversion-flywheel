#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepare a redacted, evidence-linked batch manifest for Skill distillation."""

import argparse
import hashlib
import io
import json
import os
import re
import unicodedata
from pathlib import Path

from compat import ensure_dir, expand_path


PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WECHAT = re.compile(r"(微信号|微信|wxid)\s*[:：]?\s*[A-Za-z][A-Za-z0-9_-]{5,}", re.I)

DERIVED_PARTS = {
    "转写与OCR", "案例标准化", "资料索引", "蒸馏任务", "蒸馏候选",
    "患者洞察候选", "当前能力包", "当前机构知识", "机构知识候选",
}


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


def normalized_text(text):
    value = unicodedata.normalize("NFKC", text or "")
    value = redact(value).lower()
    value = re.sub(r"\s+", "", value)
    return value


def is_derived(source, root):
    try:
        relative = source.relative_to(root)
    except ValueError:
        return False
    parts = set(relative.parts)
    return "_系统" in parts and bool(parts.intersection(DERIVED_PARTS))


def derived_source_map(input_root):
    """Map derived text back to its original source and quality metadata.

    OCR, YouNavi transcripts and document extraction all use a manifest. The
    batch must preserve that link so a model can cite the original recording,
    image or document instead of treating a derived text file as the source.
    """
    mapping = {}
    manifests = []
    for name, key in (
        ("ocr_manifest.jsonl", "text"),
        ("transcript_manifest.jsonl", "transcript"),
        ("extraction_manifest.jsonl", "text"),
    ):
        # Depending on the input directory, a manifest lives either beside
        # the derived files (transcripts/documents) or one directory above
        # them (OCR text is nested under OCR/text).
        for candidate in (input_root / name, input_root.parent / name):
            if candidate not in [item[0] for item in manifests]:
                manifests.append((candidate, key))
    for manifest, derived_key in manifests:
        if not manifest.is_file():
            continue
        try:
            with io.open(str(manifest), "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    derived = row.get(derived_key)
                    if derived and row.get("source"):
                        mapping[str(Path(derived).resolve())] = row
        except IOError:
            continue
    return mapping


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
    parser.add_argument("inputs", nargs="+", help="workspace or directories containing txt files")
    parser.add_argument("--output", required=True, help="distillation-batch.jsonl path")
    parser.add_argument("--max-chars", type=int, default=30000)
    args = parser.parse_args()
    input_roots = [expand_path(value) for value in args.inputs]
    output = expand_path(args.output)
    ensure_dir(output.parent)
    rows = []
    clusters = {}
    for input_root in input_roots:
        if not input_root.is_dir():
            continue
        source_map = derived_source_map(input_root)
        for source in sorted(input_root.rglob("*.txt")):
            if source.resolve() == output:
                continue
            if is_derived(source, input_root):
                continue
            with io.open(str(source), "r", encoding="utf-8", errors="replace") as handle:
                raw = handle.read()
            # An OCR engine can return success with an empty text file. Keep
            # that evidence in the OCR manifest for coverage, but do not put
            # an empty case into the language-model distillation batch.
            if not raw.strip():
                continue
            safe = redact(raw)
            truncated = len(safe) > args.max_chars
            if truncated:
                safe = safe[:args.max_chars] + "\n[文本已截断，需回到原始证据继续核对]"
            relative_value = str(source.relative_to(input_root))
            rel = relative_value if len(input_roots) == 1 else str(input_root.name + "/" + relative_value)
            source_id = "batch-" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12]
            derived_meta = source_map.get(str(source.resolve())) or {}
            original_source = derived_meta.get("source") if isinstance(derived_meta, dict) else derived_meta
            inference_path = Path(original_source) if original_source else source
            medium, outcome, outcome_provenance, source_nature, sample_role, result_weight, sample_weight, analysis_route = infer(inference_path)
            quality = derived_meta.get("ocr_quality") if isinstance(derived_meta, dict) else None
            if not quality and isinstance(derived_meta, dict):
                quality = "unknown" if derived_meta.get("status") in ("failed", "slice_failed", "ocr_failed") else "processed"
            quality = quality or ("unknown" if original_source else "text")
            weight = {"high": 1.0, "medium": 0.6, "low": 0.2, "unknown": 0.5, "text": 0.7}.get(quality, 0.5)
            normalized_hash = hashlib.sha1(normalized_text(raw).encode("utf-8")).hexdigest()[:16]
            dedup_cluster_id = "dedup-" + normalized_hash
            duplicate_index = len(clusters.get(dedup_cluster_id, []))
            clusters.setdefault(dedup_cluster_id, []).append(source_id)
            rows.append({
            "case_id": source_id,
            "source_id": source_id,
            "source_nature": source_nature,
            "sample_role": sample_role,
            "analysis_route": analysis_route,
            "result_weight": result_weight,
            "sample_weight": sample_weight,
            "source_hash": sha256(source),
            "dedup_cluster_id": dedup_cluster_id,
            "duplicate_index": duplicate_index,
            "independent_case": duplicate_index == 0,
            "episode_id": "episode-" + normalized_hash,
            "source_path": rel,
            "derived_from_source": original_source,
            "medium": medium,
            "outcome": outcome,
            "outcome_provenance": outcome_provenance,
            "text": safe,
            "redaction_status": "automatic_pattern_redaction",
            "consent_status": "unknown",
            "privacy_risk": "automatic_screening_required",
            "transcript_quality": quality,
            "evidence_weight": weight,
            "ocr_quality_score": derived_meta.get("ocr_quality_score") if isinstance(derived_meta, dict) else None,
            "derived_engine": derived_meta.get("engine") or derived_meta.get("ocr_engine") if isinstance(derived_meta, dict) else None,
            "derived_status": derived_meta.get("status") if isinstance(derived_meta, dict) else None,
            "label_confidence": "unknown",
            "behavior_confidence": "unknown",
            "outcome_confidence": "unknown",
            "review_status": "auto_quarantined" if quality == "low" else "auto_processed",
            "inclusion_status": "included_with_weight" if quality != "low" else "stored_not_used_as_sole_evidence",
                "split": "candidate",
                "truncated": truncated,
            })
    with io.open(str(output), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"cases": len(rows), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
