#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the v1.10 visual asset compiler."""

import json
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECT = ROOT / "scripts" / "select_visual_asset.py"
FEEDBACK = ROOT / "scripts" / "record_visual_feedback.py"
CATALOG = ROOT / "references" / "visual-asset-catalog.json"


def run_json(script, *args):
    process = subprocess.Popen(
        [sys.executable, str(script)] + list(args), cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise AssertionError("command failed: {}\n{}\n{}".format(script, stdout.decode("utf-8"), stderr.decode("utf-8")))
    return json.loads(stdout.decode("utf-8"))


class VisualAssetTests(unittest.TestCase):
    def test_catalog_contains_all_excel_asset_labels(self):
        with io.open(str(CATALOG), "r", encoding="utf-8") as handle:
            catalog = json.load(handle)
        labels = {item["label"] for item in catalog["assets"]}
        for label in ("医院大楼图", "排队图（人气图）", "报告图", "药品图", "好评对话图", "挂号图（预约卡）", "活动倒计时图", "权威报道/时事热点图", "客情/情绪价值图", "组合运用、重复使用"):
            self.assertIn(label, labels)

    def test_selects_objection_qa_for_fee_barrier(self):
        result = run_json(SELECT, "患者说费用太贵，帮我做抗拒点QA图", "--channel", "chat")
        self.assertEqual(result["selected"]["asset_id"], "objection_qa")
        self.assertIn("price", result["inferred"]["barriers"])
        self.assertTrue(result["recommended_bundle"])

    def test_selects_appointment_card_for_arrival_request(self):
        result = run_json(SELECT, "患者已经愿意来，但想看挂号和到院安排", "--channel", "chat")
        self.assertEqual(result["selected"]["asset_id"], "appointment_card")
        self.assertIn("appointment", result["inferred"]["stages"])

    def test_explicit_asset_leads_the_bundle(self):
        result = run_json(SELECT, "帮我做活动倒计时图", "--asset-type", "countdown", "--channel", "moments")
        self.assertEqual(result["selected"]["asset_id"], "countdown")
        self.assertEqual(result["recommended_bundle"][0]["asset_id"], "countdown")

    def test_feedback_is_append_only_and_traceable(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_json(FEEDBACK, directory, "--asset-id", "queue_scene", "--channel", "moments", "--status", "sent", "--replied", "yes")
            path = Path(result["path"])
            self.assertTrue(path.is_file())
            with io.open(str(path), "r", encoding="utf-8") as handle:
                row = json.loads(handle.readline())
            self.assertEqual(row["asset_id"], "queue_scene")
            self.assertEqual(row["replied"], "yes")


if __name__ == "__main__":
    unittest.main()
