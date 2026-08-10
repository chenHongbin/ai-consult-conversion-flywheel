#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the v1.9 base runtime and specialist router."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "scripts" / "route_consultation.py"
LOADER = ROOT / "scripts" / "load_active_capability.py"
INIT = ROOT / "scripts" / "init_consult_workspace.py"


def run_json(script, *args):
    process = subprocess.Popen(
        [sys.executable, str(script)] + list(args),
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate()
    stdout = stdout.decode("utf-8")
    stderr = stderr.decode("utf-8")
    if process.returncode != 0:
        raise AssertionError("command failed: {}\n{}\n{}".format(script, stdout, stderr))
    return json.loads(stdout)


class V19Tests(unittest.TestCase):
    def test_empty_workspace_can_use_base_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_json(LOADER, directory)
            self.assertEqual(result["status"], "base_only")
            self.assertEqual(result["runtime_mode"], "base_only")
            self.assertTrue(result["can_analyze"])
            self.assertFalse(result["institution_specific"])
            self.assertFalse(result["base_runtime"]["requires_distillation"])

    def test_specialist_routes_and_direct_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            fee = run_json(ROUTER, "患者说费用太贵，担心乱收费", "--workspace-root", directory)
            self.assertEqual(fee["status"], "routed")
            self.assertEqual(fee["route_id"], "objection_fee")
            self.assertEqual(fee["runtime_mode"], "base_only")
            self.assertIn("当前专项能力可在无蒸馏的基础运行时中直接使用", fee["message"])

            practice = run_json(ROUTER, "", "--route", "practice_coach", "--workspace-root", directory)
            self.assertEqual(practice["route_id"], "practice_coach")
            self.assertEqual(practice["confidence"], "high")

            listing = run_json(ROUTER, "--list")
            route_ids = {item["id"] for item in listing["routes"]}
            self.assertIn("objection_effect", route_ids)
            self.assertIn("no_show_followup", route_ids)
            self.assertIn("champion_distillation", route_ids)

    def test_manager_routes_are_blocked_for_frontline(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_json(ROUTER, "蒸馏销冠录音并发布团队 Skill", "--role", "frontline",
                              "--workspace-root", directory)
            self.assertEqual(result["status"], "manager_confirmation_required")
            self.assertEqual(result["permission"], "manager_only")

            manager = run_json(ROUTER, "蒸馏销冠录音", "--role", "manager", "--workspace-root", directory)
            self.assertEqual(manager["status"], "routed")
            self.assertEqual(manager["route_id"], "champion_distillation")

    def test_initialized_workspace_defaults_to_manager_route_role(self):
        with tempfile.TemporaryDirectory() as directory:
            run_json(INIT, directory, "--manager-name", "咨询主管A")
            result = run_json(ROUTER, "蒸馏销冠录音", "--workspace-root", directory)
            self.assertEqual(result["role"], "manager")
            self.assertEqual(result["status"], "routed")


if __name__ == "__main__":
    unittest.main()
