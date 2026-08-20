#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent manager approval receipts bound to immutable candidate hashes."""

import datetime
import hashlib
import io
import json
import os
import tempfile
import uuid
from pathlib import Path

from compat import ensure_dir, expand_path
from workspace_paths import locate_workspace

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows hosts use atomic receipt files.
    fcntl = None


COMPONENTS = ("capability", "knowledge", "patient_insight", "content_asset")


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def load_json(path, default=None):
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def approval_root(workspace):
    root = locate_workspace(workspace) / "_系统" / "审核账本"
    ensure_dir(root / "approvals")
    return root


def workspace_identity(workspace):
    root = locate_workspace(workspace)
    manifest = load_json(root / "_系统" / "工作区清单.json", {}) or {}
    workspace_id = manifest.get("workspace_id")
    if not workspace_id:
        raise ValueError("workspace_id missing; rerun V2.1.3 initialization")
    return root, workspace_id


def require_manager(workspace):
    root, workspace_id = workspace_identity(workspace)
    role = load_json(root / "_系统" / "运行时角色.json", {}) or {}
    profile = load_json(root / "_系统" / "首次设置" / "confirmed-profile.json", {}) or {}
    if role.get("role") != "manager" or (profile and profile.get("role") != "manager"):
        raise ValueError("manager authorization required")
    return root, workspace_id, profile


def atomic_write_new(path, value):
    path = Path(path)
    ensure_dir(path.parent)
    descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def append_locked(path, value):
    ensure_dir(path.parent)
    with io.open(str(path), "a", encoding="utf-8") as handle:
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def required_checks(component, candidate):
    required = set()
    if component in ("capability", "patient_insight"):
        required.update(("evaluation", "coverage"))
    if component == "patient_insight":
        required.add("privacy")
        delta = candidate.get("delta") or {}
        states = delta.get("decision_states_upsert") or []
        if any(item.get("clinical_boundary") not in (None, "non_clinical") for item in states if isinstance(item, dict)):
            required.add("clinical")
    return required


def create_approval(workspace, component, candidate_path, reviewer, note="", checks=None):
    if component not in COMPONENTS:
        raise ValueError("unsupported approval component")
    root, workspace_id, profile = require_manager(workspace)
    candidate_path = expand_path(candidate_path)
    if not candidate_path.is_file():
        raise ValueError("candidate file not found")
    reviewer = str(reviewer or "").strip()
    if not reviewer:
        raise ValueError("reviewer is required")
    candidate = load_json(candidate_path, {}) or {}
    checks = sorted(set(checks or []))
    missing_checks = sorted(required_checks(component, candidate) - set(checks))
    if missing_checks:
        raise ValueError("approval missing independent checks: {0}".format(", ".join(missing_checks)))
    scope = candidate.get("scope") or {}
    for field in ("institution", "department"):
        expected = profile.get(field) if profile else None
        actual = scope.get(field)
        if expected and actual and expected != actual:
            raise ValueError("candidate scope does not match confirmed profile: {0}".format(field))
    receipt = {
        "schema_version": "2.1.3-approval-receipt",
        "approval_id": "APR-" + uuid.uuid4().hex,
        "decision": "approved",
        "component": component,
        "candidate_hash": sha256(candidate_path),
        "candidate_name": candidate_path.name,
        "workspace_id": workspace_id,
        "scope": scope,
        "reviewer": reviewer,
        "review_note": str(note or "").strip(),
        "independent_checks": checks,
        "approved_at": now_iso(),
    }
    ledger_root = approval_root(root)
    atomic_write_new(ledger_root / "approvals" / (receipt["approval_id"] + ".json"), receipt)
    append_locked(ledger_root / "approval-events.jsonl", receipt)
    return receipt


def validate_approval(workspace, component, candidate_path, approval_id):
    root, workspace_id = workspace_identity(workspace)
    if not approval_id:
        raise ValueError("--approval-id is required for publication")
    receipt = load_json(approval_root(root) / "approvals" / (str(approval_id) + ".json"), {}) or {}
    if receipt.get("decision") != "approved" or receipt.get("component") != component:
        raise ValueError("approval receipt is missing or does not match component")
    if receipt.get("workspace_id") != workspace_id:
        raise ValueError("approval receipt belongs to another workspace")
    if receipt.get("candidate_hash") != sha256(candidate_path):
        raise ValueError("candidate changed after approval")
    return receipt
