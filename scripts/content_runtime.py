#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compile approved content assets into a frozen, distributable runtime component."""

import io
import json
from pathlib import Path

from approval_ledger import load_json, sha256, workspace_identity
from privacy_guard import scan_value
from release_utils import atomic_save_json
from workspace_paths import assert_within, locate_workspace


def load_jsonl(path):
    rows = []
    if not path.is_file():
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


def compile_content_runtime(workspace, release_dir, version):
    root, workspace_id = workspace_identity(workspace)
    approved_path = root / "_系统" / "内容资产" / "approved-assets.jsonl"
    assets = []
    for row in load_jsonl(approved_path):
        candidate_ref = row.get("candidate_ref")
        approval_id = row.get("approval_id")
        if not candidate_ref or not approval_id:
            raise ValueError("approved content asset is missing candidate or approval binding")
        candidate_path = assert_within(candidate_ref, root / "_系统" / "内容资产" / "candidates", "candidate_ref")
        receipt = load_json(root / "_系统" / "审核账本" / "approvals" / (approval_id + ".json"), {}) or {}
        if (receipt.get("decision") != "approved" or receipt.get("component") != "content_asset"
                or receipt.get("workspace_id") != workspace_id or receipt.get("candidate_hash") != sha256(candidate_path)):
            raise ValueError("approved content asset has an invalid approval receipt")
        if scan_value(row.get("content_body")):
            raise ValueError("approved content asset contains possible patient identifier")
        assets.append({
            "asset_id": row.get("asset_id"),
            "content_type": row.get("content_type"),
            "channel": row.get("channel"),
            "patient_stage": row.get("patient_stage"),
            "concern": row.get("concern"),
            "voice_scope": row.get("voice_scope"),
            "scope": row.get("scope") or {},
            "content_body": row.get("content_body"),
            "content_hash": row.get("content_hash"),
            "approval_id": approval_id,
            "approved_at": row.get("approved_at"),
        })
    assets.sort(key=lambda item: (item.get("content_type") or "", item.get("asset_id") or ""))
    package = {
        "schema_version": "2.1.3-content-runtime",
        "version": version,
        "workspace_id": workspace_id,
        "asset_count": len(assets),
        "assets": assets,
        "contains_raw_patient_material": False,
    }
    package_path = Path(release_dir) / "content-runtime.json"
    runtime_path = Path(release_dir) / "content-runtime.md"
    atomic_save_json(package_path, package)
    with io.open(str(runtime_path), "w", encoding="utf-8") as handle:
        handle.write("# 已审核内容运行时 {0}\n\n".format(version))
        handle.write("仅使用与当前渠道、阶段、顾虑和机构作用域匹配的内容；使用前仍需核对当次患者事实。\n\n")
        for item in assets:
            handle.write("## {0} / {1}\n\n{2}\n\n".format(
                item.get("content_type") or "content", item.get("asset_id"), item.get("content_body") or ""))
    scope = assets[0].get("scope") if assets else {}
    return {
        "component": "content_runtime",
        "status": "active",
        "version": version,
        "package_path": str(package_path),
        "runtime_context_path": str(runtime_path),
        "package_hash": sha256(package_path),
        "runtime_hash": sha256(runtime_path),
        "scope": scope or {},
        "asset_count": len(assets),
        "pointer": None,
    }
