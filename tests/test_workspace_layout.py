#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the canonical workspace initialization contract."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "scripts" / "init_consult_workspace.py"
VERIFY = ROOT / "scripts" / "verify_consult_workspace.py"


def run_json(script, *args, expected_codes=(0,)):
    process = subprocess.Popen(
        [sys.executable, str(script)] + list(args), cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate()
    stdout = stdout.decode("utf-8")
    stderr = stderr.decode("utf-8")
    if process.returncode not in expected_codes:
        raise AssertionError("command failed: {}\n{}\n{}".format(script, stdout, stderr))
    return json.loads(stdout)


class WorkspaceLayoutTests(unittest.TestCase):
    def test_default_init_uses_child_container_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_json(INIT, directory)
            workspace = Path(directory) / "咨询转化工作区"
            self.assertEqual(Path(result["workspace"]), workspace)
            self.assertTrue(result["canonical_layout"])
            self.assertTrue((workspace / "_系统" / "工作区清单.json").is_file())
            verified = run_json(VERIFY, directory)
            self.assertEqual(verified["status"], "canonical")
            self.assertEqual(verified["location"], "child_workspace")

    def test_verify_detects_root_mode_as_selected_folder_not_wrong_names(self):
        with tempfile.TemporaryDirectory() as directory:
            run_json(INIT, directory, "--use-root")
            verified = run_json(VERIFY, directory)
            self.assertEqual(verified["status"], "canonical")
            self.assertEqual(verified["location"], "selected_folder")

    def test_verify_reports_partial_layout_without_touching_unknown_files(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "咨询转化工作区"
            (workspace / "01_机构介绍").mkdir(parents=True)
            (workspace / "我的原有资料").mkdir()
            result = run_json(VERIFY, directory, expected_codes=(1,))
            self.assertEqual(result["status"], "needs_repair")
            self.assertIn("02_科室项目与服务", result["missing_visible_folders"])
            self.assertTrue((workspace / "我的原有资料").is_dir())

    def test_init_rejects_non_canonical_container_name(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_json(INIT, directory, "--name", "我的知识库", expected_codes=(2,))
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["reason"], "non_canonical_workspace_name")


if __name__ == "__main__":
    unittest.main()
