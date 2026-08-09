#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a distributable team Skill containing only the approved capability pack.

The public/base Skill remains unchanged. This command copies the base package
to a temporary staging directory and adds a redacted institution-pack folder.
Raw recordings, patient chats and manager-only workspace files are never copied.
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from compat import ensure_dir, expand_path


PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WECHAT = re.compile(r"(微信号|微信|wxid)\s*[:：]?\s*[A-Za-z][A-Za-z0-9_-]{5,}", re.I)
SKIP_DIRS = {".git", "output", "__pycache__", ".venv", "node_modules", "咨询转化工作区"}
SKIP_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".amr", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".html", ".htm", ".xlsx", ".xls", ".csv", ".jsonl"}


def load_json(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path, value):
    ensure_dir(path.parent)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sensitive(text):
    return bool(PHONE.search(text) or ID_CARD.search(text) or EMAIL.search(text) or WECHAT.search(text))


def safe_name(value):
    value = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", str(value or "").strip())
    return value.strip("_") or "机构"


def copy_base(source_root, staging):
    for path in source_root.rglob("*"):
        relative = path.relative_to(source_root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.is_dir() or path.is_symlink():
            continue
        if path.suffix.lower() in SKIP_SUFFIXES and relative.parts[:2] != ("references", "test-set"):
            continue
        if path.name.endswith(".skill"):
            continue
        target = staging / relative
        ensure_dir(target.parent)
        shutil.copy2(str(path), str(target))


def main():
    parser = argparse.ArgumentParser(description="Build a weekly institution/team Skill package.")
    parser.add_argument("workspace_root")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--institution", default="当前机构")
    parser.add_argument("--department", default="当前科室")
    parser.add_argument("--channel", default="通用")
    parser.add_argument("--version", help="defaults to the active capability version")
    args = parser.parse_args()

    workspace = expand_path(args.workspace_root)
    source_root = Path(__file__).resolve().parents[1]
    package_root = workspace / "咨询转化工作区" / "_系统" / "当前能力包"
    active_path = package_root / "active.json"
    if not active_path.is_file():
        print(json.dumps({"status": "rejected", "reason": "active_capability_missing"}, ensure_ascii=False))
        return 2
    active = load_json(active_path)
    if active.get("status") != "active" or not active.get("package_path"):
        print(json.dumps({"status": "rejected", "reason": "capability_not_published", "active": active}, ensure_ascii=False))
        return 2
    capability_path = expand_path(active["package_path"])
    runtime_path = expand_path(active.get("runtime_context_path", ""))
    if not capability_path.is_file() or not runtime_path.is_file():
        print(json.dumps({"status": "rejected", "reason": "capability_artifacts_missing"}, ensure_ascii=False))
        return 2
    capability = load_json(capability_path)
    with io.open(str(runtime_path), "r", encoding="utf-8") as handle:
        runtime_text = handle.read()
    serialized = json.dumps(capability, ensure_ascii=False) + "\n" + runtime_text
    if sensitive(serialized):
        print(json.dumps({"status": "rejected", "reason": "capability_contains_possible_personal_identifier"}, ensure_ascii=False))
        return 2

    version = args.version or active.get("active_version")
    institution = args.institution or capability.get("scope", {}).get("institution", "当前机构")
    department = args.department or capability.get("scope", {}).get("department", "当前科室")
    filename = "AI咨询转化飞轮_{0}_{1}_{2}.skill".format(safe_name(institution), safe_name(department), safe_name(version))
    output_dir = expand_path(args.output_dir)
    ensure_dir(output_dir)
    output = output_dir / filename
    staging = Path(tempfile.mkdtemp(prefix="ai-flywheel-team-package-"))
    try:
        copy_base(source_root, staging)
        pack_dir = staging / "institution-pack"
        ensure_dir(pack_dir)
        save_json(pack_dir / "package.json", capability)
        with io.open(str(pack_dir / "runtime-context.md"), "w", encoding="utf-8") as handle:
            handle.write(runtime_text)
        manifest = {
            "package_type": "team_runtime_skill",
            "base_skill_name": "AI咨询转化飞轮",
            "base_skill_version": "v1.3",
            "capability_version": version,
            "institution": institution,
            "department": department,
            "channel": args.channel,
            "published_at": datetime.datetime.now().isoformat(),
            "capability_hash": sha256(capability_path),
            "runtime_hash": sha256(runtime_path),
            "contains_raw_patient_material": False,
            "contains_manager_workspace": False,
            "update_rule": "install this package to update the team's institution capability",
        }
        save_json(pack_dir / "manifest.json", manifest)
        with io.open(str(pack_dir / "团队更新说明.md"), "w", encoding="utf-8") as handle:
            handle.write("# 团队 Skill 更新说明\n\n")
            handle.write("这是 {0} / {1} 的咨询转化能力包 {2}。\n\n".format(institution, department, version))
            handle.write("安装或更新这个 Skill 后，日常咨询分析、顾虑处理、流失诊断和新人陪练会优先使用本机构能力。\n")
            handle.write("本包不包含原始患者录音、微信聊天、姓名、电话、病历或主管私有工作区。\n")
        if output.exists():
            output.unlink()
        with zipfile.ZipFile(str(output), "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(str(path), str(path.relative_to(staging)))
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)

    release_dir = workspace / "咨询转化工作区" / "_系统" / "团队发布包" / str(version)
    ensure_dir(release_dir)
    shutil.copy2(str(output), str(release_dir / filename))
    save_json(release_dir / "release-manifest.json", manifest)
    print(json.dumps({"status": "built", "package": str(output), "version": version,
                      "release_manifest": str(release_dir / "release-manifest.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
