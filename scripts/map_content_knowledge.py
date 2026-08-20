#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a read-only logical mapping for legacy content-workbench folders."""

import argparse
import datetime
import io
import json
import re
import sys
from pathlib import Path

from compat import ensure_dir, expand_path
from workspace_paths import locate_workspace


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "references" / "content-knowledge-mapping.json"
SKIP_NAMES = {".git", "node_modules", "output", "__pycache__", "_系统", ".venv"}


def load_json(path, default=None):
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def save_json(path, value):
    ensure_dir(path.parent)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def normalize(value):
    value = re.sub(r"[\s_\-/、（）()]+", "", str(value or "").lower())
    return value[:-1] if value.endswith("库") else value


def match_category(name, categories):
    target = normalize(name)
    ranked = []
    for index, category in enumerate(categories):
        for alias in category.get("aliases", []):
            candidate = normalize(alias)
            if target == candidate:
                ranked.append((100 + len(candidate), index, category, alias))
            elif candidate and (candidate in target or target in candidate):
                ranked.append((len(candidate), index, category, alias))
    if not ranked:
        return None, None
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][2], ranked[0][3]


def iter_dirs(source_root, max_depth):
    source_root = expand_path(source_root)
    if not source_root.is_dir():
        return
    for path in sorted(source_root.rglob("*")):
        if not path.is_dir() or any(part in SKIP_NAMES for part in path.parts):
            continue
        try:
            depth = len(path.relative_to(source_root).parts)
        except ValueError:
            continue
        if depth <= max_depth:
            yield path


def main():
    parser = argparse.ArgumentParser(description="Map legacy consultation content folders without moving source files.")
    parser.add_argument("workspace_root")
    parser.add_argument("--source-root", action="append", default=[])
    parser.add_argument("--ima-folder", action="append", default=[])
    parser.add_argument("--max-depth", type=int, default=3)
    args = parser.parse_args()
    workspace = locate_workspace(args.workspace_root)
    registry = load_json(REGISTRY, {}) or {}
    categories = registry.get("categories", [])
    roots = args.source_root or [args.workspace_root]
    mappings = []
    seen = set()
    for source in roots:
        for path in iter_dirs(source, max(1, args.max_depth)):
            category, alias = match_category(path.name, categories)
            if not category:
                continue
            key = ("local", str(path.resolve()), category.get("id"))
            if key in seen:
                continue
            seen.add(key)
            mappings.append({
                "source_type": "local",
                "source_name": path.name,
                "source_ref": str(path.resolve()),
                "matched_alias": alias,
                "logical_category": category.get("id"),
                "canonical_target": category.get("canonical_target"),
                "knowledge_object": category.get("knowledge_object"),
                "review_status": "indexed_not_approved",
            })
    for folder_name in args.ima_folder:
        category, alias = match_category(folder_name, categories)
        if not category:
            continue
        key = ("ima", folder_name, category.get("id"))
        if key in seen:
            continue
        seen.add(key)
        mappings.append({
            "source_type": "ima",
            "source_name": folder_name,
            "source_ref": "ima-folder:{0}".format(folder_name),
            "matched_alias": alias,
            "logical_category": category.get("id"),
            "canonical_target": category.get("canonical_target"),
            "knowledge_object": category.get("knowledge_object"),
            "review_status": "indexed_not_approved",
        })
    output = workspace / "_系统" / "内容资产" / "knowledge-mapping.json"
    result = {
        "schema_version": "2.1.2-content-knowledge-index",
        "generated_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "workspace": str(workspace),
        "source_roots": [str(expand_path(item)) for item in roots],
        "mapping_count": len(mappings),
        "mappings": mappings,
        "moves_or_renames_source_files": False,
        "mapping_implies_approval": False,
    }
    save_json(output, result)
    result["path"] = str(output)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
