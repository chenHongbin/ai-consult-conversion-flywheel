#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the public/base AI咨询转化飞轮 .skill archive."""

import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path

from compat import ensure_dir, expand_path
from privacy_guard import scan_file


ALLOWLIST = "references/public-package-allowlist.json"
VERSION_FILE = "VERSION"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def load_core_version(root):
    with io.open(str(root / VERSION_FILE), "r", encoding="utf-8") as handle:
        return handle.read().strip()


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def archive_write(archive, relative, content):
    info = zipfile.ZipInfo(str(relative), FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, content)


def should_include(path, root):
    allowlist_path = root / ALLOWLIST
    with io.open(str(allowlist_path), "r", encoding="utf-8") as handle:
        allowed = set(json.load(handle).get("files") or [])
    return path.is_file() and not path.is_symlink() and str(path.relative_to(root)) in allowed


def main():
    parser = argparse.ArgumentParser(description="Build the public/base Skill archive")
    parser.add_argument("--source-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--version", default="", help="must match the root VERSION file")
    args = parser.parse_args()
    root = expand_path(args.source_root)
    core_version = load_core_version(root)
    version = "v" + core_version
    if args.version and args.version not in (core_version, version):
        print(json.dumps({"status": "rejected", "reason": "version_must_match_VERSION",
                          "expected": version, "received": args.version}, ensure_ascii=False))
        return 2
    output_dir = expand_path(args.output_dir) if args.output_dir else root
    ensure_dir(output_dir)
    output = output_dir / "AI咨询转化飞轮_{0}.skill".format(version)
    with io.open(str(root / ALLOWLIST), "r", encoding="utf-8") as handle:
        allowed = sorted(set(json.load(handle).get("files") or []))
    missing = [item for item in allowed if not (root / item).is_file() or (root / item).is_symlink()]
    if missing:
        print(json.dumps({"status": "rejected", "reason": "allowlisted_files_missing", "files": missing}, ensure_ascii=False))
        return 2
    privacy_findings = []
    for relative in allowed:
        # Source files contain the privacy regexes and domain vocabulary by
        # design.  Run secret scanning on executable source, and full PII +
        # secret scanning on distributable human/runtime content.  The exact
        # allowlist remains the primary control against accidental raw files.
        is_source = Path(relative).suffix in (".py", ".sh", ".js", ".mjs")
        findings = scan_file(root / relative, include_secrets=True,
                             include_personal_data=not is_source)
        if findings:
            privacy_findings.append({"file": relative, "findings": findings})
    if privacy_findings:
        print(json.dumps({"status": "rejected", "reason": "package_privacy_scan_failed",
                          "findings": privacy_findings}, ensure_ascii=False))
        return 2
    file_contents = {}
    file_hashes = {}
    for relative in allowed:
        with (root / relative).open("rb") as handle:
            content = handle.read()
        file_contents[relative] = content
        file_hashes[relative] = sha256_bytes(content)
    manifest = {
        "package_type": "public_base_skill",
        "skill_name": "AI咨询转化飞轮",
        "technical_name": "medical-consult-conversion-coach",
        "version": version,
        "core_version": core_version,
        "workspace_schema_version": "v2.1.3",
        "contains_external_institution_data": False,
        "contains_ima_credentials": False,
        "contains_raw_patient_material": False,
        "contains_user_workspace": False,
        "telemetry": "disabled_by_default",
        "allowlist_schema": "2.2-public-package-allowlist",
        "privacy_scan_passed": True,
        "files": file_hashes,
    }
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(str(output), "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in allowed:
            archive_write(archive, relative, file_contents[relative])
        archive_write(archive, "package-manifest.json",
                      (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    with output.open("rb") as handle:
        package_hash = sha256_bytes(handle.read())
    print(json.dumps({"status": "built", "package": str(output), "version": version,
                      "sha256": package_hash, "reproducible": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
