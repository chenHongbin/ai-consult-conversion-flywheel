#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slice long images and OCR every slice with local Tesseract."""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from compat import ensure_dir, expand_path

from slice_long_images import image_files, slice_one


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

    for source in image_files(input_path):
        try:
            parts = slice_one(source, input_path, slices_root, args.max_height, args.overlap)
        except Exception as exc:
            failures.append({"source": str(source), "status": "slice_failed", "error": str(exc)})
            continue
        for part in parts:
            slice_path = Path(part["slice"])
            try:
                proc = subprocess.Popen(
                    [tesseract, str(slice_path), "stdout", "-l", args.lang, "--psm", str(args.psm)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                stdout, stderr = proc.communicate()
                stdout = stdout.decode("utf-8", "replace")
                stderr = stderr.decode("utf-8", "replace")
                if proc.returncode != 0:
                    raise RuntimeError(stderr.strip() or "tesseract failed")
                text_path = text_root / slice_path.relative_to(slices_root).with_suffix(".txt")
                ensure_dir(text_path.parent)
                with io.open(str(text_path), "w", encoding="utf-8") as handle:
                    handle.write(stdout)
                rows.append(dict(part, text=str(text_path), ocr_engine="tesseract",
                                 ocr_lang=args.lang, status="ok"))
            except Exception as exc:
                failures.append(dict(part, status="ocr_failed", error=str(exc)))

    manifest = output_root / "ocr_manifest.jsonl"
    with io.open(str(manifest), "w", encoding="utf-8") as handle:
        for row in rows + failures:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"ocr_ok": len(rows), "failures": len(failures),
                      "manifest": str(manifest)}, ensure_ascii=False))
    return 1 if failures and not rows else 0


if __name__ == "__main__":
    sys.exit(main())
