#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the v1.8 deterministic runtime helpers."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMA = ROOT / "scripts" / "ima_sync.py"
PERSONAL = ROOT / "scripts" / "personal_growth.py"


def run_script(script, *args):
    process = subprocess.Popen([sys.executable, str(script)] + list(args), cwd=str(ROOT),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    stdout = stdout.decode("utf-8")
    stderr = stderr.decode("utf-8")
    if process.returncode != 0:
        raise AssertionError("command failed: {}\n{}\n{}".format(script, stdout, stderr))
    return json.loads(stdout)


class V18Tests(unittest.TestCase):
    def test_ima_inventory_queue_and_quota_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            listing = workspace / "ima-listing.json"
            with listing.open("w") as handle:
                handle.write(json.dumps({"data": {"knowledge_list": [
                {"media_type": 99, "media_id": "folder_1", "title": "已到文件夹"},
                {"media_type": 9, "media_id": "positive-1", "title": "已约优秀-费用.jpg"},
                {"media_type": 9, "media_id": "negative-1", "title": "未到-爽约.jpg"},
                {"media_type": 9, "media_id": "unknown-1", "title": "普通咨询.jpg"},
                ]}}, ensure_ascii=False))
            result = run_script(IMA, "inventory", str(workspace), "--input", str(listing),
                                "--knowledge-base", "测试知识库")
            self.assertEqual(result["total"], 3)
            queued = run_script(IMA, "queue", str(workspace), "--limit", "3")
            self.assertEqual(queued["selected"], 3)
            self.assertEqual(queued["roles"]["positive_reference"], 1)
            self.assertEqual(queued["roles"]["negative_reference"], 1)
            run_script(IMA, "record", str(workspace), "--media-id", "positive-1",
                       "--quota-error", "--error", "资料获取次数已达上限，请明天再尝试")
            status = run_script(IMA, "status", str(workspace))
            self.assertEqual(status["status_counts"]["quota_blocked"], 1)
            events = workspace / "咨询转化工作区" / "_系统" / "IMA同步" / "quota-events.jsonl"
            self.assertTrue(events.is_file())
            with events.open("r") as handle:
                self.assertIn("positive-1", handle.read())

    def test_personal_growth_survives_team_rebase(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            run_script(PERSONAL, "init", str(workspace), "--operator", "A001_张三", "--team-release", "Team-v1.8")
            run_script(PERSONAL, "case", str(workspace), "--source-id", "case-1", "--role", "positive",
                       "--summary", "先共情后追问费用顾虑", "--outcome", "预约")
            result = run_script(PERSONAL, "rule", str(workspace), "--rule-id", "personal-fee-1",
                                "--text", "费用顾虑先确认担心价格还是担心无效", "--status", "active")
            self.assertEqual(result["rule_status"], "active")
            run_script(PERSONAL, "compose", str(workspace))
            run_script(PERSONAL, "rebase", str(workspace), "--team-release", "Team-v1.9")
            runtime = workspace / "咨询转化工作区" / "_系统" / "个人成长" / "runtime-manifest.json"
            with runtime.open("r") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["team_release"], "Team-v1.9")
            self.assertEqual(payload["revalidated_candidate_count"], 1)
            candidates = workspace / "咨询转化工作区" / "_系统" / "个人成长" / "personal-candidates.jsonl"
            with candidates.open("r") as handle:
                self.assertIn("needs_revalidation", handle.read())

    def test_team_package_allowlist(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import build_team_skill_package as builder

        self.assertTrue(builder.should_copy_frontline(Path("scripts/ima_sync.py")))
        self.assertTrue(builder.should_copy_frontline(Path("scripts/route_consultation.py")))
        self.assertTrue(builder.should_copy_frontline(Path("scripts/verify_consult_workspace.py")))
        self.assertTrue(builder.should_copy_frontline(Path("scripts/batch_transcribe_younavi.py")))
        self.assertFalse(builder.should_copy_frontline(Path("scripts/run_full_distillation.py")))
        self.assertFalse(builder.should_copy_frontline(Path("scripts/publish_release.py")))
        self.assertTrue(builder.should_copy_frontline(Path("references/frontline-runtime.md")))
        self.assertTrue(builder.should_copy_frontline(Path("references/specialist-routing.json")))
        self.assertTrue(builder.should_copy_frontline(Path("references/workspace-initialization-contract.md")))
        self.assertTrue(builder.should_copy_frontline(Path("runtime/base-runtime.json")))


if __name__ == "__main__":
    unittest.main()
