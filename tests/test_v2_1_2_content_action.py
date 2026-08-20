#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the V2.1.2 content action layer."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "scripts" / "init_consult_workspace.py"
ROUTER = ROOT / "scripts" / "route_consultation.py"
CONTENT = ROOT / "scripts" / "content_asset.py"
MAPPER = ROOT / "scripts" / "map_content_knowledge.py"


def run_json(script, *args, **kwargs):
    expected_codes = kwargs.pop("expected_codes", (0,))
    process = subprocess.Popen(
        [sys.executable, str(script)] + list(args),
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate()
    stdout = stdout.decode("utf-8")
    stderr = stderr.decode("utf-8")
    if process.returncode not in expected_codes:
        raise AssertionError("command failed: {0}\n{1}\n{2}".format(script, stdout, stderr))
    return json.loads(stdout)


class ContentActionTests(unittest.TestCase):
    def write_workspace_file(self, directory, relative, text="合成测试内容"):
        path = Path(directory) / "咨询转化工作区" / relative
        if not path.parent.is_dir():
            path.parent.mkdir(parents=True)
        with io.open(str(path), "w", encoding="utf-8") as handle:
            handle.write(text)
        return relative

    def configure_scope(self, directory):
        relative = "_系统/首次设置/confirmed-profile.json"
        self.write_workspace_file(directory, relative, json.dumps({
            "role": "manager", "institution": "合成医院", "department": "皮肤科"
        }, ensure_ascii=False))

    def test_four_content_requests_route_to_content_action_but_images_do_not(self):
        prompts = (
            "帮我写跟进话术",
            "写条朋友圈",
            "写患者科普",
            "写私信回复",
        )
        for prompt in prompts:
            result = run_json(ROUTER, prompt)
            self.assertEqual(result["route_id"], "content_action")
            self.assertEqual(result["status"], "routed")
            self.assertEqual(result["runtime_mode"], "base_only")
        visual = run_json(ROUTER, "做一张朋友圈配图")
        self.assertEqual(visual["route_id"], "visual_content")

    def test_workspace_contains_content_outputs_and_private_asset_store(self):
        with tempfile.TemporaryDirectory() as directory:
            run_json(INIT, directory)
            workspace = Path(directory) / "咨询转化工作区"
            for folder in ("01_跟进话术", "02_朋友圈文案", "03_患者科普", "04_私信承接"):
                self.assertTrue((workspace / "07_我的产出" / "07_内容行动工作台" / folder).is_dir())
            self.assertTrue((workspace / "_系统" / "内容资产").is_dir())

    def test_unknown_feedback_stays_unknown_and_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            run_json(INIT, directory)
            output_ref = "07_我的产出/07_内容行动工作台/01_跟进话术/case-1.md"
            draft = run_json(
                CONTENT, "record", directory,
                "--content-type", "followup", "--status", "draft",
                "--output-ref", output_ref,
                "--case-id", "case-1",
            )
            recorded = run_json(
                CONTENT, "record", directory, "--asset-id", draft["asset_id"],
                "--content-type", "followup", "--status", "sent",
                "--output-ref", output_ref, "--case-id", "case-1",
            )
            asset_id = recorded["asset_id"]
            self.assertEqual(recorded["record"]["replied"], "unknown")
            status = run_json(CONTENT, "status", directory, "--asset-id", asset_id)
            self.assertFalse(status["assets"][0]["positive_feedback"])
            self.assertFalse(status["assets"][0]["candidate"])
            blocked = run_json(
                CONTENT, "review", directory, "--asset-id", asset_id,
                "--decision", "approve", "--reviewer", "主管A",
                expected_codes=(2,),
            )
            self.assertEqual(blocked["status"], "blocked")
            self.assertTrue(any("正向结果" in item for item in blocked["blocked_reasons"]))

    def test_positive_evidence_creates_candidate_and_manager_can_approve(self):
        with tempfile.TemporaryDirectory() as directory:
            run_json(INIT, directory, "--role", "manager")
            self.configure_scope(directory)
            output_ref = self.write_workspace_file(
                directory, "07_我的产出/07_内容行动工作台/02_朋友圈文案/case-2.md",
                "本院已确认的科普跟进内容。")
            evidence_ref = self.write_workspace_file(
                directory, "_系统/内容资产/evidence/case-2-result.json", "{\"result\":\"positive\"}")
            draft = run_json(
                CONTENT, "record", directory,
                "--content-type", "moments", "--status", "draft",
                "--output-ref", output_ref,
                "--case-id", "case-2", "--knowledge-ref", "knowledge:fact-12",
            )
            asset_id = draft["asset_id"]
            run_json(
                CONTENT, "record", directory, "--asset-id", asset_id,
                "--content-type", "moments", "--status", "sent",
                "--output-ref", output_ref,
                "--case-id", "case-2", "--replied", "yes", "--reply-quality", "positive",
                "--evidence-ref", evidence_ref,
            )
            status = run_json(CONTENT, "status", directory, "--asset-id", asset_id)
            self.assertTrue(status["assets"][0]["candidate"])
            approved = run_json(
                CONTENT, "review", directory, "--asset-id", asset_id,
                "--decision", "approve", "--reviewer", "主管A",
            )
            self.assertEqual(approved["status"], "reviewed")
            self.assertTrue(Path(approved["approved_path"]).is_file())
            runtime = run_json(ROUTER, "写条朋友圈", "--workspace-root", directory)
            self.assertEqual(runtime["runtime"]["content_assets"], "pending_publish")
            run_json(CONTENT, "review", directory, "--asset-id", asset_id,
                     "--decision", "reject", "--reviewer", "主管A")
            with io.open(approved["approved_path"], "r", encoding="utf-8") as handle:
                self.assertEqual([line for line in handle if line.strip()], [])
            revoked = run_json(ROUTER, "写条朋友圈", "--workspace-root", directory)
            self.assertEqual(revoked["runtime"]["content_assets"], "unavailable")

    def test_frontline_can_record_but_cannot_approve(self):
        with tempfile.TemporaryDirectory() as directory:
            run_json(INIT, directory, "--role", "frontline")
            output_ref = "07_我的产出/07_内容行动工作台/04_私信承接/case-3.md"
            draft = run_json(
                CONTENT, "record", directory,
                "--content-type", "private_message", "--status", "draft",
                "--output-ref", output_ref,
            )
            recorded = run_json(
                CONTENT, "record", directory, "--asset-id", draft["asset_id"],
                "--content-type", "private_message", "--status", "sent",
                "--output-ref", output_ref,
                "--replied", "yes", "--reply-quality", "positive",
            )
            blocked = run_json(
                CONTENT, "review", directory, "--asset-id", recorded["asset_id"],
                "--decision", "approve", "--reviewer", "咨询师A",
                expected_codes=(3,),
            )
            self.assertEqual(blocked["status"], "manager_confirmation_required")

    def test_content_event_rejects_direct_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            run_json(INIT, directory)
            rejected = run_json(
                CONTENT, "record", directory,
                "--content-type", "followup", "--status", "draft",
                "--output-ref", "07_我的产出/07_内容行动工作台/01_跟进话术/case-4.md",
                "--note", "患者手机号13800138000",
                expected_codes=(2,),
            )
            self.assertEqual(rejected["status"], "rejected")
            self.assertEqual(rejected["reason"], "possible_personal_identifier")

    def test_legacy_folders_are_mapped_without_move_or_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "旧内容工作台"
            old_folders = (
                "机构基础与信任背书库", "项目病种知识库", "客户问题与异议库",
                "咨询转化方法论库", "标杆黑榜案例库", "销冠语料库",
                "合规与边界库", "朋友圈私域内容库",
            )
            for folder in old_folders:
                (source / folder).mkdir(parents=True)
            run_json(INIT, directory)
            mapped = run_json(
                MAPPER, directory, "--source-root", str(source),
                "--ima-folder", "朋友圈私域内容库",
            )
            self.assertEqual(mapped["mapping_count"], 9)
            self.assertFalse(mapped["moves_or_renames_source_files"])
            self.assertFalse(mapped["mapping_implies_approval"])
            self.assertTrue(all(item["review_status"] == "indexed_not_approved" for item in mapped["mappings"]))
            self.assertTrue(all((source / folder).is_dir() for folder in old_folders))


if __name__ == "__main__":
    unittest.main()
