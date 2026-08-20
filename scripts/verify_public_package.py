#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify an official public Core package against its manifest and allowlist."""

import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]


def load_json(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Verify an AI consultation flywheel public Core package.")
    parser.add_argument("package")
    parser.add_argument("--source-root", default=str(ROOT))
    args = parser.parse_args()
    root = Path(args.source_root).resolve()
    package = Path(args.package).resolve()
    expected = set(load_json(root / "references" / "public-package-allowlist.json").get("files") or [])
    expected.add("package-manifest.json")
    errors = []
    try:
        with zipfile.ZipFile(str(package), "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                errors.append("duplicate_archive_entries")
            for name in names:
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts or "\\" in name:
                    errors.append("unsafe_archive_path:" + name)
            if set(names) != expected:
                errors.append("archive_allowlist_mismatch")
            manifest = json.loads(archive.read("package-manifest.json").decode("utf-8"))
            with io.open(str(root / "VERSION"), "r", encoding="utf-8") as handle:
                core_version = handle.read().strip()
            if manifest.get("core_version") != core_version or manifest.get("version") != "v" + core_version:
                errors.append("manifest_version_mismatch")
            if manifest.get("package_type") != "public_base_skill":
                errors.append("wrong_package_type")
            if manifest.get("contains_raw_patient_material") is not False:
                errors.append("raw_patient_material_flag_not_false")
            if manifest.get("contains_user_workspace") is not False:
                errors.append("user_workspace_flag_not_false")
            for relative, expected_hash in sorted((manifest.get("files") or {}).items()):
                if relative not in names or sha256(archive.read(relative)) != expected_hash:
                    errors.append("file_hash_mismatch:" + relative)
    except (IOError, KeyError, ValueError, zipfile.BadZipfile) as exc:
        errors.append("invalid_archive:" + str(exc))
    with package.open("rb") as handle:
        package_hash = sha256(handle.read())
    result = {"status": "verified" if not errors else "rejected", "package": str(package),
              "sha256": package_hash, "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
