#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create the learner-facing consultation conversion workspace."""

import argparse
import io
import json
import os
from pathlib import Path

from compat import ensure_dir, expand_path


VISIBLE_FOLDERS = [
    ("01_机构介绍", "机构介绍、品牌资料、服务范围和对外可确认信息"),
    ("02_科室项目与服务", "科室、病种、项目、流程、价格和预约事实"),
    ("03_咨询流程与标准", "咨询 SOP、合规要求、回访标准和团队规则"),
    ("04_优秀咨询案例", "优秀录音、优秀微信聊天和已审核标杆案例"),
    ("05_普通失败与未预约案例", "普通、失败、未预约、爽约和投诉案例"),
    ("06_团队培训与反馈", "培训材料、练习记录、主管反馈和复盘记录"),
    ("07_我的产出", "分析报告、话术卡、陪练结果、复盘报告和生成内容"),
    ("08_团队管理", "员工沟通、过程量、结果数据、团队报告和管理跟进"),
]

TEAM_ROOT_FOLDERS = [
    ("01_成员", "每名员工一个文件夹，资料直接放到对应员工名下"),
    ("02_团队会议", "早会、晚会、周复盘、月度盘点和培训会录音"),
    ("03_团队数据", "飞书、WPS、腾讯文档或本地表格导出的数据"),
    ("04_团队报告", "日报、周报、月报和数据看板"),
]

TEAM_MEMBER_FOLDERS = [
    ("01_今天放这里", "每天新增的录音、聊天和资料，Skill 会自动整理"),
    ("02_历史资料", "历史录音、历史微信聊天和已有资料"),
    ("03_个人报告", "个人日报、周报、月报和辅导报告"),
]

TEAM_MEETING_FOLDERS = [
    ("01_今天放这里", "每天新增的早会、晚会和其他会议录音"),
    ("02_会议报告", "会议纪要、决定和待办"),
]

TEAM_DATA_FOLDERS = [
    ("01_今天放这里", "每天新增的 Excel、CSV 或导出的数据表"),
]

TEAM_REPORT_FOLDERS = [
    ("01_日报", "每日数据和录音分析摘要"),
    ("02_周报", "每周个人与团队报告"),
    ("03_月报", "每月个人成长和团队经营报告"),
    ("04_数据看板", "HTML/Markdown 数据看板"),
]

TRAINING_OUTPUT_FOLDERS = [
    ("01_新人培训", "新人必知必会、分阶段训练和陪练任务"),
    ("02_训练记录与复盘", "新人练习结果、真实案例复盘和改进记录"),
]

OUTPUT_FOLDERS = [
    ("01_流失节点报告", "电话、微信、私信的流失阶段和断点"),
    ("02_客户顾虑与标准回应", "客户常见问题、顾虑知识卡和回应结构"),
    ("03_销冠蒸馏能力包", "销冠完整销售逻辑、流程、动作卡和版本"),
    ("04_咨询分析与陪练", "咨询复盘卡、陪练结果和跟进建议"),
    ("05_患者决策洞察与陪练", "患者决策状态、常见疑义、合成场景和训练建议"),
    ("06_咨询视觉素材", "朋友圈配图、微信跟进素材、案例示意、环境示意和培训视觉卡"),
]

VISUAL_OUTPUT_FOLDERS = [
    ("01_朋友圈文案与配图", "朋友圈短文案和配套图片"),
    ("02_微信跟进素材", "当前咨询下一步微信文案和配图"),
    ("03_案例与过程示意", "匿名案例示意和过程解释图"),
    ("04_医院环境与医生科普", "环境示意、医生科普和机构品牌素材"),
    ("05_团队培训视觉卡", "普通/优化回复对比和动作拆解图"),
]

SYSTEM_FOLDERS = [
    "原始资料",
    "转写与OCR",
    "案例标准化",
    "蒸馏候选",
    "当前能力包",
    "当前机构知识",
    "患者洞察",
    "机构知识候选",
    "评估集",
    "来源记录",
    "失败记录",
    "自动化",
    "发布",
    "视觉生成",
    "IMA同步",
    "个人成长",
]


def safe_name(value):
    value = str(value or "").strip()
    for char in "/\\:*?\"<>|":
        value = value.replace(char, "_")
    return value or "待命名"


def parse_members(value):
    members = []
    for item in (value or "").split(","):
        item = safe_name(item)
        if item and item != "待命名":
            members.append(item)
    return members


def write_if_missing(path, content):
    if not path.exists():
        with io.open(str(path), "w", encoding="utf-8") as handle:
            handle.write(content)


