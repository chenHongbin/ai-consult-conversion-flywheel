#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Acceptance tests for the V2.2 public Core and private workspace boundary."""

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BUILD = SCRIPTS / "build_base_skill_package.py"
VERIFY = SCRIPTS / "verify_public_package.py"
INIT = SCRIPTS / "init_consult_workspace.py"
DOCTOR = SCRIPTS / "doctor.py"
RELEASE_CHECK = SCRIPTS / "release_check.py"
PRODUCT_FEEDBACK = SCRIPTS / "product_feedback.py"
ROUTER = SCRIPTS / "route_consultation.py"


def run_json(script, *args, **kwargs):
    expected = kwargs.pop("expected", (0,))
    process = subprocess.Popen([sys.executable, str(script)] + [str(value) for value in args],
                               cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    if process.returncode not in expected:
        raise AssertionError("command failed: {0}\n{1}\n{2}".format(
            script, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")))
    return json.loads(stdout.decode("utf-8"))


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class OpenSourceCoreV22Tests(unittest.TestCase):
    def test_version_is_single_source_and_public_build_is_reproducible(self):
        with io.open(str(ROOT / "VERSION"), "r", encoding="utf-8") as handle:
            version = handle.read().strip()
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = run_json(BUILD, "--output-dir", first_dir)
            second = run_json(BUILD, "--output-dir", second_dir)
            first_path = Path(first["package"])
            second_path = Path(second["package"])
            self.assertEqual(first["version"], "v" + version)
            self.assertEqual(file_hash(first_path), file_hash(second_path))
            with zipfile.ZipFile(str(first_path), "r") as archive:
                manifest = json.loads(archive.read("package-manifest.json").decode("utf-8"))
                self.assertEqual(manifest["core_version"], version)
                self.assertEqual(manifest["workspace_schema_version"], "v2.1.3")
                self.assertFalse(manifest["contains_user_workspace"])
                self.assertEqual(manifest["telemetry"], "disabled_by_default")
                self.assertIn("VERSION", archive.namelist())
                self.assertIn("references/open-source-runtime.md", archive.namelist())
                self.assertIn("scripts/doctor.py", archive.namelist())
                self.assertIn("scripts/product_feedback.py", archive.namelist())
                self.assertIn("references/distribution-and-feedback.md", archive.namelist())
                self.assertIn("skills/medical-image-studio/SKILL.md", archive.namelist())
                self.assertNotIn("references/lai-methodology.md", archive.namelist())
            verified = run_json(VERIFY, first_path)
            self.assertEqual(verified["status"], "verified")

    def test_workspace_is_outside_core_and_records_ownership_boundary(self):
        blocked = run_json(INIT, ROOT, expected=(2,))
        self.assertEqual(blocked["reason"], "workspace_must_be_outside_core_directory")
        with tempfile.TemporaryDirectory() as directory:
            created = run_json(INIT, directory, "--manager-name", "合成主管", "--members", "A001_合成员工")
            workspace = Path(created["workspace"])
            with io.open(str(workspace / "_系统" / "工作区清单.json"), "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            self.assertEqual(manifest["created_by_core_version"], "2.2.1")
            self.assertEqual(manifest["data_ownership"], "local_user_controlled")
            self.assertEqual(manifest["upstream_sync"], "disabled")
            self.assertEqual(manifest["institution_binding"], "one_workspace_one_institution")
            diagnosis = run_json(DOCTOR, workspace)
            self.assertEqual(diagnosis["status"], "ready")
            self.assertTrue(diagnosis["workspace"]["outside_core_directory"])

    def test_repository_release_contract_is_ready(self):
        result = run_json(RELEASE_CHECK)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["tag"], "v2.2.1")

    def test_feedback_is_local_and_rejects_patient_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            created = run_json(INIT, directory, "--manager-name", "合成主管", "--members", "A001_合成员工")
            workspace = Path(created["workspace"])
            feedback = run_json(
                PRODUCT_FEEDBACK, "create", workspace,
                "--category", "suggestion",
                "--summary", "希望今日重点可以按风险筛选",
                "--expected", "可以选择只看高风险",
                "--actual", "目前需要继续翻页",
                "--steps", "使用完全合成数据打开今日重点",
                "--include-diagnostics",
            )
            self.assertEqual(feedback["status"], "created")
            self.assertFalse(feedback["auto_uploaded"])
            with io.open(feedback["record"], "r", encoding="utf-8") as handle:
                record = json.load(handle)
            serialized = json.dumps(record, ensure_ascii=False)
            self.assertNotIn(str(workspace), serialized)
            self.assertFalse(record["contains_raw_patient_material"])
            rejected = run_json(
                PRODUCT_FEEDBACK, "create", workspace,
                "--category", "bug", "--summary", "患者电话13800138000无法处理",
                expected=(2,),
            )
            self.assertEqual(rejected["status"], "rejected")

    def test_feedback_natural_language_has_route_precedence(self):
        for text in ("我要反馈问题", "安装失败了", "这个分析结果不准，我要反馈"):
            result = run_json(ROUTER, text)
            self.assertEqual(result["route_id"], "product_feedback")


if __name__ == "__main__":
    unittest.main()
