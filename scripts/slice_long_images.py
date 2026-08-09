#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slice long chat screenshots into overlapping, OCR-friendly images."""

import argparse
import io
import json
import os
import sys
from pathlib import Path

from compat import ensure_dir, expand_path

from PIL import Image


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def image_files(input_path):
    path = expand_path(input_path)
    if path.is_file():
        return [path] if path.suffix.lower() in IMAGE_EXTS else []
    return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def slice_one(source, input_root, output_root, max_height, overlap):
    rel = source.name if input_root.is_file() else str(source.relative_to(input_root))
    with Image.open(str(source)) as image:
        image = image.convert("RGB")
        width, height = image.size
        source_dir = output_root / Path(rel).parent / (Path(rel).stem + "_slices")
        ensure_dir(source_dir)
        rows = []
        if height <= max_height:
            target = source_dir / "part-001.jpg"
            image.save(str(target), "JPEG", quality=95, optimize=True)
            rows.append({"source": str(source), "slice": str(target), "part": 1,
                         "top": 0, "bottom": height, "height": height})
            return rows

        top = 0
        part = 1
        while top < height:
            bottom = min(top + max_height, height)
            target = source_dir / ("part-{0:03d}.jpg".format(part))
            image.crop((0, top, width, bottom)).save(str(target), "JPEG", quality=95, optimize=True)
            rows.append({"source": str(source), "slice": str(target), "part": part,
                         "top": top, "bottom": bottom, "height": bottom - top})
            if bottom == height:
                break
            top = max(0, bottom - overlap)
            part += 1
    return rows


def main():
    parser = argparse.ArgumentParser(description="Slice long chat screenshots with overlap.")
    parser.add_argument("input", help="image file or directory")
    parser.add_argument("--output-dir", required=True, help="directory for slices and manifest")
    parser.add_argument("--max-height", type=int, default=2400)
    parser.add_argument("--overlap", type=int, default=160)
    args = parser.parse_args()
    if args.max_height <= 0 or args.overlap < 0 or args.overlap >= args.max_height:
        parser.error("overlap must be >= 0 and smaller than max-height")

    input_path = expand_path(args.input)
    output_root = expand_path(args.output_dir)
    ensure_dir(output_root)
    rows = []
    failures = []
    for source in image_files(input_path):
        try:
            rows.extend(slice_one(source, input_path, output_root, args.max_height, args.overlap))
        except Exception as exc:
            failures.append({"source": str(source), "error": str(exc)})

    manifest = output_root / "slice_manifest.jsonl"
    with io.open(str(manifest), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        for row in failures:
            handle.write(json.dumps(dict(row, status="failed"), ensure_ascii=False) + "\n")
    print(json.dumps({"slices": len(rows), "failures": len(failures),
                      "manifest": str(manifest)}, ensure_ascii=False))
    return 1 if failures and not rows else 0


if __name__ == "__main__":
    sys.exit(main())