def main():
    parser = argparse.ArgumentParser(
        description="Create a simple consultation conversion workspace in a selected local folder."
    )
    parser.add_argument("workspace_root", help="the local workspace selected by the user")
    parser.add_argument("--name", default="咨询转化工作区", help="folder name to create")
    parser.add_argument(
        "--use-root",
        action="store_true",
        help="use workspace_root itself instead of creating a child folder",
    )
    parser.add_argument("--manager-name", default="咨询主管_待命名", help="single team manager name")
    parser.add_argument(
        "--members",
        default="",
        help="comma-separated members, for example A001_张三,A002_李四",
    )
    parser.add_argument(
        "--role",
        choices=("manager", "frontline", "consultant"),
        default="manager",
        help="runtime role; manager workspaces can distill/publish, frontline workspaces cannot",
    )
    args = parser.parse_args()

    selected = expand_path(args.workspace_root)
    root = selected if args.use_root else selected / args.name
    ensure_dir(root)

    for folder, _ in VISIBLE_FOLDERS:
        ensure_dir(root / folder)
    for folder, _ in TRAINING_OUTPUT_FOLDERS:
        ensure_dir(root / "06_团队培训与反馈" / folder)
    for folder, _ in OUTPUT_FOLDERS:
        ensure_dir(root / "07_我的产出" / folder)
    for folder, _ in VISUAL_OUTPUT_FOLDERS:
        ensure_dir(root / "07_我的产出" / "06_咨询视觉素材" / folder)
    team_root = root / "08_团队管理"
    for folder, _ in TEAM_ROOT_FOLDERS:
        ensure_dir(team_root / folder)
    for folder, _ in TEAM_MEETING_FOLDERS:
        ensure_dir(team_root / "02_团队会议" / folder)
    for folder, _ in TEAM_DATA_FOLDERS:
        ensure_dir(team_root / "03_团队数据" / folder)
    for folder, _ in TEAM_REPORT_FOLDERS:
        ensure_dir(team_root / "04_团队报告" / folder)
    manager_name = safe_name(args.manager_name)
    manager_root = team_root / "01_成员"
    ensure_dir(manager_root)
    for member in parse_members(args.members):
        member_root = manager_root / member
        for folder, _ in TEAM_MEMBER_FOLDERS:
            ensure_dir(member_root / folder)
    system = root / "_系统"
    ensure_dir(system)
    for folder in SYSTEM_FOLDERS:
        ensure_dir(system / folder)
    ensure_dir(system / "团队档案")
    ensure_dir(system / "当前能力包" / "versions")
    ensure_dir(system / "当前机构知识" / "versions")
    ensure_dir(system / "患者洞察" / "versions")
    ensure_dir(system / "发布" / "versions")
    write_if_missing(
        system / "发布" / "active.json",
        json.dumps(
            {
                "schema_version": "1.0",
                "status": "base_only",
                "release_id": None,
                "release_version": None,
                "message": "三类运行时组件完成审核后，由统一发布流程生成原子版本",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    write_if_missing(
        system / "当前机构知识" / "active.json",
        json.dumps(
            {
                "schema_version": "1.0",
                "status": "base_only",
                "active_version": None,
                "package_path": None,
                "runtime_context_path": None,
                "approved_fact_count": 0,
                "pending_fact_count": 0,
                "message": "每轮蒸馏会提取机构知识候选，管理者确认后进入运行时知识",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    write_if_missing(
        system / "当前能力包" / "active.json",
        json.dumps(
            {
                "schema_version": "1.0",
                "status": "base_only",
                "active_version": None,
                "package_path": None,
                "runtime_context_path": None,
                "message": "首次蒸馏完成并通过测试后生成机构专属能力包",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    write_if_missing(
        system / "患者洞察" / "active.json",
        json.dumps(
            {
                "schema_version": "1.0",
                "status": "base_only",
                "active_version": None,
                "package_path": None,
                "runtime_context_path": None,
                "message": "完成案例去重、疑义提取和审核后生成患者决策洞察",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    profile = system / "来源配置.json"
    write_if_missing(
        profile,
        json.dumps(
            {
                "version": "1.9",
                "workspace": str(root),
                "runtime": {"preferred": ["workbuddy", "trae", "codex", "claude"]},
                "sources": {
                    "local": {"enabled": True, "root": str(root)},
                    "ima": {
                        "enabled": False,
                        "knowledge_bases": [],
                        "native_context": "unknown",
                        "retrieval_mode": "controlled_cache",
                        "quota_policy": "pause_and_resume",
                        "priority_mix": {"positive": 0.50, "negative": 0.30, "other": 0.20},
                    },
                },
                "transcription": {"engine": "younavi", "enabled": True},
                "team": {
                    "mode": "single_manager_team",
                    "manager_name": manager_name,
                    "members": parse_members(args.members),
                    "front_door": "member_today_meeting_today_data_today_reports",
                    "backend_archive": "_系统/团队档案",
                    "report_names_in_private_workspace": True,
                    "public_export_redacts_names": True,
                },
                "automation": {
                    "enabled": True,
                    "schedule": "22:00",
                    "scheduler": "workbuddy_or_codex_agent",
                    "stability_minutes": 30,
                    "catch_up_next_run": True,
                },
                "release": {
                    "active_version": "base_only",
                    "candidate_version": "v1.9",
                    "auto_publish": False,
                },
                "patient_insights": {
                    "enabled": True,
                    "mode": "aggregate_decision_states_only",
                    "active_version": "base_only",
                    "publish_requires": ["evidence", "counterexamples", "privacy_review", "manager_review", "fixed_test"],
                },
                "personal_growth": {
                    "enabled": True,
                    "root": "_系统/个人成长",
                    "team_rules_override_personal": True,
                    "personal_experience_auto_promotes_to_team": False,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    write_if_missing(
        system / "运行时角色.json",
        json.dumps(
            {
                "schema_version": "1.0",
                "role": args.role,
                "message": "manager 可执行团队蒸馏与发布；frontline/consultant 只执行个人分析、陪练和成长。",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    write_if_missing(
        system / "状态.json",
        json.dumps(
            {
                "stage": "未初始化",
                "institution_confirmed": False,
                "department_confirmed": False,
                "first_case_ready": False,
                "team_management_ready": False,
                "metrics_baseline_ready": False,
                "active_capability_version": "v0.1",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    write_if_missing(
        system / "指标口径.json",
        json.dumps(
            {
                "consultation_conversion_rate": "appointments / effective_consultations",
                "arrival_rate": "arrivals / appointments",
                "paid_rate": "paid_cases / arrivals",
                "contact_rate": "contacts_obtained / valid_leads",
                "note": "如果机构口径不同，请先修改并说明周期、分母和负责人。",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    write_if_missing(
        system / "团队基线.json",
        json.dumps(
            {
                "status": "待补充",
                "note": "至少先提供同机构、同科室/病种、同渠道的完整基线周期，再计算翻倍目标。",
                "period": "",
                "metrics": {},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    write_if_missing(
        system / "自动化配置.json",
        json.dumps(
            {
                "enabled": True,
                "schedule": "22:00",
                "scheduler": "workbuddy_or_codex_agent",
                "scan_script": "scripts/run_nightly_cycle.py",
                "stability_minutes": 30,
                "run_when_computer_wakes": True,
                "retry_next_run": True,
                "do_not": ["自动发布能力包", "自动做人事定级", "自动发送微信", "自动对外发布"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    write_if_missing(
        root / "08_团队管理" / "03_团队数据" / "01_今天放这里" / "过程量数据模板.csv",
        "date,employee_id,employee_name,institution,department,disease_or_project,channel,valid_leads,first_responses,effective_consultations,contacts_obtained,followups,appointments,process_minutes\n",
    )
    write_if_missing(
        root / "08_团队管理" / "03_团队数据" / "01_今天放这里" / "结果数据模板.csv",
        "date,employee_id,employee_name,institution,department,disease_or_project,channel,appointments,arrivals,paid_cases,refunds,complaints\n",
    )

    folder_lines = "\n".join(
        "- **{0}**：{1}。不确定时先放这里，我会自动判断。".format(folder, description)
        for folder, description in VISIBLE_FOLDERS
    )
    team_lines = "\n".join(
        "- **08_团队管理/{0}**：{1}。".format(folder, description)
        for folder, description in TEAM_ROOT_FOLDERS
    )
    write_if_missing(
        root / "README_先看这里.md",
        """# AI咨询转化飞轮工作区\n\n只记住一件事：把资料放进看起来最接近的文件夹即可，不需要先整理。首次说“蒸馏销冠”时，我会扫描当前工作空间里的全部候选资料。\n\n{folder_lines}\n\n## 第一次使用\n\n1. 告诉 AI咨询转化飞轮：机构名称和要管理的科室。\n2. 选择使用本地文件夹、IMA，或两种都用。\n3. 第一次说“蒸馏销冠完整销售逻辑和流程”，我会扫描并处理当前工作空间的全部录音、聊天记录、截图和文档。\n4. 如果只想看一条，明确说“只分析这一条”。\n5. 如果要管理团队，告诉我主管姓名和成员名单，直接说“建立我的团队档案”。\n6. 每天把新增资料放入对应员工的 `01_今天放这里`，晚上由 WorkBuddy/Codex 定时任务处理。\n\n## 团队管理文件夹\n\n{team_lines}\n\n当前团队主管：{manager_name}。\n\n新人训练资料在 `06_团队培训与反馈/01_新人培训`；流失、顾虑和销冠能力包在 `07_我的产出` 下的对应文件夹。会议录音放入 `08_团队管理/02_团队会议/01_今天放这里`；团队数据放入 `08_团队管理/03_团队数据/01_今天放这里`；报告在 `08_团队管理/04_团队报告`。日期、分类和命名由 Skill 自动处理。\n\n`_系统` 由 Skill 自动维护，不需要手动修改。未脱敏的原始资料只保存在当前工作区，不会自动进入通用 Skill 或发布包。\n""".format(folder_lines=folder_lines, team_lines=team_lines, manager_name=manager_name),
    )

    print(
        json.dumps(
            {
                "workspace": str(root),
                "visible_folders": [folder for folder, _ in VISIBLE_FOLDERS],
                "team_root_folders": [folder for folder, _ in TEAM_ROOT_FOLDERS],
                "manager_name": manager_name,
                "members": parse_members(args.members),
                "system_folder": str(system),
                "profile": str(profile),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
