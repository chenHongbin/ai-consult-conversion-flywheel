#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Acceptance tests for the V2.1 daily review factory."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "scripts" / "init_consult_workspace.py"
NIGHTLY = ROOT / "scripts" / "run_nightly_cycle.py"
REVIEW = ROOT / "scripts" / "daily_review.py"
MIGRATE = ROOT / "scripts" / "migrate_workspace_v21.py"
ROUTER = ROOT / "scripts" / "route_consultation.py"


def run_json(script, *args, **kwargs):
    expected_codes = kwargs.get("expected_codes", (0,))
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


def load_json(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path, value):
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)


def analysis(gap="没有验证患者顾虑", strength="能够礼貌回应", risk="P1"):
    return {
        "summary": ["患者仍在比较", "主要担心效果不确定", "下一步先验证顾虑"],
        "material_quality": "complete",
        "stage": "顾虑处理",
        "patient_concern": "效果与信任",
        "breakpoint": gap,
        "patient_facts": ["患者明确说仍担心"],
        "consultant_actions": ["解释了项目"],
        "strengths": [strength],
        "verified_strength": strength,
        "missed_opportunities": ["没有追问具体担心什么"],
        "champion_comparison": ["先问一个验证问题，再引用已确认事实"],
        "next_service_action": "先确认患者最担心的具体结果",
        "safe_response_draft": "我先确认一下，您更担心的是过程还是结果的不确定？",
        "training_action": {
            "key_action": "先问一个顾虑验证问题",
            "pass_criteria": "连续两条同类新案例中先验证再解释",
            "review_scenario": "下一条效果顾虑案例",
        },
        "evidence": [{"locator": "文本第2行", "quote": "我还是担心效果"}],
        "risk_level": risk,
        "unknowns": ["预约结果未知"],
    }


