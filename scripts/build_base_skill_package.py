#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the public/base AI咨询转化飞轮 .skill archive."""

import argparse
import datetime
import io
import json
import zipfile
from pathlib import Path

from compat import ensure_dir, expand_path


EXCLUDED_DIRS = {".git", "output", "__pycache__", ".venv", "node_modules", "咨询转化工作区", "tests"}
RAW_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".amr", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".html", ".htm", ".xlsx", ".xls", ".csv", ".jsonl"}


def should_include(path, root):
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        return False
    if path.name.endswith(".skill"):
        return False
    if path.suffix.lower() in RAW_SUFFIXES and relative.parts[:2] != ("references", "test-set"):
        return False
    return path.is_file() and not path.is_symlink()


def main():
    parser = argparse.ArgumentParser(description="Build the public/base Skill archive")
    parser.add_argument("--source-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--version", default="v2.0")
    args = parser.parse_args()
    root = expand_path(args.source_root)
    output_dir = expand_path(args.output_dir) if args.output_dir else root
    ensure_dir(output_dir)
    output = output_dir / "AI咨询转化飞轮_{0}.skill".format(args.version)
    manifest = {
        "package_type": "public_base_skill",
        "skill_name": "AI咨询转化飞轮",
        "technical_name": "medical-consult-conversion-coach",
        "version": args.version,
        "built_at": datetime.datetime.now().isoformat(),
        "contains_external_institution_data": False,
        "contains_ima_credentials": False,
        "contains_raw_patient_material": False,
    }
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(str(output), "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if should_include(path, root):
                archive.write(str(path), str(path.relative_to(root)))
        archive.writestr("package-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "built", "package": str(output), "version": args.version}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
