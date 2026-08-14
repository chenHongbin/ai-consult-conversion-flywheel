#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end acceptance tests for the v2.0 manager workbench."""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
INIT = SCRIPTS / "init_consult_workspace.py"
DASHBOARD = SCRIPTS / "generate_management_dashboard.py"
VERIFY = SCRIPTS / "verify_consult_workspace.py"
RECORD_SAMPLE = SCRIPTS / "record_communication_sample.py"
RECORD_EVENT = SCRIPTS / "record_management_action.py"
RECORD_TRAINING = SCRIPTS / "record_training_action.py"
PROMOTE = SCRIPTS / "promote_team_capability.py"
DEMO = SCRIPTS / "create_demo_management_workspace.py"
NIGHTLY = SCRIPTS / "run_nightly_cycle.py"
ROUTER = SCRIPTS / "route_consultation.py"
BUILD_BASE = SCRIPTS / "build_base_skill_package.py"
PUBLISH = SCRIPTS / "publish_release.py"
BUILD_TEAM = SCRIPTS / "build_team_skill_package.py"


def run_json(script, *args, **kwargs):
    expected_codes = kwargs.get("expected_codes", (0,))
    process = subprocess.Popen([sys.executable, str(script)] + list(args), cwd=str(ROOT),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    stdout = stdout.decode("utf-8")
    stderr = stderr.decode("utf-8")
    if process.returncode not in expected_codes:
        raise AssertionError("command failed: {}\n{}\n{}".format(script, stdout, stderr))
    return json.loads(stdout)


def load_json(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_text(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        return handle.read()


def save_json(path, value):
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)


class ManagerWorkbenchTests(unittest.TestCase):
    def init_workspace(self, directory, members="A001_张宁,A002_李明,A003_王芳"):
        result = run_json(INIT, directory, "--manager-name", "主管林老师", "--members", members)
        return Path(result["workspace"])

    def test_empty_and_legacy_workspace_generate_without_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory, "")
            manifest_path = workspace / "_系统" / "工作区清单.json"
            manifest = load_json(manifest_path)
            manifest["layout_version"] = "v1.9"
            with io.open(str(manifest_path), "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False)
            manager_store = workspace / "_系统" / "管理工作台"
            if manager_store.is_dir():
                shutil.rmtree(str(manager_store))
            marker = workspace / "我的旧资料.txt"
            with io.open(str(marker), "w", encoding="utf-8") as handle:
                handle.write("keep")

            verified = run_json(VERIFY, str(workspace))
            self.assertEqual(verified["status"], "canonical")
            result = run_json(DASHBOARD, str(workspace), "--date", "2026-08-14")
            self.assertEqual(result["status"], "generated")
            self.assertTrue(marker.is_file())
            self.assertTrue(manager_store.is_dir())
            data = load_json(manager_store / "dashboard-data.json")
            today = data["periods"]["today"]
            self.assertEqual(today["data_status"]["data_completeness"], "missing")
            self.assertIsNone(today["outcomes"]["metrics"]["appointment_arrival_rate"]["value"])
            self.assertIn("证据不足", load_text(Path(result["dashboard"])))

    def test_source_hash_deduplication_uses_newest_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            store = workspace / "_系统" / "管理工作台"
            store.mkdir(parents=True)
            samples = store / "communication-samples.jsonl"
            older = {"sample_id": "S-OLD", "source_hash": "same-hash", "employee_id": "A001",
                     "date": "2026-08-14", "medium": "audio", "breakpoint": "旧断点",
                     "updated_at": "2026-08-14T08:00:00"}
            newer = dict(older)
            newer.update({"sample_id": "S-NEW", "breakpoint": "新断点", "updated_at": "2026-08-14T09:00:00"})
            with io.open(str(samples), "w", encoding="utf-8") as handle:
                handle.write(json.dumps(older, ensure_ascii=False) + "\n")
                handle.write(json.dumps(newer, ensure_ascii=False) + "\n")
            run_json(DASHBOARD, str(workspace), "--date", "2026-08-14")
            data = load_json(store / "dashboard-data.json")["periods"]["today"]
            self.assertEqual(data["data_status"]["sample_count"], 1)
            self.assertEqual(data["summary"]["main_breakpoint"], "新断点")

    def test_same_stable_id_uses_latest_even_if_source_hash_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory)
            samples = workspace / "_系统" / "管理工作台" / "communication-samples.jsonl"
            samples.parent.mkdir(parents=True)
            with io.open(str(samples), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"sample_id": "S-STABLE", "source_hash": "old", "employee_id": "A001",
                                         "date": "2026-08-14", "breakpoint": "旧版本", "updated_at": "2026-08-14T08:00:00"}, ensure_ascii=False) + "\n")
                handle.write(json.dumps({"sample_id": "S-STABLE", "source_hash": "new", "employee_id": "A001",
                                         "date": "2026-08-14", "breakpoint": "新版本", "updated_at": "2026-08-14T09:00:00"}, ensure_ascii=False) + "\n")
            run_json(DASHBOARD, str(workspace), "--date", "2026-08-14")
            today = load_json(workspace / "_系统" / "管理工作台" / "dashboard-data.json")["periods"]["today"]
            self.assertEqual(today["data_status"]["sample_count"], 1)
            self.assertEqual(today["summary"]["main_breakpoint"], "新版本")

    def test_management_events_fold_to_current_status(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory, "")
            run_json(RECORD_EVENT, str(workspace), "--task-id", "M-001", "--event", "create",
                     "--priority", "P0", "--target", "A001", "--action", "复核录音", "--due-date", "2026-08-14")
            run_json(RECORD_EVENT, str(workspace), "--task-id", "M-001", "--event", "start", "--note", "开始处理")
            run_json(RECORD_EVENT, str(workspace), "--task-id", "M-001", "--event", "review",
                     "--note", "复查通过", "--review-sample", "S-NEW", "--minutes", "12")
            run_json(DASHBOARD, str(workspace), "--date", "2026-08-14")
            task = load_json(workspace / "_系统" / "管理工作台" / "dashboard-data.json")["periods"]["today"]["tasks"][0]
            self.assertEqual(task["status"], "reviewed")
            self.assertEqual(task["review_samples"], ["S-NEW"])

    def test_each_employee_has_one_latest_personal_training_action(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory, "A001_张宁")
            run_json(RECORD_SAMPLE, str(workspace), "--employee-id", "A001", "--date", "2026-08-14",
                     "--medium", "wechat", "--source-hash", "sample-a", "--breakpoint", "顾虑验证不足")
            run_json(RECORD_TRAINING, str(workspace), "--action-id", "T-TEAM", "--scope", "team", "--target-id", "team",
                     "--title", "团队动作", "--key-action", "团队旧动作", "--pass-criteria", "出现一次")
            run_json(RECORD_TRAINING, str(workspace), "--action-id", "T-A001", "--scope", "employee", "--target-id", "A001",
                     "--title", "个人动作", "--key-action", "只问一个顾虑验证问题", "--pass-criteria", "两条样本出现")
            run_json(DASHBOARD, str(workspace), "--date", "2026-08-14")
            employees = load_json(workspace / "_系统" / "管理工作台" / "dashboard-data.json")["periods"]["today"]["employees"]
            self.assertEqual(len(employees), 1)
            self.assertEqual(employees[0]["training_action"], "只问一个顾虑验证问题")

    def test_capability_progression_cannot_skip_and_requires_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory, "")
            common = (str(workspace), "--capability-id", "CAP-1", "--name", "问具体", "--support-cases", "S1|S2",
                      "--counterexamples", "S0", "--applicable-conditions", "患者模糊推迟", "--manager-reviewed", "yes",
                      "--non-source-employees", "A001|A002", "--review-counts", "A001:2|A002:2")
            first = run_json(PROMOTE, *(common + ("--request-stage", "behavior_verified")))
            self.assertEqual(first["stage"], "trainable_action")
            second = run_json(PROMOTE, *(common + ("--request-stage", "behavior_verified")))
            self.assertEqual(second["stage"], "behavior_verified")
            no_result = run_json(PROMOTE, *(common + ("--request-stage", "outcome_verified")))
            self.assertEqual(no_result["stage"], "behavior_verified")
            result = run_json(PROMOTE, *(common + ("--request-stage", "team_capability", "--outcome-data", "yes",
                                                  "--outcome-improved", "yes", "--release-approved", "yes")))
            self.assertEqual(result["stage"], "outcome_verified")
            final = run_json(PROMOTE, *(common + ("--request-stage", "team_capability", "--outcome-data", "yes",
                                                 "--outcome-improved", "yes", "--release-approved", "yes")))
            self.assertEqual(final["stage"], "team_capability")

    def test_demo_contains_all_six_modules_and_complete_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_json(DEMO, directory, "--date", "2026-08-14")
            html = load_text(Path(result["dashboard"]))
            for label in ("今日管理结论", "今日管理待办", "今日训练主题", "重点员工与辅导进度",
                          "团队结果快照", "女娲学习与能力复制"):
                self.assertIn(label, html)
            self.assertIn("合成演示", html)
            self.assertNotIn("<script src=", html.lower())
            self.assertNotIn("<link rel=", html.lower())
            scripts = re.findall(r"<script(?: [^>]*)?>(.*?)</script>", html, re.S)
            if shutil.which("node"):
                syntax = subprocess.Popen(["node", "--check"], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                _, stderr = syntax.communicate(scripts[-1].encode("utf-8"))
                self.assertEqual(syntax.returncode, 0, stderr.decode("utf-8"))
            data = load_json(Path(result["workspace"]) / "_系统" / "管理工作台" / "dashboard-data.json")
            today = data["periods"]["today"]
            self.assertEqual(len(today["employees"]), 3)
            self.assertEqual(today["outcomes"]["metrics"]["appointment_arrival_rate"]["status"], "known")
            self.assertEqual(today["management_metrics"]["training_adoption_rate"]["status"], "known")

    def test_nightly_cycle_is_idempotent_and_rebuilds_dashboard(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory, "A001_张宁")
            inbox = workspace / "08_团队管理" / "01_成员" / "A001_张宁" / "01_今天放这里" / "合成微信.txt"
            with io.open(str(inbox), "w", encoding="utf-8") as handle:
                handle.write("合成测试文本")
            first = run_json(NIGHTLY, str(workspace), "--date", "2026-08-14", "--force")
            nightly_periods = load_json(workspace / "_系统" / "管理工作台" / "dashboard-data.json")["periods"]
            second = run_json(NIGHTLY, str(workspace), "--date", "2026-08-14", "--force")
            self.assertTrue(Path(first["dashboard_path"]).is_file())
            self.assertEqual(second["tasks_created"], 0)
            self.assertEqual(first["pending_analysis_count"], second["pending_analysis_count"])
            run_json(DASHBOARD, str(workspace), "--date", "2026-08-14")
            manual_periods = load_json(workspace / "_系统" / "管理工作台" / "dashboard-data.json")["periods"]
            self.assertEqual(nightly_periods, manual_periods)

    def test_audio_wechat_and_team_data_only_inputs_render(self):
        for medium in ("audio", "wechat"):
            with tempfile.TemporaryDirectory() as directory:
                workspace = self.init_workspace(directory, "A001_张宁")
                run_json(RECORD_SAMPLE, str(workspace), "--employee-id", "A001", "--date", "2026-08-14",
                         "--medium", medium, "--source-hash", "hash-" + medium, "--breakpoint", "单片段断点")
                result = run_json(DASHBOARD, str(workspace), "--date", "2026-08-14")
                self.assertTrue(Path(result["dashboard"]).is_file())
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory, "")
            data_dir = workspace / "08_团队管理" / "03_团队数据" / "01_今天放这里"
            with io.open(str(data_dir / "只有过程量.csv"), "w", encoding="utf-8") as handle:
                handle.write("date,employee_id,effective_consultations,appointments\n2026-08-14,A001,10,3\n")
            run_json(DASHBOARD, str(workspace), "--date", "2026-08-14")
            today = load_json(workspace / "_系统" / "管理工作台" / "dashboard-data.json")["periods"]["today"]
            self.assertEqual(today["outcomes"]["metrics"]["consultation_conversion_rate"]["status"], "known")
            self.assertEqual(today["outcomes"]["metrics"]["appointment_arrival_rate"]["status"], "missing")

    def test_manager_route_precedes_generic_team_route_and_is_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = self.init_workspace(directory, "")
            manager = run_json(ROUTER, "开始今天的工作，刷新咨询管理工作台", "--workspace-root", directory)
            self.assertEqual(manager["route_id"], "manager_workbench")
            frontline = run_json(ROUTER, "开始今天的工作", "--workspace-root", directory, "--role", "frontline")
            self.assertEqual(frontline["status"], "manager_confirmation_required")

    def test_public_package_contains_manager_runtime_frontline_allowlist_excludes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            built = run_json(BUILD_BASE, "--output-dir", directory)
            with zipfile.ZipFile(built["package"], "r") as archive:
                names = set(archive.namelist())
            self.assertIn("scripts/generate_management_dashboard.py", names)
            self.assertIn("assets/management-dashboard.template", names)
            self.assertNotIn("08_团队管理/04_团队报告/04_数据看板/咨询管理工作台.html", names)
            sys.path.insert(0, str(SCRIPTS))
            import build_team_skill_package as builder
            self.assertFalse(builder.should_copy_frontline(Path("scripts/generate_management_dashboard.py")))
            self.assertFalse(builder.should_copy_frontline(Path("scripts/promote_team_capability.py")))
            self.assertFalse(builder.should_copy_frontline(Path("references/manager-workbench.md")))

            selected = Path(directory) / "team-fixture"
            workspace = self.init_workspace(str(selected), "A001_张宁")
            scope = {"institution": "合成机构", "department": "合成科室", "disease_or_project": "通用", "channel": "通用"}
            components = (
                ("当前能力包", "package.json", "runtime-context.md", {"version": "v0.1", "scope": scope, "rules": []}),
                ("当前机构知识", "knowledge.json", "knowledge-runtime.md", {"version": "v0.1", "scope": scope, "facts": []}),
                ("患者洞察", "patient-insights.json", "patient-insights-runtime.md", {"version": "v1.5", "scope": scope, "decision_states": [], "doubt_intents": [], "practice_scenarios": []}),
            )
            for folder, package_name, runtime_name, package in components:
                version = package["version"]
                component_root = workspace / "_系统" / folder / "versions" / version
                package_path = component_root / package_name
                runtime_path = component_root / runtime_name
                save_json(package_path, package)
                with io.open(str(runtime_path), "w", encoding="utf-8") as handle:
                    handle.write("# 合成运行时\n")
                save_json(workspace / "_系统" / folder / "active.json", {
                    "status": "active", "active_version": version, "package_path": str(package_path),
                    "runtime_context_path": str(runtime_path), "scope": scope,
                })
            run_json(PUBLISH, str(selected), "--version", "Team-v2.0")
            team_output = Path(directory) / "team-output"
            team = run_json(BUILD_TEAM, str(selected), "--output-dir", str(team_output),
                            "--institution", "合成机构", "--department", "合成科室")
            with zipfile.ZipFile(team["package"], "r") as archive:
                team_names = set(archive.namelist())
                team_manifest = json.loads(archive.read("institution-pack/manifest.json").decode("utf-8"))
                release = json.loads(archive.read("institution-pack/release.json").decode("utf-8"))
            self.assertNotIn("scripts/generate_management_dashboard.py", team_names)
            self.assertNotIn("scripts/promote_team_capability.py", team_names)
            self.assertNotIn("references/manager-workbench.md", team_names)
            self.assertFalse(any(name.endswith("咨询管理工作台.html") for name in team_names))
            self.assertFalse(team_manifest["contains_manager_workspace"])
            self.assertEqual(team_manifest["release_id"], release["release_id"])


if __name__ == "__main__":
    unittest.main()
