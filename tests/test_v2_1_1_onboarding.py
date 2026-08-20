#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Behavioral tests for the V2.1.1 embedded onboarding workflow."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ONBOARDING = ROOT / "scripts" / "onboarding.py"
ROUTER = ROOT / "scripts" / "route_consultation.py"


def run_json(script, *args, **kwargs):
    expected = kwargs.pop("expected", (0,))
    process = subprocess.Popen(
        [sys.executable, str(script)] + [str(item) for item in args],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate()
    stdout = stdout.decode("utf-8", "replace")
    stderr = stderr.decode("utf-8", "replace")
    if process.returncode not in expected:
        raise AssertionError("command failed: {0}\n{1}\n{2}".format(script, stdout, stderr))
    return json.loads(stdout)


def save_json(path, value):
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def write_text(path, value):
    with io.open(str(path), "w", encoding="utf-8") as handle:
        handle.write(value)


def read_text(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return handle.read()


def analysis():
    return {
        "summary": ["患者仍在了解阶段", "担心效果", "先验证具体担忧"],
        "material_quality": "complete",
        "stage": "需求确认",
        "patient_concern": "担心效果",
        "breakpoint": "没有验证患者顾虑",
        "patient_facts": ["患者明确询问效果"],
        "consultant_actions": ["直接解释项目"],
        "strengths": ["及时回应"],
        "verified_strength": "及时回应",
        "missed_opportunities": ["没有追问具体担忧"],
        "champion_comparison": ["先验证担忧来源，再给适用信息"],
        "next_service_action": "先问患者最担心哪一部分",
        "safe_response_draft": "我先了解一下，您主要担心的是过程、恢复还是效果的不确定性？",
        "training_action": {
            "key_action": "解释前先验证顾虑",
            "pass_criteria": "先提出一个验证问题，再给信息",
            "review_scenario": "下一条效果顾虑咨询",
        },
        "evidence": [{"locator": "文本第1行", "quote": "我担心效果", "claim": "效果顾虑"}],
        "risk_level": "P2",
        "case_signals": [],
        "unknowns": ["真实到院结果"],
        "deep_analysis": False,
    }


class OnboardingV211Tests(unittest.TestCase):
    def configure_manager(self, directory):
        return run_json(
            ONBOARDING, "configure", directory,
            "--role", "manager", "--institution", "测试医院", "--department", "口腔",
            "--manager-name", "林主管", "--members", "张三,李四",
            "--projects", "种植,正畸", "--channels", "信息流,转介绍",
            "--schedule", "21:45", "--host", "workbuddy",
        )

    def test_onboarding_route_is_shared_and_high_confidence(self):
        result = run_json(ROUTER, "初始化我的咨询助手", "--role", "frontline")
        self.assertEqual(result["route_id"], "onboarding_setup")
        self.assertEqual(result["status"], "routed")
        self.assertEqual(result["confidence"], "high")

    def test_v221_new_local_folder_uses_deterministic_five_question_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            started = run_json(ONBOARDING, "interview-start", directory, "--host", "younavi")
            self.assertEqual(started["core_version"], "2.2.1")
            self.assertEqual(started["step"], "role")
            self.assertEqual(started["question"], "你主要是自己做患者咨询，还是管理一个咨询团队？")
            self.assertEqual(Path(started["planned_workspace"]), Path(directory) / "咨询转化工作区")
            self.assertFalse((Path(directory) / "咨询转化工作区").exists())
            skipped = run_json(
                ONBOARDING, "interview-answer", directory,
                "--step", "organization", "--institution", "测试医院", "--department", "口腔",
                expected=(2,),
            )
            self.assertEqual(skipped["status"], "error")
            role = run_json(ONBOARDING, "interview-answer", directory, "--step", "role", "--role", "manager")
            self.assertEqual(role["step"], "organization")
            organization = run_json(
                ONBOARDING, "interview-answer", directory, "--step", "organization",
                "--institution", "测试医院", "--department", "口腔", "--projects", "种植",
            )
            self.assertEqual(organization["step"], "daily_work")
            daily = run_json(
                ONBOARDING, "interview-answer", directory, "--step", "daily_work",
                "--daily-work", "看微信聊天,辅导员工", "--current-problem", "无法每天覆盖全部咨询",
            )
            self.assertEqual(daily["step"], "identity")
            count_only = run_json(
                ONBOARDING, "interview-answer", directory, "--step", "identity",
                "--manager-name", "林主管", "--members", "10个咨询师", expected=(2,),
            )
            self.assertEqual(count_only["status"], "error")
            identity = run_json(
                ONBOARDING, "interview-answer", directory, "--step", "identity",
                "--manager-name", "林主管", "--members", "张三,李四",
            )
            self.assertEqual(identity["step"], "first_case")
            final_answer = run_json(
                ONBOARDING, "interview-answer", directory, "--step", "first_case", "--first-case", "defer",
            )
            self.assertEqual(final_answer["status"], "awaiting_confirmation")
            self.assertEqual(final_answer["summary"]["members"], ["A001_张三", "A002_李四"])
            self.assertEqual(final_answer["summary"]["workspace_location"], str(Path(directory) / "咨询转化工作区"))
            confirmed = run_json(
                ONBOARDING, "interview-confirm", directory, "--confirmation", "confirm",
            )
            workspace = Path(confirmed["workspace"])
            self.assertTrue(confirmed["workspace_verified"])
            self.assertTrue((workspace / "_系统" / "工作区清单.json").is_file())
            self.assertTrue((workspace / "_系统" / "首次设置" / "confirmed-profile.json").is_file())
            workspace_readme = read_text(workspace / "README_先看这里.md")
            self.assertNotIn("选择使用本地文件夹、IMA", workspace_readme)
            self.assertIn("先上传一条真实微信、录音或文本", workspace_readme)
            self.assertEqual(run_json(ONBOARDING, "status", workspace)["first_case_status"], "deferred")

    def test_v221_existing_workspace_only_asks_for_missing_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = self.configure_manager(directory)
            workspace = Path(configured["workspace"])
            resumed = run_json(ONBOARDING, "interview-start", workspace, "--host", "younavi")
            self.assertTrue(resumed["existing_workspace"])
            self.assertEqual(resumed["step"], "daily_work")
            self.assertNotEqual(resumed["step"], "role")

    def test_team_count_cannot_be_committed_as_a_fake_member(self):
        with tempfile.TemporaryDirectory() as directory:
            rejected = run_json(
                ONBOARDING, "configure", directory,
                "--role", "manager", "--institution", "测试医院", "--department", "口腔",
                "--members", "10个咨询师", expected=(2,),
            )
            self.assertEqual(rejected["status"], "error")
            self.assertIn("人数不能代替成员名单", rejected["message"])

    def test_new_folder_already_named_workspace_is_not_nested(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "咨询转化工作区"
            selected.mkdir()
            started = run_json(ONBOARDING, "interview-start", selected, "--host", "younavi")
            self.assertEqual(Path(started["planned_workspace"]), selected)
            self.assertEqual(started["location_mode"], "use_selected_workspace")

    def test_v221_zero_workspace_can_interview_then_choose_temporary_or_create(self):
        started = run_json(ONBOARDING, "interview-start", "--host", "workbuddy")
        session_id = started["session_id"]
        self.assertTrue(session_id)
        self.assertEqual(started["location_mode"], "pending_location")
        self.assertEqual(started["step"], "role")
        run_json(ONBOARDING, "interview-answer", "--session-id", session_id,
                 "--step", "role", "--role", "manager")
        run_json(ONBOARDING, "interview-answer", "--session-id", session_id,
                 "--step", "organization", "--institution", "零空间测试医院", "--department", "皮肤科")
        run_json(ONBOARDING, "interview-answer", "--session-id", session_id,
                 "--step", "daily_work", "--daily-work", "看微信聊天", "--current-problem", "覆盖不全")
        run_json(ONBOARDING, "interview-answer", "--session-id", session_id,
                 "--step", "identity", "--manager-name", "测试主管", "--members", "合成成员")
        summary = run_json(ONBOARDING, "interview-answer", "--session-id", session_id,
                           "--step", "first_case", "--first-case", "ready")
        self.assertEqual(summary["location_mode"], "recommended_default")
        self.assertIn("AI咨询转化飞轮", summary["summary"]["workspace_location"])
        temporary = run_json(ONBOARDING, "interview-confirm", "--session-id", session_id,
                             "--confirmation", "temporary")
        self.assertEqual(temporary["status"], "temporary_mode")
        self.assertFalse(temporary["workspace_created"])
        self.assertIn("每日全量复盘", temporary["cannot_do"])
        with tempfile.TemporaryDirectory() as directory:
            custom_parent = Path(directory) / "自动创建的机构目录"
            confirmed = run_json(
                ONBOARDING, "interview-confirm", "--session-id", session_id,
                "--confirmation", "confirm", "--workspace-root", custom_parent,
            )
            self.assertTrue(confirmed["workspace_verified"])
            self.assertEqual(Path(confirmed["workspace"]), custom_parent / "咨询转化工作区")
            self.assertTrue((Path(confirmed["workspace"]) / "_系统" / "首次设置" / "interview-state.json").is_file())

    def test_confirmed_interview_creates_canonical_manager_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.configure_manager(directory)
            workspace = Path(result["workspace"])
            self.assertEqual(result["onboarding"]["stage"], "awaiting_first_case")
            self.assertEqual([row["folder"] for row in result["profile"]["members"]],
                             ["A001_张三", "A002_李四"])
            self.assertTrue((workspace / "08_团队管理" / "01_成员" / "A001_张三" / "01_今天放这里").is_dir())
            self.assertTrue((workspace / "_系统" / "首次设置" / "confirmed-profile.json").is_file())
            source = json.loads(read_text(workspace / "_系统" / "来源配置.json"))
            self.assertEqual(source["institution"], "测试医院")
            self.assertFalse(source["automation"]["enabled"])
            self.assertEqual(source["automation"]["authorization_status"], "not_authorized")
            status = run_json(ONBOARDING, "status", directory)
            self.assertEqual(status["front_doors"], [])
            repeated = run_json(
                ONBOARDING, "configure", workspace,
                "--role", "manager", "--institution", "测试医院", "--department", "口腔",
                "--manager-name", "林主管", "--members", "A001_张三,A002_李四",
                "--projects", "种植,正畸", "--channels", "信息流,转介绍",
                "--schedule", "21:45", "--host", "workbuddy",
            )
            self.assertEqual(Path(repeated["workspace"]), workspace)
            self.assertFalse((workspace / "咨询转化工作区").exists())

    def test_frontline_interview_creates_only_personal_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_json(
                ONBOARDING, "configure", directory,
                "--role", "frontline", "--institution", "测试医院", "--department", "皮肤科",
                "--employee-name", "王芳", "--employee-id", "C008", "--host", "manual",
            )
            workspace = Path(result["workspace"])
            self.assertEqual(result["profile"]["role"], "frontline")
            self.assertEqual(result["profile"]["members"][0]["folder"], "C008_王芳")
            self.assertTrue((workspace / "08_团队管理" / "01_成员" / "C008_王芳" / "01_今天放这里").is_dir())
            run_json(ONBOARDING, "defer-first-case", workspace)
            run_json(ONBOARDING, "manual-mode", workspace)
            completed = run_json(ONBOARDING, "complete", workspace)
            self.assertEqual(completed["front_doors"], ["分析这一条", "生成下一步内容", "帮我回复", "安排回访", "陪我练一遍"])

    def test_first_case_and_real_automation_receipt_unlock_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = self.configure_manager(directory)
            workspace = Path(configured["workspace"])
            source = Path(directory) / "首条咨询.txt"
            write_text(source, "患者：我担心效果\n咨询师：我们这里项目很好")
            registered = run_json(
                ONBOARDING, "register-first-case", workspace,
                "--source", source, "--date", "2026-08-20", "--medium", "text",
            )
            sys.path.insert(0, str(ROOT / "scripts"))
            import daily_review
            tasks = daily_review.claim_tasks(str(workspace), "onboarding-test", 1, 30, "2026-08-20")
            self.assertEqual(len(tasks), 1)
            daily_review.commit_analysis(str(workspace), tasks[0]["analysis_task_id"], analysis(),
                                         "onboarding-test", tasks[0]["lease_token"])
            verified_case = run_json(
                ONBOARDING, "verify-first-case", workspace,
                "--task-id", registered["task"]["analysis_task_id"], "--judgment", "accepted",
            )
            self.assertEqual(verified_case["status"], "completed")
            request = run_json(ONBOARDING, "request-automation", workspace,
                               "--schedule", "21:45", "--host", "workbuddy")["request"]
            receipt = Path(directory) / "receipt.json"
            save_json(receipt, {
                "id": "automation-123", "status": "ACTIVE", "rrule": request["rrule"],
                "cwds": [str(workspace)],
            })
            verified = run_json(ONBOARDING, "verify-automation", workspace, "--receipt", receipt)
            self.assertEqual(verified["status"], "verified")
            completed = run_json(ONBOARDING, "complete", workspace)
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["front_doors"],
                             ["分析这一条", "分析今天全部咨询", "查看今天重点", "查看某个咨询师"])

    def test_fake_automation_receipt_cannot_enable_automation(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = self.configure_manager(directory)
            workspace = Path(configured["workspace"])
            run_json(ONBOARDING, "defer-first-case", workspace)
            run_json(ONBOARDING, "request-automation", workspace, "--host", "workbuddy")
            receipt = Path(directory) / "bad-receipt.json"
            save_json(receipt, {"status": "ACTIVE"})
            result = run_json(ONBOARDING, "verify-automation", workspace,
                              "--receipt", receipt, expected=(2,))
            self.assertEqual(result["status"], "error")
            config = json.loads(read_text(workspace / "_系统" / "自动化配置.json"))
            self.assertFalse(config["enabled"])
            self.assertEqual(config["authorization_status"], "requested")

    def test_manual_mode_and_safe_workbuddy_projection_are_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = self.configure_manager(directory)
            workspace = Path(configured["workspace"])
            run_json(ONBOARDING, "defer-first-case", workspace)
            run_json(ONBOARDING, "manual-mode", workspace)
            completed = run_json(ONBOARDING, "complete", workspace)
            self.assertEqual(completed["automation_status"], "manual_mode")
            user_file = Path(directory) / "USER.md"
            memory_file = Path(directory) / "MEMORY.md"
            write_text(user_file, "# 原有用户配置\n")
            write_text(memory_file, "# 原有长期记忆\n")
            preview = run_json(ONBOARDING, "project-workbuddy", workspace,
                               "--user-file", user_file, "--memory-file", memory_file)
            self.assertEqual(preview["status"], "preview")
            self.assertNotIn("AI_CONSULT_ONBOARDING_START", read_text(user_file))
            applied = run_json(ONBOARDING, "project-workbuddy", workspace,
                               "--user-file", user_file, "--memory-file", memory_file, "--apply")
            self.assertEqual(applied["status"], "applied")
            self.assertIn("AI_CONSULT_ONBOARDING_START", read_text(user_file))
            self.assertIn("# 原有用户配置", read_text(user_file))
            backups = list(Path(directory).glob("USER.md.bak-*"))
            self.assertEqual(len(backups), 1)
            second = run_json(ONBOARDING, "project-workbuddy", workspace,
                              "--user-file", user_file, "--memory-file", memory_file, "--apply")
            self.assertFalse(any(item["changed"] for item in second["files"]))


if __name__ == "__main__":
    unittest.main()
