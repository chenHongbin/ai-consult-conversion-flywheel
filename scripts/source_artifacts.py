#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compile local and IMA sources into one content-addressed source ledger."""

import argparse
import datetime
import hashlib
import io
import json
from pathlib import Path

from compat import ensure_dir
from workspace_paths import assert_within, locate_workspace


def load_jsonl(path):
    rows = []
    if not Path(path).is_file():
        return rows
    with io.open(str(path), "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compile_ledger(workspace_root):
    workspace = locate_workspace(workspace_root)
    system = workspace / "_系统"
    local_path = system / "资料索引" / "workspace-inventory.jsonl"
    ima_path = system / "IMA同步" / "cache-index.jsonl"
    coverage_path = system / "资料索引" / "coverage-report.json"
    source_root = workspace
    try:
        with io.open(str(coverage_path), "r", encoding="utf-8") as handle:
            source_root = Path(json.load(handle).get("workspace_root") or workspace)
    except (IOError, ValueError):
        pass
    artifacts = []
    for row in load_jsonl(local_path):
        text_paths = [Path(value) for value in (row.get("derived_text_paths") or [])]
        if row.get("material_type") == "text_or_structured_text" and row.get("source_path"):
            text_paths.append(source_root / row["source_path"])
        for text_path in text_paths:
            if not text_path.is_file() or text_path.suffix.lower() != ".txt":
                continue
            content_hash = file_sha256(text_path)
            artifacts.append({
                "artifact_id": "artifact-" + content_hash[:20],
                "workspace_id": workspace_id(workspace),
                "source_type": "local",
                "source_id": row.get("source_id"),
                "content_hash": content_hash,
                "original_ref": row.get("source_path"),
                "derived_text_paths": [str(text_path)],
                "quality": row.get("derived_status") or row.get("processing_status"),
                "processing_basis": "local_analysis",
            })
    for row in load_jsonl(ima_path):
        raw_path = row.get("cache_path")
        if not raw_path:
            continue
        try:
            cache_path = assert_within(raw_path, workspace, "IMA cache_path")
        except ValueError:
            continue
        if not cache_path.is_file():
            continue
        content_hash = file_sha256(cache_path)
        artifacts.append({
            "artifact_id": "artifact-" + content_hash[:20],
            "workspace_id": workspace_id(workspace),
            "source_type": "ima",
            "source_id": row.get("source_id"),
            "content_hash": content_hash,
            "original_ref": "ima:{0}".format(row.get("media_id") or row.get("source_id")),
            "derived_text_paths": [str(cache_path)],
            "quality": row.get("quality") or "cached",
            "processing_basis": "local_analysis",
        })
    by_hash = {}
    for row in artifacts:
        group = by_hash.setdefault(row["content_hash"], [])
        group.append(row)
    output_rows = []
    for content_hash in sorted(by_hash):
        group = by_hash[content_hash]
        sources = sorted(set(item["source_type"] for item in group))
        for index, row in enumerate(group):
            value = dict(row)
            value["dedup_key"] = "sha256:" + content_hash
            value["cross_source_duplicate"] = len(group) > 1
            value["canonical_in_group"] = index == 0
            value["source_types_in_group"] = sources
            output_rows.append(value)
    output = system / "资料索引" / "source-artifacts.jsonl"
    ensure_dir(output.parent)
    with io.open(str(output), "w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return output, output_rows


def workspace_id(workspace):
    manifest = workspace / "_系统" / "workspace-manifest.json"
    try:
        with io.open(str(manifest), "r", encoding="utf-8") as handle:
            return json.load(handle).get("workspace_id") or "legacy-workspace"
    except (IOError, ValueError):
        return "legacy-workspace"


def main():
    parser = argparse.ArgumentParser(description="Build one source ledger for local and IMA materials")
    parser.add_argument("workspace_root")
    args = parser.parse_args()
    output, rows = compile_ledger(args.workspace_root)
    print(json.dumps({
        "status": "compiled",
        "output": str(output),
        "artifacts": len(rows),
        "unique_content": len(set(row["content_hash"] for row in rows)),
        "ima_ready": sum(1 for row in rows if row["source_type"] == "ima"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
