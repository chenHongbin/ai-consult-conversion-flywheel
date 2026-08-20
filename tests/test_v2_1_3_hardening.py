#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adversarial acceptance tests for the V2.1.3 trusted release boundary."""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
INIT = SCRIPTS / "init_consult_workspace.py"
ROUTER = SCRIPTS / "route_consultation.py"
REVIEW = SCRIPTS / "daily_review.py"
PUBLISH = SCRIPTS / "publish_release.py"
LOADER = SCRIPTS / "load_active_capability.py"
BUILD_BASE = SCRIPTS / "build_base_skill_package.py"
SOURCE_ARTIFACTS = SCRIPTS / "source_artifacts.py"
REVIEW_CANDIDATE = SCRIPTS / "review_candidate.py"
COMMIT_CAPABILITY = SCRIPTS / "commit_capability_candidate.py"
MIGRATE = SCRIPTS / "migrate_workspace_v21.py"
OCR = SCRIPTS / "ocr_long_images.py"


def run_json(script, *args, **kwargs):
    expected = kwargs.pop("expected", (0,))
    process = subprocess.Popen([sys.executable, str(script)] + [str(value) for value in args],
                               cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    if process.returncode not in expected:
        raise AssertionError("command failed: {0}\n{1}\n{2}".format(
            script, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")))
    return json.loads(stdout.decode("utf-8"))


def save_json(path, value):
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)


def load_json(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_text(path, value):
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        handle.write(value)


def analysis():
    return {
        "summary": ["患者仍在比较", "高意向停滞", "先验证顾虑"],
        "material_quality": "complete", "stage": "顾虑处理", "patient_concern": "效果与信任",
        "breakpoint": "没有验证顾虑", "patient_facts": ["患者正在比较"],
        "consultant_actions": ["介绍了项目"], "strengths": ["回复及时"], "verified_strength": "回复及时",
        "missed_opportunities": ["没有追问"], "champion_comparison": ["先问再解释"],
        "next_service_action": "今日回访并验证顾虑",
        "safe_response_draft": "我先确认一下，您最担心哪个方面？",
        "training_action": {"key_action": "先问一个验证问题", "pass_criteria": "连续两例出现"},
        "evidence": [{"locator": "文本第2行", "quote": "我再比较一下"}],
        "risk_level": "P1", "unknowns": ["结果未知"], "case_signals": ["high_intent_stalled"],
        "deep_analysis": {"reason": "high_intent_stalled"},
    }


class HardeningV213Tests(unittest.TestCase):
    def init_workspace(self, directory, members="A001_张三"):
        return Path(run_json(INIT, directory, "--role", "manager", "--members", members)["workspace"])

    def activate_components(self, workspace):
        scope = {"institution": "合成医院", "department": "皮肤科", "channel": "通用"}
        specs = (
            ("当前能力包", "package.json", "runtime-context.md", {"version": "v0.1", "scope": scope, "rules": []}),
            ("当前机构知识", "knowledge.json", "knowledge-runtime.md", {"version": "v0.1", "scope": scope, "facts": []}),
            ("患者洞察", "patient-insights.json", "patient-insights-runtime.md", {"version": "v0.1", "scope": scope,
                                                        "decision_states": [], "doubt_intents": [], "practice_scenarios": []}),
        )
        for folder, package_name, runtime_name, package in specs:
            version_dir = workspace / "_系统" / folder / "versions" / "v0.1"
            package_path = version_dir / package_name
            runtime_path = version_dir / runtime_name
            save_json(package_path, package)
            write_text(runtime_path, "# 合成运行时\n")
            save_json(workspace / "_系统" / folder / "active.json", {
                "status": "active", "active_version": "v0.1", "package_path": str(package_path),
                "runtime_context_path": str(runtime_path), "scope": scope,
            })

    def test_natural_routes_and_composite_actions(self):
        self.assertEqual(run_json(ROUTER, "给患者写个费用跟进话术")["route_id"], "content_action")
        composite = run_json(ROUTER, "分析今天全部咨询并生成跟进文案")
        self.assertEqual(composite["route_id"], "daily_review_factory")
        self.assertEqual(composite["downstream_routes"], ["content_action"])
        self.assertEqual(run_json(ROUTER, "查看张三咨询师今天的情况")["route_id"], "employee_review")

    def test_release_runtime_tamper_enters_safe_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            self.activate_components(workspace)
            run_json(PUBLISH, workspace, "--version", "Team-v2.1.3")
            self.assertNotEqual(run_json(LOADER, workspace)["status"], "safe_mode")
            runtime = workspace / "_系统" / "当前能力包" / "versions" / "v0.1" / "runtime-context.md"
            write_text(runtime, "# 发布后被篡改\n")
            result = run_json(LOADER, workspace)
            self.assertEqual(result["status"], "safe_mode")
            self.assertIn("component_not_bound_to_release", result["reason"])

    def test_approval_is_independent_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            candidate = Path(directory) / "candidate.json"
            save_json(candidate, {
                "scope": {"institution": "合成医院", "department": "皮肤科"},
                "delta": {"rules_upsert": [{"id": "R1", "name": "先验证", "when": "出现顾虑",
                                               "do": "追问", "evidence_refs": ["synthetic-case-1"]}]},
            })
            blocked = run_json(COMMIT_CAPABILITY, workspace, candidate, "--publish", expected=(2,))
            self.assertIn("--approval-id", " ".join(blocked["errors"]))
            receipt = run_json(REVIEW_CANDIDATE, workspace, "--component", "capability",
                               "--candidate", candidate, "--reviewer", "主管A",
                               "--check", "evaluation", "--check", "coverage")["approval"]
            value = load_json(candidate)
            value["delta"]["rules_upsert"][0]["do"] = "篡改后的动作"
            save_json(candidate, value)
            changed = run_json(COMMIT_CAPABILITY, workspace, candidate, "--publish",
                               "--approval-id", receipt["approval_id"], expected=(2,))
            self.assertIn("candidate changed after approval", " ".join(changed["errors"]))

    def test_completed_task_cannot_be_failed_and_priority_is_patient_level(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            source = Path(directory) / "case.txt"
            write_text(source, "患者正在比较")
            registered = run_json(REVIEW, "register", workspace, "--source", source,
                                  "--employee-id", "A001", "--employee-name", "张三",
                                  "--date", "2026-08-20", "--medium", "text")["task"]
            task = run_json(REVIEW, "claim", workspace, "--owner", "worker",
                            "--task-id", registered["analysis_task_id"])["tasks"][0]
            payload = Path(directory) / "analysis.json"
            save_json(payload, analysis())
            run_json(REVIEW, "complete", workspace, "--task-id", task["analysis_task_id"],
                     "--analysis-json", payload, "--owner", "worker", "--lease-token", task["lease_token"])
            failed = run_json(REVIEW, "fail", workspace, "--task-id", task["analysis_task_id"],
                              "--reason", "late failure", "--owner", "worker",
                              "--lease-token", task["lease_token"], expected=(2,))
            self.assertIn("terminal", failed["message"])
            projection = run_json(REVIEW, "aggregate", workspace, "--date", "2026-08-20")["projection"]
            self.assertEqual(projection["patient_priorities"][0]["reason_id"], "high_intent_stalled")
            self.assertEqual(projection["patient_priorities"][0]["employee_id"], "A001")

    def test_local_and_ima_share_content_hash_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            text_path = workspace / "_系统" / "IMA同步" / "cache" / "same.txt"
            write_text(text_path, "同一份已脱敏咨询文本")
            inventory = workspace / "_系统" / "资料索引" / "workspace-inventory.jsonl"
            write_text(inventory, json.dumps({
                "source_id": "local-1", "material_type": "audio", "source_hash": "raw-hash",
                "source_path": "local.wav", "derived_text_paths": [str(text_path)], "derived_status": "available",
            }, ensure_ascii=False) + "\n")
            cache = workspace / "_系统" / "IMA同步" / "cache-index.jsonl"
            write_text(cache, json.dumps({"source_id": "ima-1", "media_id": "M1", "cache_path": str(text_path)},
                                         ensure_ascii=False) + "\n")
            result = run_json(SOURCE_ARTIFACTS, workspace)
            self.assertEqual(result["unique_content"], 1)
            with io.open(result["output"], "r", encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["cross_source_duplicate"] for row in rows))
            self.assertEqual(rows[0]["source_types_in_group"], ["ima", "local"])

    def test_public_package_uses_exact_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            allowlist = load_json(ROOT / "references" / "public-package-allowlist.json")["files"]
            for relative in allowlist:
                target = source / relative
                if not target.parent.is_dir():
                    target.parent.mkdir(parents=True)
                shutil.copy2(str(ROOT / relative), str(target))
            write_text(source / "患者原始聊天.txt", "患者手机号138 0013 8000")
            built = run_json(BUILD_BASE, "--source-root", source, "--output-dir", directory)
            with zipfile.ZipFile(built["package"], "r") as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("package-manifest.json").decode("utf-8"))
            self.assertNotIn("患者原始聊天.txt", names)
            self.assertTrue(manifest["privacy_scan_passed"])
            self.assertFalse(manifest["contains_raw_patient_material"])

    def test_real_v20_fixture_migrates_to_complete_v213_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "咨询转化工作区"
            save_json(workspace / "_系统" / "工作区清单.json", {"layout_version": "v2.0"})
            run_json(MIGRATE, workspace, "--apply")
            manifest = load_json(workspace / "_系统" / "工作区清单.json")
            self.assertEqual(manifest["layout_version"], "v2.1.3")
            self.assertTrue((workspace / "_系统" / "审核账本" / "approvals").is_dir())
            self.assertTrue((workspace / "07_我的产出" / "07_内容行动工作台" / "04_素材库").is_dir())

    def test_long_screenshot_dependencies_and_e2e(self):
        check = run_json(OCR, "--check", expected=(0, 2))
        if check["status"] != "ready" or not shutil.which("ffmpeg"):
            self.skipTest("host does not provide the local long-image OCR stack")
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "long.png"
            process = subprocess.Popen([
                shutil.which("ffmpeg"), "-loglevel", "error", "-f", "lavfi", "-i",
                "color=c=white:s=800x3200", "-frames:v", "1", "-y", str(image),
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _, stderr = process.communicate()
            self.assertEqual(process.returncode, 0, stderr.decode("utf-8", "replace"))
            result = run_json(OCR, image, "--output-dir", Path(directory) / "ocr")
            self.assertGreaterEqual(result["ocr_ok"], 2)
            self.assertEqual(result["failures"], 0)


if __name__ == "__main__":
    unittest.main()
