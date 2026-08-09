#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slice long images and OCR every slice with local Tesseract."""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from compat import ensure_dir, expand_path

from slice_long_images import image_files, slice_one


def assess_ocr_quality(text):
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return {"quality": "low", "score": 0.0, "reason": "empty_text"}
    noise = len(re.findall(r"[�□■�]", compact))
    allowed = len(re.findall(r"[\u3400-\u9fffA-Za-z0-9，。！？、：；（）《》“”‘’\-—_.,!?():;\[\]{}]", compact))
    noise_ratio = noise / float(len(compact))
    readable_ratio = allowed / float(len(compact))
    score = max(0.0, min(1.0, readable_ratio - noise_ratio * 0.8))
    if len(compact) < 12 or noise_ratio > 0.25 or score < 0.45:
        quality = "low"
    elif score < 0.75:
        quality = "medium"
    else:
        quality = "high"
    return {"quality": quality, "score": round(score, 3), "reason": "automatic_text_quality"}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_previous(path):
    previous = {}
    if not path.is_file():
        return previous
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("source") and row.get("source_hash"):
                    previous[row["source"]] = row
    except IOError:
        return previous
    return previous


def main():
    parser = argparse.ArgumentParser(description="Slice and OCR Chinese chat screenshots.")
    parser.add_argument("input", help="image file or directory")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--lang", default="chi_sim+eng")
    parser.add_argument("--max-height", type=int, default=2400)
    parser.add_argument("--overlap", type=int, default=160)
    parser.add_argument("--psm", default="6")
    args = parser.parse_args()

    tesseract = shutil.which("tesseract")
    if not tesseract:
        print("ERROR: tesseract is not installed or not on PATH", file=sys.stderr)
        return 2
    output_root = expand_path(args.output_dir)
    slices_root = output_root / "slices"
    text_root = output_root / "text"
    ensure_dir(slices_root)
    ensure_dir(text_root)
    input_path = expand_path(args.input)
    rows = []
    failures = []
    previous_manifest = load_previous(output_root / "ocr_manifest.jsonl")

    for source in image_files(input_path):
        source_hash = sha256(source)
        previous = previous_manifest.get(str(source))
        if previous and previous.get("source_hash") == source_hash and previous.get("status") in ("ok", "skipped_existing"):
            rows.append(dict(previous, status="skipped_existing"))
            continue
        try:
            parts = slice_one(source, input_path, slices_root, args.max_height, args.overlap)
        except Exception as exc:
            failures.append({"source": str(source), "source_hash": source_hash, "status": "slice_failed", "error": str(exc)})
            continue
        for part in parts:
            slice_path = Path(part["slice"])
            try:
                proc = subprocess.Popen(
                    # The bundled macOS Tesseract build used in some learner
                    # environments mishandles absolute input paths. Running
                    # from the slice directory with a relative ASCII filename
                    # is equivalent and much more reliable.
                    [tesseract, slice_path.name, "stdout", "-l", args.lang, "--psm", str(args.psm)],
                    cwd=str(slice_path.parent),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                stdout, stderr = proc.communicate()
                stdout = stdout.decode("utf-8", "replace")
                stderr = stderr.decode("utf-8", "replace")
                if proc.returncode != 0:
                    raise RuntimeError(stderr.strip() or "tesseract failed")
                quality = assess_ocr_quality(stdout)
                text_path = text_root / slice_path.relative_to(slices_root).with_suffix(".txt")
                ensure_dir(text_path.parent)
                with io.open(str(text_path), "w", encoding="utf-8") as handle:
                    handle.write(stdout)
                rows.append(dict(part, source_hash=source_hash, text=str(text_path), ocr_engine="tesseract",
                                 ocr_lang=args.lang, status="ok", ocr_quality=quality["quality"],
                                 ocr_quality_score=quality["score"], ocr_quality_reason=quality["reason"]))
            except Exception as exc:
                failures.append(dict(part, source_hash=source_hash, status="ocr_failed", error=str(exc)))

    manifest = output_root / "ocr_manifest.jsonl"
    with io.open(str(manifest), "w", encoding="utf-8") as handle:
        for row in rows + failures:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"ocr_ok": len(rows), "failures": len(failures),
                      "manifest": str(manifest)}, ensure_ascii=False))
    return 1 if failures and not rows else 0


if __name__ == "__main__":
    sys.exit(main())
