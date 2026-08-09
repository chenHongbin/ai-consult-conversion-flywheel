#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrate first-run full processing and later incremental processing.

This script prepares the complete evidence set. The language-model distillation
itself remains an Agent step after the coverage gate says it is ready.
"""

import argparse
import datetime
import io
import json
import os
import subprocess
import sys
from pathlib import Path

from compat import ensure_dir, expand_path


SCRIPT_DIR = Path(__file__).resolve().parent


def run(command):
    print("RUN " + " ".join(str(value) for value in command))
    return subprocess.call([str(value) for value in command])


def read_json(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def inventory(root, index_dir, derived_dirs, mode):
    command = [sys.executable, SCRIPT_DIR / "inventory_workspace.py", root,
               "--output-dir", index_dir, "--mode", mode]
    for directory in derived_dirs:
        command.extend(["--derived-dir", directory])
    return run(command)


def main():
    parser = argparse.ArgumentParser(description="Run the full evidence preparation pipeline for AI咨询转化飞轮.")
    parser.add_argument("workspace_root")
    parser.add_argument("--mode", choices=("auto", "full", "incremental"), default="auto")
    parser.add_argument("--run-transcription", action="store_true", help="invoke YouNavi for all pending audio")
    parser.add_argument("--run-ocr", action="store_true", help="slice and OCR all pending images with Tesseract")
    parser.add_argument("--skip-extract", action="store_true", help="skip HTML/PDF/Office text extraction")
    parser.add_argument("--agent", help="YouNavi agent-cli path")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    root = expand_path(args.workspace_root)
    if not root.is_dir():
        print("ERROR: workspace root is not a directory: {0}".format(root), file=sys.stderr)
        return 2
    index_dir = root / "咨询转化工作区" / "_系统" / "资料索引"
    processing_dir = root / "咨询转化工作区" / "_系统" / "转写与OCR"
    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = root / "咨询转化工作区" / "_系统" / "蒸馏任务" / run_id
    transcript_dir = processing_dir / "转写"
    ocr_dir = processing_dir / "OCR"
    document_dir = processing_dir / "文档文本"
    ensure_dir(run_dir)
    ensure_dir(transcript_dir)
    ensure_dir(ocr_dir)
    ensure_dir(document_dir)

    # Keep derived paths stable so later incremental runs can skip unchanged
    # transcripts and OCR results.
    derived_dirs = [transcript_dir, ocr_dir / "text", document_dir]
    if inventory(root, index_dir, derived_dirs, args.mode) != 0:
        return 1

    if args.run_transcription:
        command = [sys.executable, SCRIPT_DIR / "batch_transcribe_younavi.py", root,
                   "--output-dir", transcript_dir, "--timeout", args.timeout]
        if args.agent:
            command.extend(["--agent", args.agent])
        code = run(command)
        if code not in (0, 1):
            return code

    if args.run_ocr:
        code = run([sys.executable, SCRIPT_DIR / "ocr_long_images.py", root,
                    "--output-dir", ocr_dir])
        if code not in (0, 1):
            return code

    if not args.skip_extract:
        code = run([sys.executable, SCRIPT_DIR / "extract_text_sources.py", root,
                    "--output-dir", document_dir])
        if code not in (0, 1):
            return code

    if inventory(root, index_dir, derived_dirs, args.mode) != 0:
        return 1

    # Build one redacted, deduplicated evidence manifest only after all
    # available transcription, OCR and document extraction has completed.
    batch_path = root / "咨询转化工作区" / "_系统" / "案例标准化" / ("蒸馏批次-" + run_id + ".jsonl")
    batch_code = run([sys.executable, SCRIPT_DIR / "prepare_distillation_batch.py",
                      transcript_dir, ocr_dir / "text", document_dir, "--output", batch_path])
    if batch_code != 0:
        return batch_code

    shadow_code = run([sys.executable, SCRIPT_DIR / "run_shadow_analysis.py", root,
                       "--batch", batch_path, "--count", "3"])

    coverage_path = index_dir / "coverage-report.json"
    coverage = read_json(coverage_path)
    gate = "ready_for_agent_distillation" if coverage.get("full_processing_ready") else "partial_ready_pending_processing"
    gate_payload = {
        "run_id": run_id,
        "gate": gate,
        "coverage": coverage,
        "standardized_batch": str(batch_path),
        "shadow_run_status": "ready" if shadow_code == 0 else "waiting_for_cases",
        "next_agent_step": (
            "按咨询转化八步法和全链路蒸馏提示词读取全部样本，按资料性质与结果权重分层汇总，同时输出 candidate.json、knowledge-candidate.json 和 patient-insight-candidate.json；分别运行三类候选写回脚本；不要只分析单条，也不要因缺少已到/未到标签跳过样本。"
            if gate == "ready_for_agent_distillation" else
            "继续处理 pending 项；当前只能输出部分样本候选分析。"
        ),
    }
    gate_path = run_dir / "distillation-gate.json"
    with io.open(str(gate_path), "w", encoding="utf-8") as handle:
        json.dump(gate_payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(gate_payload, ensure_ascii=False))
    return 0 if gate == "ready_for_agent_distillation" else 3


if __name__ == "__main__":
    sys.exit(main())