class DailyReviewV21Tests(unittest.TestCase):
    def init_workspace(self, directory, members="A001_张宁"):
        return Path(run_json(INIT, directory, "--members", members)["workspace"])

    def write_case(self, workspace, member, name, text="患者：我还是担心效果"):
        path = workspace / "08_团队管理" / "01_成员" / member / "01_今天放这里" / name
        if not path.parent.is_dir():
            path.parent.mkdir(parents=True)
        with io.open(str(path), "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_new_workspace_uses_v21_additive_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            manifest = load_json(workspace / "_系统" / "工作区清单.json")
            profile = load_json(workspace / "_系统" / "来源配置.json")
            self.assertEqual(manifest["layout_version"], "v2.1.3")
            self.assertTrue((workspace / "_系统" / "每日复盘").is_dir())
            self.assertEqual(profile["daily_review"]["patient_grouping"], "suggest_then_manager_confirm")
            self.assertEqual(profile["daily_review"]["outcomes"], "unknown_until_observed")

    def test_router_exposes_daily_review_and_blocks_frontline_manager_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            manager = run_json(ROUTER, "分析今天全部咨询", "--workspace-root", directory)
            self.assertEqual(manager["route_id"], "daily_review_factory")
            self.assertEqual(manager["status"], "routed")
            frontline = run_json(ROUTER, "分析今天全部咨询", "--workspace-root", directory,
                                 "--role", "frontline")
            self.assertEqual(frontline["route_id"], "daily_review_factory")
            self.assertEqual(frontline["status"], "manager_confirmation_required")

    def test_v20_migration_is_dry_run_first_additive_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            manifest_path = workspace / "_系统" / "工作区清单.json"
            manifest = load_json(manifest_path)
            manifest["layout_version"] = "v2.0"
            save_json(manifest_path, manifest)
            marker = workspace / "旧资料保留.txt"
            with io.open(str(marker), "w", encoding="utf-8") as handle:
                handle.write("keep")
            samples = workspace / "_系统" / "管理工作台" / "communication-samples.jsonl"
            if not samples.parent.is_dir():
                samples.parent.mkdir(parents=True)
            with io.open(str(samples), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "sample_id": "S-OLD", "source_hash": "legacy-hash", "employee_id": "A001",
                    "date": "2026-08-19", "source": "旧录音.txt",
                }, ensure_ascii=False) + "\n")
            dry = run_json(MIGRATE, str(workspace))
            self.assertEqual(dry["status"], "dry_run")
            self.assertEqual(load_json(manifest_path)["layout_version"], "v2.0")
            first = run_json(MIGRATE, str(workspace), "--apply")
            second = run_json(MIGRATE, str(workspace), "--apply")
            self.assertEqual(first["status"], "migrated")
            self.assertEqual(second["result"]["legacy_samples_projected"], 1)
            self.assertEqual(load_json(manifest_path)["layout_version"], "v2.1.3")
            self.assertEqual(load_json(manifest_path)["migrated_from"], "v2.0")
            self.assertTrue(marker.is_file())
            projection = Path(second["result"]["projection"])
            with io.open(str(projection), "r", encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["conversation_id"].startswith("CONV-"))

    def test_patient_grouping_is_suggested_but_never_auto_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            self.write_case(workspace, "A001_张宁", "患者ID-P100/微信.txt", "患者：我担心效果")
            self.write_case(workspace, "A001_张宁", "患者ID-P100/电话.txt", "患者：我还担心恢复时间")
            run_json(NIGHTLY, str(workspace), "--date", "2026-08-19", "--force")
            tasks = [load_json(path) for path in sorted((workspace / "_系统" / "每日复盘" / "tasks").glob("*.json"))]
            self.assertEqual(len(tasks), 2)
            self.assertEqual({task["grouping_state"] for task in tasks}, {"suggested"})
            self.assertEqual(len({task["suggested_group_id"] for task in tasks}), 1)
            self.assertEqual(len({task["patient_case_id"] for task in tasks}), 2)
            suggestion = tasks[0]["suggested_group_id"]
            run_json(REVIEW, "group", str(workspace), "--suggestion-id", suggestion,
                     "--decision", "confirmed", "--reviewer", "主管林老师")
            confirmed = [load_json(path) for path in sorted((workspace / "_系统" / "每日复盘" / "tasks").glob("*.json"))]
            self.assertEqual({task["grouping_state"] for task in confirmed}, {"confirmed"})
            self.assertEqual(len({task["patient_case_id"] for task in confirmed}), 1)
            claimed = run_json(REVIEW, "claim", str(workspace), "--owner", "group-worker",
                               "--batch-size", "10", "--date", "2026-08-19")["tasks"]
            for index, task in enumerate(claimed):
                payload = Path(directory) / "grouped-{}.json".format(index)
                save_json(payload, analysis())
                run_json(REVIEW, "complete", str(workspace), "--task-id", task["analysis_task_id"],
                         "--analysis-json", str(payload), "--owner", "group-worker",
                         "--lease-token", task["lease_token"])
            projection = run_json(REVIEW, "aggregate", str(workspace), "--date", "2026-08-19")["projection"]
            self.assertEqual(projection["completed_count"], 2)
            self.assertEqual(projection["patient_bundle_report_count"], 1)
            self.assertEqual(projection["completed_patient_case_count"], 1)
            self.assertEqual(projection["employees"][0]["case_count"], 1)
            self.assertEqual(projection["employees"][0]["main_gap"]["count"], 1)

    def test_direct_upload_and_folder_upload_share_the_same_queue_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            direct = Path(directory) / "直接上传.txt"
            with io.open(str(direct), "w", encoding="utf-8") as handle:
                handle.write("患者：我想先了解一下")
            first = run_json(REVIEW, "register", str(workspace), "--source", str(direct),
                             "--employee-id", "A001", "--employee-name", "张宁",
                             "--date", "2026-08-19", "--medium", "text")
            second = run_json(REVIEW, "register", str(workspace), "--source", str(direct),
                              "--employee-id", "A001", "--employee-name", "张宁",
                              "--date", "2026-08-20", "--medium", "text")
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            task_id = first["task"]["analysis_task_id"]
            claimed = run_json(REVIEW, "claim", str(workspace), "--owner", "direct-worker",
                               "--task-id", task_id)["tasks"]
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0]["schema_version"], "2.1-analysis-task")
            self.assertEqual(claimed[0]["analysis_contract"], "2.1-case-report")

    def test_employee_one_on_one_recording_never_enters_patient_case_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            source = workspace / "08_团队管理" / "01_成员" / "A001_张宁" / "01_今天放这里" / "月度一对一沟通.m4a"
            with io.open(str(source), "wb") as handle:
                handle.write(b"synthetic employee conversation")
            result = run_json(NIGHTLY, str(workspace), "--date", "2026-08-19", "--force")
            self.assertEqual(result["v21_analysis_tasks_registered"], 0)
            self.assertEqual(list((workspace / "_系统" / "每日复盘" / "tasks").glob("*.json")), [])

    def test_claim_complete_and_daily_outputs_form_one_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            self.write_case(workspace, "A001_张宁", "微信咨询.txt")
            night = run_json(NIGHTLY, str(workspace), "--date", "2026-08-19", "--force")
            self.assertEqual(night["v21_analysis_tasks_registered"], 1)
            claimed = run_json(REVIEW, "claim", str(workspace), "--owner", "codex-test", "--date", "2026-08-19")
            self.assertEqual(len(claimed["tasks"]), 1)
            second = run_json(REVIEW, "claim", str(workspace), "--owner", "other", "--date", "2026-08-19")
            self.assertEqual(second["tasks"], [])
            task = claimed["tasks"][0]
            payload = Path(directory) / "analysis.json"
            save_json(payload, analysis())
            completed = run_json(REVIEW, "complete", str(workspace), "--task-id", task["analysis_task_id"],
                                 "--analysis-json", str(payload), "--owner", "codex-test",
                                 "--lease-token", task["lease_token"])
            report_path = workspace / completed["task"]["report_path"]
            self.assertTrue(report_path.is_file())
            projection = run_json(REVIEW, "aggregate", str(workspace), "--date", "2026-08-19")["projection"]
            self.assertEqual(projection["completed_count"], 1)
            self.assertEqual(projection["outcome_status"], "unknown")
            self.assertTrue((workspace / projection["team_report_path"]).is_file())
            self.assertTrue((workspace / projection["employees"][0]["report_path"]).is_file())
            self.assertIsNone(projection["employees"][0]["training"])
            with io.open(str(report_path), "r", encoding="utf-8") as handle:
                self.assertIn("本次只训练一个动作", handle.read())

    def test_team_breakpoint_requires_two_employees_and_three_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory, "A001_张宁,A002_李明")
            for member in ("A001_张宁", "A002_李明"):
                for index in range(2):
                    self.write_case(workspace, member, "咨询-{}.txt".format(index),
                                    "{} 第{}条：患者担心效果".format(member, index))
            run_json(NIGHTLY, str(workspace), "--date", "2026-08-19", "--force")
            claimed = run_json(REVIEW, "claim", str(workspace), "--owner", "codex-test", "--batch-size", "10",
                               "--date", "2026-08-19")["tasks"]
            self.assertEqual(len(claimed), 4)
            for index, task in enumerate(claimed):
                payload = Path(directory) / "analysis-{}.json".format(index)
                save_json(payload, analysis())
                run_json(REVIEW, "complete", str(workspace), "--task-id", task["analysis_task_id"],
                         "--analysis-json", str(payload), "--owner", "codex-test",
                         "--lease-token", task["lease_token"])
            projection = run_json(REVIEW, "aggregate", str(workspace), "--date", "2026-08-19")["projection"]
            self.assertEqual(projection["team_breakpoint"], "没有验证患者顾虑")
            self.assertEqual(len(projection["employees"]), 2)
            for employee in projection["employees"]:
                self.assertEqual(employee["main_gap"]["status"], "observation")

    def test_one_active_training_is_rechecked_until_behavior_is_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            for index in range(3):
                self.write_case(workspace, "A001_张宁", "第一天-{}.txt".format(index),
                                "第一天案例{}：患者担心效果".format(index))
            run_json(NIGHTLY, str(workspace), "--date", "2026-08-18", "--force")
            first_tasks = run_json(REVIEW, "claim", str(workspace), "--owner", "coach", "--batch-size", "10",
                                   "--date", "2026-08-18")["tasks"]
            for index, first_task in enumerate(first_tasks):
                first_payload = Path(directory) / "day1-{}.json".format(index)
                save_json(first_payload, analysis())
                run_json(REVIEW, "complete", str(workspace), "--task-id", first_task["analysis_task_id"],
                         "--analysis-json", str(first_payload), "--owner", "coach",
                         "--lease-token", first_task["lease_token"])
            day1 = run_json(REVIEW, "aggregate", str(workspace), "--date", "2026-08-18")["projection"]
            action_id = day1["employees"][0]["training"]["action_id"]

            self.write_case(workspace, "A001_张宁", "第二天-1.txt", "第二天案例1：患者担心效果")
            self.write_case(workspace, "A001_张宁", "第二天-2.txt", "第二天案例2：患者担心效果")
            run_json(NIGHTLY, str(workspace), "--date", "2026-08-19", "--force")
            day2_tasks = run_json(REVIEW, "claim", str(workspace), "--owner", "coach", "--batch-size", "10",
                                  "--date", "2026-08-19")["tasks"]
            self.assertEqual(len(day2_tasks), 2)
            self.assertEqual({task["current_training"]["action_id"] for task in day2_tasks}, {action_id})
            for index, task in enumerate(day2_tasks):
                payload = analysis()
                payload["training_followup"] = {
                    "target_action_observed": True,
                    "evidence_locator": "文本第3行",
                    "note": "先验证顾虑再解释",
                }
                path = Path(directory) / "day2-{}.json".format(index)
                save_json(path, payload)
                run_json(REVIEW, "complete", str(workspace), "--task-id", task["analysis_task_id"],
                         "--analysis-json", str(path), "--owner", "coach",
                         "--lease-token", task["lease_token"])
            day2 = run_json(REVIEW, "aggregate", str(workspace), "--date", "2026-08-19")["projection"]
            self.assertEqual(day2["employees"][0]["training"]["action_id"], action_id)
            training_path = workspace / "_系统" / "管理工作台" / "training-actions.jsonl"
            with io.open(str(training_path), "r", encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            latest = [row for row in rows if row["action_id"] == action_id][-1]
            self.assertEqual(latest["behavior_status"], "stable")
            self.assertEqual(latest["status"], "passed")
            self.assertEqual(latest["observed_case_count"], 2)
            self.assertEqual(len({row["action_id"] for row in rows}), 1)

    def test_failed_task_stops_after_initial_attempt_plus_two_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            self.write_case(workspace, "A001_张宁", "失败重试.txt")
            run_json(NIGHTLY, str(workspace), "--date", "2026-08-19", "--force")
            last_task = None
            for attempt in range(3):
                claimed = run_json(REVIEW, "claim", str(workspace), "--owner", "worker",
                                   "--date", "2026-08-19")["tasks"]
                self.assertEqual(len(claimed), 1)
                last_task = claimed[0]
                failed = run_json(REVIEW, "fail", str(workspace), "--task-id", last_task["analysis_task_id"],
                                  "--reason", "识别失败", "--owner", "worker",
                                  "--lease-token", last_task["lease_token"])["task"]
                self.assertEqual(failed["attempts"], attempt + 1)
            self.assertFalse(failed["retryable"])
            final_claim = run_json(REVIEW, "claim", str(workspace), "--owner", "worker",
                                   "--date", "2026-08-19")["tasks"]
            self.assertEqual(final_claim, [])

    def test_p0_deep_analysis_cannot_be_cancelled_by_manager_override(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            self.write_case(workspace, "A001_张宁", "风险案例.txt", "咨询师：保证一定治好")
            run_json(NIGHTLY, str(workspace), "--date", "2026-08-19", "--force")
            task = run_json(REVIEW, "claim", str(workspace), "--owner", "risk-worker",
                            "--date", "2026-08-19")["tasks"][0]
            run_json(REVIEW, "prioritize", str(workspace), "--task-id", task["analysis_task_id"],
                     "--deep", "no", "--reviewer", "主管")
            payload = Path(directory) / "risk.json"
            risk_analysis = analysis(risk="P0")
            save_json(payload, risk_analysis)
            rejected = run_json(REVIEW, "complete", str(workspace), "--task-id", task["analysis_task_id"],
                                "--analysis-json", str(payload), "--owner", "risk-worker",
                                "--lease-token", task["lease_token"], expected_codes=(2,))
            self.assertEqual(rejected["status"], "error")
            self.assertIn("deep_analysis", rejected["message"])
            risk_analysis["deep_analysis"] = True
            save_json(payload, risk_analysis)
            completed = run_json(REVIEW, "complete", str(workspace), "--task-id", task["analysis_task_id"],
                                 "--analysis-json", str(payload), "--owner", "risk-worker",
                                 "--lease-token", task["lease_token"])["task"]
            report = load_json(workspace / completed["analysis_json"])
            self.assertIn("P0_risk", report["deep_analysis_reasons"])

    def test_audit_repairs_completed_task_with_missing_report_without_touching_source(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            source = self.write_case(workspace, "A001_张宁", "待修复.txt", "患者：我还在考虑")
            run_json(NIGHTLY, str(workspace), "--date", "2026-08-19", "--force")
            task = run_json(REVIEW, "claim", str(workspace), "--owner", "audit-worker",
                            "--date", "2026-08-19")["tasks"][0]
            payload = Path(directory) / "audit.json"
            save_json(payload, analysis())
            completed = run_json(REVIEW, "complete", str(workspace), "--task-id", task["analysis_task_id"],
                                 "--analysis-json", str(payload), "--owner", "audit-worker",
                                 "--lease-token", task["lease_token"])["task"]
            (workspace / completed["report_path"]).unlink()
            audit = run_json(REVIEW, "audit", str(workspace))
            self.assertEqual(audit["status"], "issues_found")
            self.assertEqual(audit["issues"][0]["type"], "completed_artifact_missing")
            repaired = run_json(REVIEW, "audit", str(workspace), "--repair")
            self.assertEqual(repaired["status"], "repaired")
            task_state = load_json(workspace / "_系统" / "每日复盘" / "tasks" /
                                   (task["analysis_task_id"] + ".json"))
            self.assertEqual(task_state["status"], "prepared")
            self.assertTrue(source.is_file())

    def test_case_report_rejects_unredacted_personal_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            self.write_case(workspace, "A001_张宁", "隐私案例.txt", "患者留下了联系方式")
            run_json(NIGHTLY, str(workspace), "--date", "2026-08-19", "--force")
            task = run_json(REVIEW, "claim", str(workspace), "--owner", "privacy-worker",
                            "--date", "2026-08-19")["tasks"][0]
            payload = analysis()
            payload["evidence"][0]["quote"] = "我的手机号是13812345678"
            path = Path(directory) / "privacy.json"
            save_json(path, payload)
            result = run_json(REVIEW, "complete", str(workspace), "--task-id", task["analysis_task_id"],
                              "--analysis-json", str(path), "--owner", "privacy-worker",
                              "--lease-token", task["lease_token"], expected_codes=(2,))
            self.assertEqual(result["status"], "error")
            self.assertIn("personal identifier", result["message"])

    def test_scale_50_employees_500_cases_is_resumable_and_lossless(self):
        with tempfile.TemporaryDirectory() as directory:
            members = ["A{:03d}_员工{:02d}".format(index, index) for index in range(1, 51)]
            workspace = self.init_workspace(directory, ",".join(members))
            for member in members:
                for index in range(10):
                    self.write_case(workspace, member, "患者-{:02d}.txt".format(index),
                                    "{} 患者{}：我需要再考虑一下".format(member, index))
            first = run_json(NIGHTLY, str(workspace), "--date", "2026-08-19", "--force")
            self.assertEqual(first["v21_analysis_tasks_registered"], 500)
            sys.path.insert(0, str(ROOT / "scripts"))
            import daily_review
            completed = 0
            while True:
                tasks = daily_review.claim_tasks(str(workspace), "scale-test", 20, 30, "2026-08-19")
                if not tasks:
                    break
                for task in tasks:
                    daily_review.commit_analysis(str(workspace), task["analysis_task_id"], analysis(),
                                                 "scale-test", task["lease_token"])
                    completed += 1
            self.assertEqual(completed, 500)
            status = daily_review.queue_status(str(workspace), "2026-08-19")
            self.assertEqual(status["status_counts"].get("completed"), 500)
            projection = daily_review.aggregate_daily(str(workspace), "2026-08-19")
            self.assertEqual(projection["completed_count"], 500)
            self.assertEqual(len(projection["employees"]), 50)
            second = run_json(NIGHTLY, str(workspace), "--date", "2026-08-19", "--force")
            self.assertEqual(second["tasks_created"], 0)
            self.assertEqual(daily_review.queue_status(str(workspace), "2026-08-19")["total"], 500)


if __name__ == "__main__":
    unittest.main()
