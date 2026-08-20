#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail fast when the repository is not ready to publish its public Core."""

import argparse
import io
import json
import subprocess
import sys
from pathlib import Path

from project_version import core_version, core_version_tag


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return handle.read()


def main():
    parser = argparse.ArgumentParser(description="Check V2.2 public Core release invariants.")
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    version = core_version()
    tag = core_version_tag()
    errors = []
    required = (
        "VERSION", "SKILL.md", "README.md", "LICENSE", "CHANGELOG.md",
        "CONTRIBUTING.md", "SECURITY.md", "agents/openai.yaml",
        "runtime/base-runtime.json", "references/public-package-allowlist.json",
        "references/distribution-and-feedback.md", "scripts/product_feedback.py",
        ".github/workflows/ci.yml", ".github/workflows/release.yml",
    )
    for relative in required:
        if not (ROOT / relative).is_file():
            errors.append("missing:" + relative)
    if args.tag and args.tag != tag:
        errors.append("tag_must_match_VERSION:{0}!={1}".format(args.tag, tag))
    if "V" + version not in read(ROOT / "README.md") and tag not in read(ROOT / "README.md"):
        errors.append("README_version_missing")
    runtime = json.loads(read(ROOT / "runtime" / "base-runtime.json"))
    if runtime.get("core_version") != version or runtime.get("runtime_version") != tag:
        errors.append("base_runtime_version_mismatch")
    allowlist = set(json.loads(read(ROOT / "references" / "public-package-allowlist.json")).get("files") or [])
    for relative in ("VERSION", "scripts/project_version.py", "scripts/doctor.py",
                     "scripts/verify_public_package.py", "scripts/product_feedback.py",
                     "references/open-source-runtime.md", "references/distribution-and-feedback.md"):
        if relative not in allowlist:
            errors.append("public_allowlist_missing:" + relative)
    try:
        output = subprocess.check_output(["git", "ls-files", "*.skill", "*.zip"], cwd=str(ROOT))
        tracked = [line for line in output.decode("utf-8").splitlines() if line.strip()]
        if tracked:
            errors.append("generated_release_artifacts_tracked:" + ",".join(tracked))
    except (OSError, subprocess.CalledProcessError):
        pass
    result = {"status": "ready" if not errors else "rejected", "core_version": version,
              "tag": tag, "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
