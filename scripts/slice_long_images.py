#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slice long chat screenshots into overlapping, OCR-friendly images."""

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

try:
    from PIL import Image
except ImportError:
    Image = None


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def image_files(input_path):
    path = expand_path(input_path)
    if path.is_file():
        return [path] if path.suffix.lower() in IMAGE_EXTS else []
    return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def image_backend():
    if Image is not None:
        return "pillow"
    if shutil.which("sips"):
        return "sips"
    return None


def sips_dimensions(source):
    process = subprocess.Popen(
        [shutil.which("sips"), "--oneLine", "-g", "pixelWidth", "-g", "pixelHeight", str(source)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate()
    text = stdout.decode("utf-8", "replace")
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", "replace").strip() or "sips could not read image")
    width = re.search(r"pixelWidth:\s*(\d+)", text)
    height = re.search(r"pixelHeight:\s*(\d+)", text)
    if not width or not height:
        raise RuntimeError("sips did not return image dimensions")
    return int(width.group(1)), int(height.group(1))


def sips_crop(source, target, width, top, bottom):
    height = bottom - top
    process = subprocess.Popen(
        [shutil.which("sips"), "-c", str(height), str(width), "--cropOffset", str(top), "0",
         str(source), "--out", str(target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _, stderr = process.communicate()
    if process.returncode != 0 or not target.is_file():
        raise RuntimeError(stderr.decode("utf-8", "replace").strip() or "sips crop failed")


def slice_one(source, input_root, output_root, max_height, overlap):
    rel = source.name if input_root.is_file() else str(source.relative_to(input_root))
    backend = image_backend()
    if not backend:
        raise RuntimeError("image backend unavailable; install Pillow or use macOS sips")
    if backend == "pillow":
        with Image.open(str(source)) as image:
            image = image.convert("RGB")
            width, height = image.size
            source_key = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:16]
            source_dir = output_root / ("source-" + source_key) / "slices"
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
    width, height = sips_dimensions(source)
    source_key = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:16]
    source_dir = output_root / ("source-" + source_key) / "slices"
    ensure_dir(source_dir)
    rows = []
    top = 0
    part = 1
    while top < height:
        bottom = min(top + max_height, height)
        target = source_dir / ("part-{0:03d}.jpg".format(part))
        sips_crop(source, target, width, top, bottom)
        rows.append({"source": str(source), "slice": str(target), "part": part,
                     "top": top, "bottom": bottom, "height": bottom - top,
                     "image_backend": "sips"})
        if bottom == height:
            break
        top = max(0, bottom - overlap)
        part += 1
    return rows


def main():
    parser = argparse.ArgumentParser(description="Slice long chat screenshots with overlap.")
    parser.add_argument("input", nargs="?", help="image file or directory")
    parser.add_argument("--output-dir", default="", help="directory for slices and manifest")
    parser.add_argument("--max-height", type=int, default=2400)
    parser.add_argument("--overlap", type=int, default=160)
    parser.add_argument("--check", action="store_true", help="report image backend availability and exit")
    args = parser.parse_args()
    if args.check:
        backend = image_backend()
        print(json.dumps({"status": "ready" if backend else "missing_dependency", "image_backend": backend}, ensure_ascii=False))
        return 0 if backend else 2
    if not args.input or not args.output_dir:
        parser.error("input and --output-dir are required unless --check is used")
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
