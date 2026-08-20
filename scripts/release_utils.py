#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared helpers for the atomic four-component runtime release."""

import hashlib
import io
import json
import os
import re
import tempfile
from pathlib import Path

from compat import ensure_dir, expand_path
from workspace_paths import assert_within, locate_workspace


COMPONENTS = {
    "capability": "当前能力包",
    "knowledge": "当前机构知识",
    "patient_insight": "患者洞察",
}
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def system_root(workspace):
    return locate_workspace(workspace) / "_系统"


def release_root(workspace):
    return system_root(workspace) / "发布"


def pointer_path(workspace, component):
    return system_root(workspace) / COMPONENTS[component] / "active.json"


def load_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def save_json(path, value):
    path = Path(path)
    ensure_dir(path.parent)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def atomic_save_json(path, value):
    path = Path(path)
    ensure_dir(path.parent)
    fd, temp_name = tempfile.mkstemp(prefix=".release-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, str(path))
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def sha256(path):
    if not path:
        return None
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def component_snapshot(workspace, component):
    pointer = load_json(pointer_path(workspace, component), {"status": "base_only"}) or {"status": "base_only"}
    workspace_root = locate_workspace(workspace)
    package_path = assert_within(pointer.get("package_path"), workspace_root, "package_path") if pointer.get("package_path") else None
    runtime_path = assert_within(pointer.get("runtime_context_path"), workspace_root, "runtime_context_path") if pointer.get("runtime_context_path") else None
    if pointer.get("status") == "active" and (not package_path or not runtime_path or not package_path.is_file() or not runtime_path.is_file()):
        raise ValueError("{0} active pointer has missing artifacts".format(component))
    scope = pointer.get("scope") or {}
    package = load_json(package_path, {}) if package_path else {}
    if not scope:
        scope = package.get("scope") or {}
    return {
        "component": component,
        "status": pointer.get("status", "base_only"),
        "version": pointer.get("active_version"),
        "package_path": str(package_path) if package_path else None,
        "runtime_context_path": str(runtime_path) if runtime_path else None,
        "package_hash": sha256(package_path),
        "runtime_hash": sha256(runtime_path),
        "scope": scope,
        "pointer": pointer,
    }


def load_release_active(workspace):
    return load_json(release_root(workspace) / "active.json", {"status": "base_only"}) or {"status": "base_only"}


def load_release_file(workspace, release_version):
    validate_version(release_version)
    return load_json(release_root(workspace) / "versions" / str(release_version) / "release.json", None)


def validate_version(value):
    value = str(value or "")
    if not VERSION_RE.match(value):
        raise ValueError("invalid version; use letters, numbers, dot, underscore or dash")
    return value


def scope_conflicts(snapshots):
    errors = []
    keys = ("institution", "department", "disease_or_project", "channel")
    values = {}
    for snapshot in snapshots:
        if snapshot.get("status") != "active":
            continue
        for key in keys:
            value = (snapshot.get("scope") or {}).get(key)
            if not value or value in ("通用", "待确认", "未确认"):
                continue
            values.setdefault(key, set()).add(str(value))
    for key, options in values.items():
        if len(options) > 1:
            errors.append("scope conflict for {0}: {1}".format(key, ", ".join(sorted(options))))
    return errors
