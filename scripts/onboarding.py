#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V2.2.1 first-run onboarding state machine for AI咨询转化飞轮.

The conversational interview stays in the Skill instructions.  This script
owns the deterministic effects that must survive across conversations:
canonical workspace creation, confirmed profile projection, first-case
verification, and truthful automation authorization state.
"""

import argparse
import datetime
import difflib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from compat import ensure_dir, expand_path
from project_version import core_version
from verify_consult_workspace import locate_workspace, verify


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "scripts" / "init_consult_workspace.py"
PROFILE_SCHEMA = "2.1.1-onboarding-profile"
STATE_SCHEMA = "2.1.1-onboarding-state"
EVENT_SCHEMA = "2.1.1-onboarding-event"
AUTOMATION_SCHEMA = "2.1.1-automation-request"
INTERVIEW_SCHEMA = "2.2.1-onboarding-interview"
INTERVIEW_DIR = ".ai-consult-setup"
INTERVIEW_FILE = "interview-state.json"
INTERVIEW_STEPS = ("role", "organization", "daily_work", "identity", "first_case")
TEMP_INTERVIEW_TTL_SECONDS = 24 * 60 * 60
USER_START = "<!-- AI_CONSULT_ONBOARDING_START -->"
USER_END = "<!-- AI_CONSULT_ONBOARDING_END -->"
MEMORY_START = "<!-- AI_CONSULT_SAFETY_START -->"
MEMORY_END = "<!-- AI_CONSULT_SAFETY_END -->"


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def load_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def atomic_save_json(path, value):
    path = Path(path)
    ensure_dir(path.parent)
    temporary = path.with_name(path.name + ".tmp")
    with io.open(str(temporary), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(str(temporary), str(path)) if hasattr(os, "replace") else os.rename(str(temporary), str(path))


def append_jsonl(path, value):
    path = Path(path)
    ensure_dir(path.parent)
    with io.open(str(path), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def safe_name(value):
    value = str(value or "").strip()
    for char in "/\\:*?\"<>|":
        value = value.replace(char, "_")
    return value.strip() or "待确认"


def split_values(value):
    if not value:
        return []
    return [item.strip() for item in re.split(r"[,，;；\n]+", str(value)) if item.strip()]


def validate_schedule(value):
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(value or ""))
    if not match:
        raise ValueError("schedule must use HH:MM")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("schedule must be a valid local time")
    return "{0:02d}:{1:02d}".format(hour, minute)


def normalize_role(value):
    role = str(value or "").strip().lower()
    if role == "consultant":
        role = "frontline"
    if role not in ("manager", "frontline"):
        raise ValueError("role must be manager or frontline")
    return role


def normalize_members(value, role, employee_name="", employee_id=""):
    raw = split_values(value)
    if role == "manager" and len(raw) == 1 and re.match(
            r"^\s*\d+\s*(?:个|名|位)?\s*(?:咨询师|咨询人员|成员|人)?\s*$", raw[0]):
        raise ValueError("团队人数不能代替成员名单；请至少提供1名咨询师姓名，也可以稍后上传名单")
    if role == "frontline" and not raw:
        if not employee_name:
            raise ValueError("frontline onboarding requires employee name")
        raw = ["{0}_{1}".format(employee_id or "A001", employee_name)]
    rows = []
    used = set()
    explicit_ids = set()
    for item in raw:
        match = re.match(r"^([A-Za-z]+\d+)[_-](.+)$", safe_name(item))
        if match:
            member_id = match.group(1).upper()
            if member_id in explicit_ids:
                raise ValueError("duplicate employee id: {0}".format(member_id))
            explicit_ids.add(member_id)
    next_number = 1
    for item in raw:
        item = safe_name(item)
        match = re.match(r"^([A-Za-z]+\d+)[_-](.+)$", item)
        if match:
            member_id = match.group(1).upper()
            name = safe_name(match.group(2))
        else:
            while "A{0:03d}".format(next_number) in used or "A{0:03d}".format(next_number) in explicit_ids:
                next_number += 1
            member_id = "A{0:03d}".format(next_number)
            name = item
            next_number += 1
        if member_id in used:
            raise ValueError("duplicate employee id: {0}".format(member_id))
        used.add(member_id)
        rows.append({"employee_id": member_id, "employee_name": name,
                     "folder": "{0}_{1}".format(member_id, name)})
    if role == "manager" and not rows:
        raise ValueError("manager onboarding requires at least one member; one-person trial is allowed")
    return rows


def canonical_path(value):
    return Path(os.path.realpath(str(expand_path(value))))


def path_is_within(path, parent):
    try:
        canonical_path(path).relative_to(canonical_path(parent))
        return True
    except ValueError:
        return False


def normalized_session_id(value, create=False):
    value = str(value or "").strip()
    if not value and create:
        value = uuid.uuid4().hex
    if not value or not re.match(r"^[A-Za-z0-9_-]{8,80}$", value):
        raise ValueError("无工作空间设置需要有效的内部 session id")
    return value


def interview_state_path(selected_root="", session_id=""):
    if selected_root:
        return canonical_path(selected_root) / INTERVIEW_DIR / INTERVIEW_FILE
    session_id = normalized_session_id(session_id)
    return Path(tempfile.gettempdir()) / "ai-consult-onboarding" / (session_id + ".json")


def cleanup_expired_interviews():
    root = Path(tempfile.gettempdir()) / "ai-consult-onboarding"
    if not root.is_dir():
        return
    cutoff = time.time() - TEMP_INTERVIEW_TTL_SECONDS
    for path in root.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                os.remove(str(path))
        except OSError:
            continue


def state_path_from_args(args):
    return interview_state_path(getattr(args, "selected_root", "") or "",
                                getattr(args, "session_id", "") or "")


def validate_business_location(selected, must_exist=False):
    selected = expand_path(selected)
    if must_exist and not selected.is_dir():
        raise ValueError("请先新建或选择一个本地业务文件夹，再开始设置")
    if selected.exists() and not selected.is_dir():
        raise ValueError("工作区位置必须是文件夹")
    resolved_selected = canonical_path(selected)
    resolved_home = canonical_path(Path(os.path.expanduser("~")))
    if resolved_selected == Path(resolved_selected.anchor) or resolved_selected == resolved_home:
        raise ValueError("不能把磁盘根目录或整个用户目录作为业务文件夹；请选择范围明确的位置")
    if path_is_within(selected, ROOT):
        raise ValueError("业务文件夹不能位于 Skill 安装目录内；请选择独立位置")
    return selected


def recommended_business_root(state):
    organization = (state.get("answers") or {}).get("organization") or {}
    institution = safe_name(organization.get("institution") or "我的机构")
    return expand_path(Path(os.path.expanduser("~")) / "Documents" / "AI咨询转化飞轮" / institution)


def interview_question(step, role=""):
    questions = {
        "role": "你主要是自己做患者咨询，还是管理一个咨询团队？",
        "organization": "你负责哪家机构、哪个科室或部门？有主推项目也可以一起告诉我。",
        "daily_work": ("你现在最花时间的是听录音、看微信聊天、辅导员工，还是做团队复盘？"
                       if role == "manager" else
                       "你每天最常做的是微信回复、电话邀约、回访，还是现场咨询？"),
        "identity": ("把你管理的咨询师名单发给我。可以粘贴姓名、上传Excel或名单截图；也可以先给1名姓名试用。"
                     if role == "manager" else
                     "报告里怎么称呼你？有员工编号的话也可以一起告诉我。"),
        "first_case": "最近有没有一条你觉得没聊好的微信或电话？有的话设置完成后直接发给我；暂时没有也可以稍后验证。",
    }
    return questions.get(step, "请确认以上设置。")


def existing_interview_answers(workspace):
    workspace = Path(workspace)
    profile = load_json(workspace / "_系统" / "首次设置" / "confirmed-profile.json", {}) or {}
    source = load_json(workspace / "_系统" / "来源配置.json", {}) or {}
    role_doc = load_json(workspace / "_系统" / "运行时角色.json", {}) or {}
    team = source.get("team") or {}
    answers = {}
    role = profile.get("role") or role_doc.get("role")
    if role in ("manager", "frontline", "consultant"):
        answers["role"] = "frontline" if role == "consultant" else role
    institution = profile.get("institution") or source.get("institution")
    department = profile.get("department") or source.get("department")
    if institution and department:
        answers["organization"] = {
            "institution": institution,
            "department": department,
            "projects": profile.get("projects") or source.get("projects") or [],
        }
    daily = profile.get("frequent_scenarios") or []
    current_problem = profile.get("current_problem") or ""
    if daily:
        answers["daily_work"] = {"daily_work": daily, "current_problem": current_problem}
    members = profile.get("members") or []
    if not members:
        members = [{"folder": item} for item in (team.get("members") or [])]
    if answers.get("role") == "manager" and members:
        answers["identity"] = {
            "manager_name": profile.get("manager_name") or team.get("manager_name") or "咨询主管",
            "members": [item.get("folder") for item in members if item.get("folder")],
        }
    elif answers.get("role") == "frontline" and members:
        first = members[0]
        folder = first.get("folder") or ""
        match = re.match(r"^([A-Za-z]+\d+)[_-](.+)$", folder)
        answers["identity"] = {
            "employee_id": first.get("employee_id") or (match.group(1) if match else ""),
            "employee_name": first.get("employee_name") or (match.group(2) if match else folder),
        }
    first_status = profile.get("first_case_status")
    if first_status in ("registered", "completed", "needs_correction"):
        answers["first_case"] = "ready"
    elif first_status == "deferred":
        answers["first_case"] = "defer"
    return answers


def next_interview_step(answers):
    for step in INTERVIEW_STEPS:
        if step not in answers:
            return step
    return "confirmation"


def interview_summary(state):
    answers = state.get("answers") or {}
    organization = answers.get("organization") or {}
    identity = answers.get("identity") or {}
    daily = answers.get("daily_work") or {}
    role = answers.get("role")
    return {
        "role": "咨询主管/管理者" if role == "manager" else "一线咨询师",
        "institution": organization.get("institution"),
        "department": organization.get("department"),
        "projects": organization.get("projects") or [],
        "daily_work": daily.get("daily_work") or [],
        "current_problem": daily.get("current_problem") or "待使用中补充",
        "manager_name": identity.get("manager_name") if role == "manager" else "",
        "members": identity.get("members") or [],
        "employee_name": identity.get("employee_name") if role == "frontline" else "",
        "first_case": "设置完成后上传" if answers.get("first_case") == "ready" else "稍后验证",
        "workspace_location": state.get("planned_workspace"),
    }


def render_interview_result(state):
    result = {
        "status": state.get("status"),
        "schema_version": INTERVIEW_SCHEMA,
        "core_version": core_version(),
        "phase": state.get("phase"),
        "step": state.get("current_step"),
        "rounds_completed": state.get("rounds_completed", 0),
        "planned_workspace": state.get("planned_workspace"),
        "existing_workspace": state.get("existing_workspace", False),
        "location_mode": state.get("location_mode"),
        "host": state.get("host"),
        "session_id": state.get("session_id"),
    }
    if state.get("current_step") in INTERVIEW_STEPS:
        result["question"] = interview_question(state["current_step"], (state.get("answers") or {}).get("role"))
    if state.get("current_step") == "confirmation":
        result["summary"] = interview_summary(state)
        result["question"] = "以上信息是否正确？确认后我会在所示位置建立或继续使用标准工作区。"
        result["confirmation_required"] = True
    return result


def start_interview(args):
    # Preserve the host-visible path spelling in manifests. macOS commonly
    # exposes /var as a symlink to /private/var; resolving it here would make
    # an existing valid manifest appear to belong to a different workspace.
    selected_value = args.selected_root or ""
    if selected_value:
        selected = validate_business_location(selected_value, must_exist=True)
        verification = verify(selected)
        existing = verification.get("status") == "canonical"
        use_selected_root = bool(not existing and selected.name == "咨询转化工作区")
        workspace = selected if use_selected_root else Path(verification.get("workspace"))
        session_id = ""
        path = interview_state_path(selected)
        location_mode = ("resume_existing" if existing else
                         "use_selected_workspace" if use_selected_root else "create_standard_child")
    else:
        cleanup_expired_interviews()
        selected = None
        existing = False
        use_selected_root = False
        workspace = None
        session_id = normalized_session_id(args.session_id, create=True)
        path = interview_state_path("", session_id)
        location_mode = "pending_location"
    previous = load_json(path, {}) or {}
    if (previous.get("schema_version") == INTERVIEW_SCHEMA
            and previous.get("status") in ("in_progress", "awaiting_confirmation")):
        return render_interview_result(previous)
    answers = existing_interview_answers(workspace) if existing else {}
    step = next_interview_step(answers)
    state = {
        "schema_version": INTERVIEW_SCHEMA,
        "core_version": core_version(),
        "status": "awaiting_confirmation" if step == "confirmation" else "in_progress",
        "phase": "understand_user",
        "current_step": step,
        "rounds_completed": len(answers),
        "answers": answers,
        "selected_root": str(selected) if selected else "",
        "planned_workspace": str(workspace) if workspace else "",
        "existing_workspace": existing,
        "use_selected_root": use_selected_root,
        "location_mode": location_mode,
        "session_id": session_id,
        "ephemeral_state": not bool(selected),
        "temporary_state_ttl_hours": 24 if not selected else None,
        "host": args.host,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    atomic_save_json(path, state)
    return render_interview_result(state)


def answer_interview(args):
    path = state_path_from_args(args)
    state = load_json(path, {}) or {}
    if state.get("schema_version") != INTERVIEW_SCHEMA:
        raise ValueError("未找到当前版本的设置草稿；请先执行开始设置")
    expected = state.get("current_step")
    if args.step != expected:
        raise ValueError("当前只应回答{0}，不能跳到{1}".format(expected, args.step))
    answers = dict(state.get("answers") or {})
    role = answers.get("role")
    if args.step == "role":
        answers["role"] = normalize_role(args.role)
    elif args.step == "organization":
        institution = safe_name(args.institution)
        department = safe_name(args.department)
        if institution == "待确认" or department == "待确认":
            raise ValueError("机构和科室/部门都需要确认")
        answers["organization"] = {
            "institution": institution, "department": department,
            "projects": split_values(args.projects),
        }
    elif args.step == "daily_work":
        daily = split_values(args.daily_work)
        if not daily:
            raise ValueError("请至少选择一项最常做或最花时间的工作")
        answers["daily_work"] = {"daily_work": daily, "current_problem": str(args.current_problem or "").strip()}
    elif args.step == "identity":
        if role == "manager":
            rows = normalize_members(args.members, "manager")
            answers["identity"] = {
                "manager_name": safe_name(args.manager_name or "咨询主管"),
                "members": [row["folder"] for row in rows],
            }
        elif role == "frontline":
            rows = normalize_members("", "frontline", args.employee_name, args.employee_id)
            answers["identity"] = {
                "employee_name": rows[0]["employee_name"], "employee_id": rows[0]["employee_id"],
            }
        else:
            raise ValueError("请先确认角色")
    elif args.step == "first_case":
        if args.first_case not in ("ready", "defer"):
            raise ValueError("first_case must be ready or defer")
        answers["first_case"] = args.first_case
    state["answers"] = answers
    state["rounds_completed"] = state.get("rounds_completed", 0) + 1
    state["current_step"] = next_interview_step(answers)
    state["status"] = "awaiting_confirmation" if state["current_step"] == "confirmation" else "in_progress"
    if state["current_step"] == "confirmation" and not state.get("selected_root"):
        selected = recommended_business_root(state)
        state["selected_root"] = str(selected)
        state["planned_workspace"] = str(selected / "咨询转化工作区")
        state["location_mode"] = "recommended_default"
    state["updated_at"] = now_iso()
    atomic_save_json(path, state)
    return render_interview_result(state)


def confirm_interview(args):
    path = state_path_from_args(args)
    state = load_json(path, {}) or {}
    if state.get("schema_version") != INTERVIEW_SCHEMA or state.get("current_step") != "confirmation":
        raise ValueError("核心采访尚未完成，不能创建工作区")
    if args.confirmation == "temporary":
        state.update({
            "status": "temporary_mode", "phase": "temporary_analysis",
            "updated_at": now_iso(),
        })
        atomic_save_json(path, state)
        return {
            "status": "temporary_mode",
            "phase": "temporary_analysis",
            "workspace_created": False,
            "can_do": ["分析当前上传的一张截图、一通录音或一段文本"],
            "cannot_do": ["保存团队历史", "每日全量复盘", "咨询师成长跟踪", "自动运行"],
            "next_step": "直接上传一条材料；需要持续使用时再确认创建本地工作区。",
        }
    if args.confirmation != "confirm":
        if not args.revise_step:
            raise ValueError("需要说明要修改哪一项")
        answers = dict(state.get("answers") or {})
        answers.pop(args.revise_step, None)
        if args.revise_step == "role":
            answers.pop("daily_work", None)
            answers.pop("identity", None)
        state.update({
            "answers": answers, "status": "in_progress", "phase": "understand_user",
            "current_step": args.revise_step, "updated_at": now_iso(),
        })
        atomic_save_json(path, state)
        return render_interview_result(state)
    selected_root = args.workspace_root or state.get("selected_root")
    if not selected_root:
        raise ValueError("请确认推荐位置或选择一个本地文件夹")
    selected = validate_business_location(selected_root, must_exist=False)
    use_selected_root = bool(selected.name == "咨询转化工作区")
    state["selected_root"] = str(selected)
    state["use_selected_root"] = use_selected_root
    state["planned_workspace"] = str(selected if use_selected_root else selected / "咨询转化工作区")
    state["location_mode"] = ("use_selected_workspace" if use_selected_root else
                              "custom_location" if args.workspace_root else state.get("location_mode"))
    answers = state.get("answers") or {}
    organization = answers["organization"]
    identity = answers["identity"]
    daily = answers["daily_work"]
    role = answers["role"]
    configure_args = argparse.Namespace(
        workspace_root=state["selected_root"], role=role,
        institution=organization["institution"], department=organization["department"],
        projects=",".join(organization.get("projects") or []), channels="",
        manager_name=identity.get("manager_name") or "咨询主管",
        members=",".join(identity.get("members") or []),
        employee_name=identity.get("employee_name") or "",
        employee_id=identity.get("employee_id") or "",
        frequent_scenarios=",".join(daily.get("daily_work") or []),
        current_problem=daily.get("current_problem") or "",
        output_style="口语化优先", output_versions="一版主推", prohibitions="",
        schedule="22:30", host=state.get("host") or "manual", use_root=bool(state.get("use_selected_root")),
    )
    configured = configure(configure_args)
    if answers.get("first_case") == "defer":
        defer_first_case(configured["workspace"])
    state.update({
        "status": "workspace_created", "phase": "first_value", "current_step": "first_case_delivery",
        "workspace": configured["workspace"], "updated_at": now_iso(),
    })
    atomic_save_json(path, state)
    final_state = Path(configured["workspace"]) / "_系统" / "首次设置" / "interview-state.json"
    atomic_save_json(final_state, state)
    if state.get("ephemeral_state") and path.is_file():
        os.remove(str(path))
    return {
        "status": "workspace_created",
        "phase": "first_value",
        "core_version": core_version(),
        "workspace": configured["workspace"],
        "workspace_verified": configured["onboarding"].get("workspace_verified"),
        "profile": str(Path(configured["workspace"]) / "_系统" / "首次设置" / "confirmed-profile.json"),
        "manifest": str(Path(configured["workspace"]) / "_系统" / "工作区清单.json"),
        "next_step": ("请上传刚才提到的微信长截图、录音或文字，我先分析这一条。"
                      if answers.get("first_case") == "ready"
                      else "首条真实材料已标记为稍后验证；接下来选择手动模式或宿主支持的自动运行。"),
    }


def run_init(selected_root, role, manager_name, members, use_root=False):
    command = [sys.executable, str(INIT), str(selected_root), "--name", "咨询转化工作区",
               "--role", role, "--manager-name", manager_name,
               "--members", ",".join(row["folder"] for row in members)]
    if use_root:
        command.append("--use-root")
    process = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    stdout = stdout.decode("utf-8", "replace")
    stderr = stderr.decode("utf-8", "replace")
    if process.returncode != 0:
        raise ValueError(stderr.strip() or stdout.strip() or "workspace initialization failed")
    try:
        return json.loads(stdout)
    except ValueError:
        raise ValueError("workspace initializer returned invalid output")


def onboarding_paths(root):
    base = root / "_系统" / "首次设置"
    ensure_dir(base)
    return {
        "base": base,
        "profile": base / "confirmed-profile.json",
        "state": base / "onboarding-state.json",
        "events": base / "onboarding-events.jsonl",
        "automation_request": base / "automation-request.json",
        "automation_receipt": base / "automation-receipt.json",
        "readable_profile": root / "咨询用户画像-当前.md",
    }


def append_event(paths, event, details=None):
    append_jsonl(paths["events"], {
        "schema_version": EVENT_SCHEMA,
        "event": event,
        "created_at": now_iso(),
        "details": details or {},
    })


def update_json(path, changes):
    value = load_json(path, {}) or {}
    value.update(changes)
    atomic_save_json(path, value)
    return value


def render_readable_profile(profile):
    members = profile.get("members") or []
    lines = [
        "# 咨询用户画像", "",
        "> 本文件是首次设置的可读投影；结构化配置是系统唯一真源。", "",
        "## 机构与角色", "",
        "- 机构：{0}".format(profile.get("institution") or "待确认"),
        "- 部门/科室：{0}".format(profile.get("department") or "待确认"),
        "- 主推项目：{0}".format("、".join(profile.get("projects") or []) or "待补充"),
        "- 角色：{0}".format("咨询主管" if profile.get("role") == "manager" else "一线咨询师"),
        "- 获客渠道：{0}".format("、".join(profile.get("channels") or []) or "待使用中补充"), "",
        "## 本人或团队", "",
    ]
    if members:
        lines.extend("- {0}（{1}）".format(row.get("employee_name"), row.get("employee_id")) for row in members)
    else:
        lines.append("- 待补充")
    preferences = profile.get("output_preferences") or {}
    first_labels = {
        "not_started": "待开始", "registered": "处理中", "completed": "已通过",
        "needs_correction": "需要修正", "deferred": "稍后验证",
    }
    automation_labels = {
        "not_authorized": "未授权", "requested": "等待宿主确认",
        "verified": "已开启", "manual_mode": "手动模式",
    }
    lines.extend([
        "", "## 输出偏好", "",
        "- 表达风格：{0}".format(preferences.get("style") or "口语化优先"),
        "- 版本数量：{0}".format(preferences.get("versions") or "一版主推"),
        "", "## 机构额外红线", "",
    ])
    prohibitions = profile.get("institution_prohibitions") or []
    lines.extend("- {0}".format(item) for item in prohibitions)
    if not prohibitions:
        lines.append("- 暂无额外红线；医疗通用安全规则仍然生效。")
    lines.extend([
        "", "## 设置状态", "",
        "- 第一条验证：{0}".format(first_labels.get(profile.get("first_case_status"), "待开始")),
        "- 自动运行：{0}".format(automation_labels.get(profile.get("automation_status"), "未授权")),
        "- 自动时间：{0}".format(profile.get("schedule") or "22:30"),
        "", "## 更新日志", "",
        "- {0}：首次设置或设置更新。".format(profile.get("updated_at") or now_iso()), "",
    ])
    return "\n".join(lines)


def save_profile(paths, profile):
    profile["updated_at"] = now_iso()
    atomic_save_json(paths["profile"], profile)
    with io.open(str(paths["readable_profile"]), "w", encoding="utf-8") as handle:
        handle.write(render_readable_profile(profile))
    return profile


def configure(args):
    role = normalize_role(args.role)
    institution = safe_name(args.institution)
    department = safe_name(args.department)
    if institution == "待确认" or department == "待确认":
        raise ValueError("institution and department are required")
    members = normalize_members(args.members, role, args.employee_name, args.employee_id)
    schedule = validate_schedule(args.schedule)
    selected = expand_path(args.workspace_root)
    manager_name = safe_name(args.manager_name if role == "manager" else args.employee_name)
    _, existing_location = locate_workspace(selected)
    effective_use_root = bool(args.use_root or existing_location == "selected_folder")
    run_init(selected, role, manager_name, members, effective_use_root)
    root, _ = locate_workspace(selected)
    verification = verify(root)
    if verification.get("status") != "canonical":
        raise ValueError("workspace verification failed: {0}".format(verification.get("status")))
    paths = onboarding_paths(root)
    previous = load_json(paths["profile"], {}) or {}
    scope_changed = bool(previous) and any((
        previous.get("role") != role,
        previous.get("institution") != institution,
        previous.get("department") != department,
    ))
    automation_changed = bool(previous) and any((
        previous.get("schedule") != schedule,
        previous.get("host") != args.host,
    ))
    first_case_status = "not_started" if scope_changed else (previous.get("first_case_status") or "not_started")
    automation_status = "not_authorized" if automation_changed else (previous.get("automation_status") or "not_authorized")
    profile = {
        "schema_version": PROFILE_SCHEMA,
        "role": role,
        "institution": institution,
        "department": department,
        "projects": split_values(args.projects),
        "channels": split_values(args.channels),
        "manager_name": manager_name if role == "manager" else "",
        "members": members,
        "frequent_scenarios": split_values(args.frequent_scenarios),
        "current_problem": str(args.current_problem or "").strip(),
        "output_preferences": {
            "style": str(args.output_style or "口语化优先").strip(),
            "versions": str(args.output_versions or "一版主推").strip(),
        },
        "institution_prohibitions": split_values(args.prohibitions),
        "first_case_status": first_case_status,
        "first_case_task_id": None if scope_changed else previous.get("first_case_task_id"),
        "first_case_user_validation": None if scope_changed else previous.get("first_case_user_validation"),
        "automation_status": automation_status,
        "automation_id": None if automation_changed else previous.get("automation_id"),
        "schedule": schedule,
        "host": args.host,
        "confirmed_at": now_iso() if scope_changed else (previous.get("confirmed_at") or now_iso()),
    }
    save_profile(paths, profile)
    source_config = root / "_系统" / "来源配置.json"
    source = load_json(source_config, {}) or {}
    source["institution"] = institution
    source["department"] = department
    source["projects"] = profile["projects"]
    source["channels"] = profile["channels"]
    source["team"] = dict(source.get("team") or {},
                          mode="single_manager_team" if role == "manager" else "single_frontline",
                          manager_name=manager_name if role == "manager" else "",
                          members=[row["folder"] for row in members])
    automation_enabled = profile["automation_status"] == "verified"
    source["automation"] = dict(source.get("automation") or {}, enabled=automation_enabled, schedule=schedule,
                                scheduler=args.host, authorization_status=profile["automation_status"])
    atomic_save_json(source_config, source)
    update_json(root / "_系统" / "运行时角色.json", {"schema_version": "2.1.1", "role": role})
    update_json(root / "_系统" / "状态.json", {
        "stage": "机构与团队已确认",
        "institution": institution,
        "department": department,
        "institution_confirmed": True,
        "department_confirmed": True,
        "team_management_ready": bool(members),
        "first_case_ready": profile["first_case_status"] == "completed",
    })
    update_json(root / "_系统" / "自动化配置.json", {
        "enabled": automation_enabled,
        "schedule": schedule,
        "scheduler": args.host,
        "authorization_status": profile["automation_status"],
        "automation_id": profile.get("automation_id"),
    })
    state = compute_state(root, profile)
    atomic_save_json(paths["state"], state)
    append_event(paths, "profile_confirmed", {"role": role, "member_count": len(members)})
    return {"status": "configured", "workspace": str(root), "profile": profile,
            "onboarding": state, "next_step": state.get("next_step")}


def get_profile(workspace):
    root, _ = locate_workspace(expand_path(workspace))
    paths = onboarding_paths(root)
    profile = load_json(paths["profile"], {}) or {}
    if not profile:
        raise ValueError("confirmed onboarding profile not found")
    return root, paths, profile


def compute_state(root, profile):
    first_status = profile.get("first_case_status") or "not_started"
    automation_status = profile.get("automation_status") or "not_authorized"
    verification = verify(root)
    identity_ready = bool(profile.get("members"))
    if verification.get("status") != "canonical":
        stage = "workspace_needs_repair"
        next_step = "修复工作区"
    elif first_status not in ("completed", "deferred"):
        stage = "awaiting_first_case"
        next_step = "上传一张微信长截图、一通录音或一段文字，完成第一条验证"
    elif automation_status not in ("verified", "manual_mode"):
        stage = "awaiting_automation_choice"
        next_step = "选择自动运行时间并授权，或明确使用手动模式"
    elif not identity_ready:
        stage = "identity_missing"
        next_step = "补充本人或至少一名咨询师"
    else:
        stage = "ready"
        next_step = "查看四个日常入口"
    return {
        "schema_version": STATE_SCHEMA,
        "stage": stage,
        "ready": stage == "ready",
        "role_confirmed": profile.get("role") in ("manager", "frontline"),
        "organization_confirmed": bool(profile.get("institution") and profile.get("department")),
        "identity_or_team_confirmed": identity_ready,
        "workspace_verified": verification.get("status") == "canonical",
        "first_case_status": first_status,
        "automation_status": automation_status,
        "next_step": next_step,
        "updated_at": now_iso(),
    }


def status(workspace):
    root, paths, profile = get_profile(workspace)
    state = compute_state(root, profile)
    atomic_save_json(paths["state"], state)
    return {
        "status": "ready" if state["ready"] else "in_progress",
        "workspace": str(root),
        "institution": profile.get("institution"),
        "department": profile.get("department"),
        "role": profile.get("role"),
        "member_count": len(profile.get("members") or []),
        "first_case_status": profile.get("first_case_status"),
        "automation_status": profile.get("automation_status"),
        "schedule": profile.get("schedule"),
        "next_step": state.get("next_step"),
        "front_doors": ((["分析这一条", "分析今天全部咨询", "查看今天重点", "查看某个咨询师"]
                         if profile.get("role") == "manager"
                         else ["分析这一条", "生成下一步内容", "帮我回复", "安排回访", "陪我练一遍"])
                        if state.get("ready") else []),
    }


def register_first_case(args):
    root, paths, profile = get_profile(args.workspace_root)
    sys.path.insert(0, str(ROOT / "scripts"))
    from daily_review import register_direct_task
    employee_id = args.employee_id or (profile.get("members") or [{}])[0].get("employee_id")
    employee_name = args.employee_name or (profile.get("members") or [{}])[0].get("employee_name")
    if not employee_id:
        raise ValueError("first case requires an employee")
    task, created = register_direct_task(root, args.source, employee_id, employee_name,
                                         args.date, args.medium, args.source_hash)
    profile["first_case_status"] = "registered"
    profile["first_case_task_id"] = task.get("analysis_task_id")
    profile["first_case_user_validation"] = None
    save_profile(paths, profile)
    atomic_save_json(paths["state"], compute_state(root, profile))
    append_event(paths, "first_case_registered", {"task_id": task.get("analysis_task_id")})
    return {"status": "registered", "created": created, "task": task,
            "next_step": "完成OCR/转写和V2.1逐案分析后验证第一条结果"}


def verify_first_case(args):
    root, paths, profile = get_profile(args.workspace_root)
    task_id = args.task_id or profile.get("first_case_task_id")
    if not task_id:
        raise ValueError("first case task id is missing")
    task = load_json(root / "_系统" / "每日复盘" / "tasks" / (safe_name(task_id) + ".json"), {}) or {}
    if task.get("status") != "completed":
        raise ValueError("first case analysis is not completed")
    report_path = root / task.get("report_path", "")
    if not report_path.is_file():
        raise ValueError("first case report is missing")
    profile["first_case_status"] = "completed" if args.judgment == "accepted" else "needs_correction"
    profile["first_case_user_validation"] = args.judgment
    profile["first_case_report_path"] = task.get("report_path")
    save_profile(paths, profile)
    update_json(root / "_系统" / "状态.json", {"first_case_ready": args.judgment == "accepted"})
    atomic_save_json(paths["state"], compute_state(root, profile))
    append_event(paths, "first_case_{0}".format(args.judgment), {"task_id": task_id})
    return {"status": profile["first_case_status"], "report_path": task.get("report_path"),
            "next_step": ("请求一次自动运行授权" if args.judgment == "accepted"
                          else "修正机构、员工归属、识别文本或分析标准后重新验证")}


def defer_first_case(workspace):
    root, paths, profile = get_profile(workspace)
    profile["first_case_status"] = "deferred"
    save_profile(paths, profile)
    atomic_save_json(paths["state"], compute_state(root, profile))
    append_event(paths, "first_case_deferred")
    return {"status": "deferred", "next_step": "选择自动运行或手动模式；首条语义质量仍待验证"}


def automation_prompt(root):
    return ("使用 AI咨询转化飞轮执行每日自动复盘。扫描工作区 {0} 的新增材料；"
            "建立可恢复任务后必须分批领取，完成OCR/转写和逐案语义分析，"
            "每条调用complete或fail，最后aggregate并刷新今日重点。"
            "只排队不能报告完成。").format(str(root))


def request_automation(args):
    root, paths, profile = get_profile(args.workspace_root)
    schedule = validate_schedule(args.schedule or profile.get("schedule") or "22:30")
    hour, minute = [int(value) for value in schedule.split(":")]
    request = {
        "schema_version": AUTOMATION_SCHEMA,
        "host": args.host,
        "tool": "automation_update",
        "mode": "create",
        "name": "AI咨询转化飞轮每日复盘",
        "prompt": automation_prompt(root),
        "scheduleType": "recurring",
        "rrule": "FREQ=DAILY;BYHOUR={0};BYMINUTE={1}".format(hour, minute),
        "status": "ACTIVE",
        "cwds": [str(root)],
        "requested_at": now_iso(),
    }
    atomic_save_json(paths["automation_request"], request)
    profile["schedule"] = schedule
    profile["host"] = args.host
    profile["automation_status"] = "requested"
    save_profile(paths, profile)
    update_json(root / "_系统" / "自动化配置.json", {
        "enabled": False, "schedule": schedule, "scheduler": args.host,
        "authorization_status": "requested", "automation_id": None,
    })
    atomic_save_json(paths["state"], compute_state(root, profile))
    append_event(paths, "automation_requested", {"schedule": schedule, "host": args.host})
    return {"status": "requested", "request": request,
            "next_step": "宿主调用automation_update后，把真实回执交给verify-automation"}


def verify_automation(args):
    root, paths, profile = get_profile(args.workspace_root)
    request = load_json(paths["automation_request"], {}) or {}
    if not request:
        raise ValueError("automation request not found")
    receipt = load_json(expand_path(args.receipt), {}) or {}
    automation_id = receipt.get("id") or receipt.get("automationId")
    status_value = str(receipt.get("status") or "").upper()
    if not automation_id or status_value != "ACTIVE":
        raise ValueError("automation receipt must contain an active automation id")
    if receipt.get("rrule") != request.get("rrule"):
        raise ValueError("automation receipt schedule does not match request")
    receipt_cwds = receipt.get("cwds") or []
    if str(root) not in receipt_cwds:
        raise ValueError("automation receipt workspace does not match")
    stored = dict(receipt)
    stored["verified_at"] = now_iso()
    stored["verified_against"] = str(paths["automation_request"].relative_to(root))
    atomic_save_json(paths["automation_receipt"], stored)
    profile["automation_status"] = "verified"
    profile["automation_id"] = automation_id
    save_profile(paths, profile)
    update_json(root / "_系统" / "自动化配置.json", {
        "enabled": True, "schedule": profile.get("schedule"), "scheduler": profile.get("host"),
        "authorization_status": "verified", "automation_id": automation_id,
    })
    source_path = root / "_系统" / "来源配置.json"
    source = load_json(source_path, {}) or {}
    source["automation"] = dict(source.get("automation") or {}, enabled=True,
                                schedule=profile.get("schedule"), scheduler=profile.get("host"),
                                authorization_status="verified", automation_id=automation_id)
    atomic_save_json(source_path, source)
    atomic_save_json(paths["state"], compute_state(root, profile))
    append_event(paths, "automation_verified", {"automation_id": automation_id})
    return {"status": "verified", "automation_id": automation_id,
            "next_step": "完成首次设置并进入日常入口"}


def manual_mode(workspace):
    root, paths, profile = get_profile(workspace)
    profile["automation_status"] = "manual_mode"
    profile["automation_id"] = None
    save_profile(paths, profile)
    update_json(root / "_系统" / "自动化配置.json", {
        "enabled": False, "authorization_status": "manual_mode", "automation_id": None,
    })
    atomic_save_json(paths["state"], compute_state(root, profile))
    append_event(paths, "manual_mode_selected")
    return {"status": "manual_mode", "next_step": "使用“分析今天全部咨询”手动运行同一流水线"}


def managed_projection(original, start, end, content):
    block = start + "\n" + content.strip() + "\n" + end
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if pattern.search(original):
        return pattern.sub(block, original)
    prefix = original.rstrip()
    return (prefix + "\n\n" if prefix else "") + block + "\n"


def projection_content(profile, target):
    preferences = profile.get("output_preferences") or {}
    if target == "user":
        return ("## AI咨询转化飞轮偏好\n\n"
                "- 角色：{0}\n- 机构：{1}\n- 部门：{2}\n- 输出风格：{3}\n- 版本数量：{4}").format(
                    "咨询主管" if profile.get("role") == "manager" else "一线咨询师",
                    profile.get("institution"), profile.get("department"),
                    preferences.get("style") or "口语化优先",
                    preferences.get("versions") or "一版主推")
    prohibitions = profile.get("institution_prohibitions") or []
    lines = ["## AI咨询转化飞轮机构额外红线", ""]
    lines.extend("- {0}".format(item) for item in prohibitions)
    if not prohibitions:
        lines.append("- 暂无机构额外红线；继续执行飞轮医疗通用安全规则。")
    return "\n".join(lines)


def project_file(path, start, end, content, apply=False):
    path = expand_path(path)
    original = ""
    if path.is_file():
        with io.open(str(path), "r", encoding="utf-8") as handle:
            original = handle.read()
    projected = managed_projection(original, start, end, content)
    changed = projected != original
    backup = None
    if apply and changed:
        ensure_dir(path.parent)
        if path.is_file():
            backup = path.with_name(path.name + ".bak-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
            with io.open(str(backup), "w", encoding="utf-8") as handle:
                handle.write(original)
        with io.open(str(path), "w", encoding="utf-8") as handle:
            handle.write(projected)
    diff = list(difflib.unified_diff(original.splitlines(), projected.splitlines(),
                                     fromfile=str(path), tofile=str(path) + ".projected", lineterm=""))
    return {"path": str(path), "changed": changed, "applied": bool(apply and changed),
            "backup": str(backup) if backup else None, "diff": diff[:80]}


def project_workbuddy(args):
    root, paths, profile = get_profile(args.workspace_root)
    results = []
    if args.user_file:
        results.append(project_file(args.user_file, USER_START, USER_END,
                                    projection_content(profile, "user"), args.apply))
    if args.memory_file:
        results.append(project_file(args.memory_file, MEMORY_START, MEMORY_END,
                                    projection_content(profile, "memory"), args.apply))
    if not results:
        raise ValueError("provide --user-file or --memory-file")
    append_event(paths, "workbuddy_projection_{0}".format("applied" if args.apply else "previewed"),
                 {"targets": [item["path"] for item in results]})
    return {"status": "applied" if args.apply else "preview", "files": results}


def complete_onboarding(workspace):
    root, paths, profile = get_profile(workspace)
    state = compute_state(root, profile)
    if not state.get("ready"):
        raise ValueError("onboarding is not ready: {0}".format(state.get("next_step")))
    update_json(root / "_系统" / "状态.json", {"stage": "可以分析", "onboarding_ready": True})
    atomic_save_json(paths["state"], state)
    append_event(paths, "onboarding_completed")
    return {"status": "completed", "workspace": str(root),
            "institution": profile.get("institution"), "department": profile.get("department"),
            "member_count": len(profile.get("members") or []),
            "first_case_status": profile.get("first_case_status"),
            "automation_status": profile.get("automation_status"),
            "schedule": profile.get("schedule"),
            "front_doors": status(root).get("front_doors")}


def main():
    parser = argparse.ArgumentParser(description="Run V2.2.1 three-phase first-use onboarding for AI咨询转化飞轮.")
    sub = parser.add_subparsers(dest="command")

    interview_start = sub.add_parser("interview-start", help="start or resume the deterministic five-question interview")
    interview_start.add_argument("selected_root", nargs="?", default="", help="optional local business folder selected or opened by the user")
    interview_start.add_argument("--session-id", default="", help="internal host session id when no workspace is open")
    interview_start.add_argument("--host", choices=("workbuddy", "codex", "claude", "trae", "younavi", "manual"), default="manual")

    interview_answer = sub.add_parser("interview-answer", help="answer only the currently expected interview step")
    interview_answer.add_argument("selected_root", nargs="?", default="")
    interview_answer.add_argument("--session-id", default="")
    interview_answer.add_argument("--step", choices=INTERVIEW_STEPS, required=True)
    interview_answer.add_argument("--role", choices=("manager", "frontline", "consultant"), default="manager")
    interview_answer.add_argument("--institution", default="")
    interview_answer.add_argument("--department", default="")
    interview_answer.add_argument("--projects", default="")
    interview_answer.add_argument("--daily-work", default="")
    interview_answer.add_argument("--current-problem", default="")
    interview_answer.add_argument("--manager-name", default="咨询主管")
    interview_answer.add_argument("--members", default="")
    interview_answer.add_argument("--employee-name", default="")
    interview_answer.add_argument("--employee-id", default="")
    interview_answer.add_argument("--first-case", choices=("ready", "defer"), default="ready")

    interview_confirm = sub.add_parser("interview-confirm", help="create the workspace only after one summary confirmation")
    interview_confirm.add_argument("selected_root", nargs="?", default="")
    interview_confirm.add_argument("--session-id", default="")
    interview_confirm.add_argument("--workspace-root", default="", help="optional parent or canonical workspace selected at confirmation")
    interview_confirm.add_argument("--confirmation", choices=("confirm", "revise", "temporary"), required=True)
    interview_confirm.add_argument("--revise-step", choices=INTERVIEW_STEPS, default="")

    configure_parser = sub.add_parser("configure", help="commit the confirmed interview and create a canonical workspace")
    configure_parser.add_argument("workspace_root")
    configure_parser.add_argument("--role", required=True, choices=("manager", "frontline", "consultant"))
    configure_parser.add_argument("--institution", required=True)
    configure_parser.add_argument("--department", required=True)
    configure_parser.add_argument("--projects", default="")
    configure_parser.add_argument("--channels", default="")
    configure_parser.add_argument("--manager-name", default="咨询主管")
    configure_parser.add_argument("--members", default="")
    configure_parser.add_argument("--employee-name", default="")
    configure_parser.add_argument("--employee-id", default="")
    configure_parser.add_argument("--frequent-scenarios", default="")
    configure_parser.add_argument("--current-problem", default="")
    configure_parser.add_argument("--output-style", default="口语化优先")
    configure_parser.add_argument("--output-versions", default="一版主推")
    configure_parser.add_argument("--prohibitions", default="")
    configure_parser.add_argument("--schedule", default="22:30")
    configure_parser.add_argument("--host", choices=("workbuddy", "codex", "claude", "trae", "younavi", "manual"), default="workbuddy")
    configure_parser.add_argument("--use-root", action="store_true")

    status_parser = sub.add_parser("status", help="show user-facing onboarding readiness")
    status_parser.add_argument("workspace_root")

    register = sub.add_parser("register-first-case", help="register the first real material in the V2.1 queue")
    register.add_argument("workspace_root")
    register.add_argument("--source", required=True)
    register.add_argument("--employee-id", default="")
    register.add_argument("--employee-name", default="")
    register.add_argument("--date", required=True)
    register.add_argument("--medium", choices=("audio", "image", "wechat", "text", "chat"), required=True)
    register.add_argument("--source-hash", default="")

    verify_case = sub.add_parser("verify-first-case", help="verify a completed report and store the user's judgment")
    verify_case.add_argument("workspace_root")
    verify_case.add_argument("--task-id", default="")
    verify_case.add_argument("--judgment", choices=("accepted", "needs_correction"), required=True)

    defer = sub.add_parser("defer-first-case", help="explicitly continue without semantic first-case validation")
    defer.add_argument("workspace_root")

    request = sub.add_parser("request-automation", help="create a host automation request; this does not claim installation")
    request.add_argument("workspace_root")
    request.add_argument("--schedule", default="")
    request.add_argument("--host", choices=("workbuddy", "codex"), default="workbuddy")

    verify_auto = sub.add_parser("verify-automation", help="verify a real host receipt before enabling automation")
    verify_auto.add_argument("workspace_root")
    verify_auto.add_argument("--receipt", required=True)

    manual = sub.add_parser("manual-mode", help="use the identical manual analysis flow")
    manual.add_argument("workspace_root")

    project = sub.add_parser("project-workbuddy", help="preview or apply managed USER/MEMORY projections")
    project.add_argument("workspace_root")
    project.add_argument("--user-file", default="")
    project.add_argument("--memory-file", default="")
    project.add_argument("--apply", action="store_true")

    complete = sub.add_parser("complete", help="mark onboarding complete only when readiness gates pass")
    complete.add_argument("workspace_root")

    args = parser.parse_args()
    try:
        if args.command == "interview-start":
            result = start_interview(args)
        elif args.command == "interview-answer":
            result = answer_interview(args)
        elif args.command == "interview-confirm":
            result = confirm_interview(args)
        elif args.command == "configure":
            result = configure(args)
        elif args.command == "status":
            result = status(args.workspace_root)
        elif args.command == "register-first-case":
            result = register_first_case(args)
        elif args.command == "verify-first-case":
            result = verify_first_case(args)
        elif args.command == "defer-first-case":
            result = defer_first_case(args.workspace_root)
        elif args.command == "request-automation":
            result = request_automation(args)
        elif args.command == "verify-automation":
            result = verify_automation(args)
        elif args.command == "manual-mode":
            result = manual_mode(args.workspace_root)
        elif args.command == "project-workbuddy":
            result = project_workbuddy(args)
        elif args.command == "complete":
            result = complete_onboarding(args.workspace_root)
        else:
            parser.print_help()
            return 2
    except (IOError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
