#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the offline, self-contained v2.0 consultation manager dashboard."""

import argparse
import datetime
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

from management_data import (
    CAPABILITY_FILE, EVENTS_FILE, SAMPLES_FILE, TRAINING_FILE,
    aggregate_metrics, dedupe_samples, in_period, latest_by, load_json,
    load_jsonl, locate_workspace, management_root, now_iso, period_bounds,
    read_team_data, save_json, task_state,
    team_breakpoint,
)
from compat import ensure_dir
from daily_review import queue_status as review_queue_status


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "management-dashboard.template"
STAGE_KEYS = ("candidate_experience", "trainable_action", "behavior_verified", "outcome_verified", "team_capability")


def load_profile(root):
    profile = load_json(root / "_系统" / "来源配置.json", {}) or {}
    team = profile.get("team") or {}
    state = load_json(root / "_系统" / "状态.json", {}) or {}
    return {
        "institution": state.get("institution") or profile.get("institution") or "当前机构",
        "team": team.get("team_name") or team.get("manager_name") or "当前咨询团队",
        "members": team.get("members") or [],
    }


def load_capability_version(root):
    active = load_json(root / "_系统" / "发布" / "active.json", {}) or {}
    return active.get("release_version") or active.get("active_version") or "base_only"


def load_queue_status(root, date_value):
    review = review_queue_status(root, date_value)
    if review.get("total"):
        failed = review.get("status_counts", {}).get("failed", 0)
        failed += review.get("status_counts", {}).get("quarantined", 0)
        return review.get("pending", 0) + review.get("retryable_failed", 0), failed
    auto = root / "_系统" / "团队自动化"
    queues = sorted((auto / "01_待处理队列").glob("夜间任务-*.jsonl")) if (auto / "01_待处理队列").is_dir() else []
    pending = 0
    for path in queues:
        for row in load_jsonl(path):
            if row.get("status", "queued") not in ("completed", "skipped"):
                pending += 1
    failed_root = root / "_系统" / "失败记录"
    failed = sum(1 for item in failed_root.rglob("*") if item.is_file()) if failed_root.is_dir() else 0
    return pending, failed


def choose_breakpoint(samples):
    return team_breakpoint(samples)


def outcome_label(samples):
    known = [row.get("outcome") for row in samples if row.get("outcome") not in (None, "", "unknown", "missing")]
    return known[-1] if known else "结果待观察"


def build_employees(samples, trainings, profile, team_rows):
    by_employee = defaultdict(list)
    names = {}
    for sample in samples:
        employee_id = sample.get("employee_id") or "unknown"
        by_employee[employee_id].append(sample)
        names[employee_id] = sample.get("employee_name") or names.get(employee_id) or employee_id
    for member in profile.get("members", []):
        member_id = member.split("_", 1)[0]
        names.setdefault(member_id, member)
        by_employee.setdefault(member_id, [])
    result = []
    for employee_id in sorted(by_employee):
        rows = by_employee[employee_id]
        gaps = Counter(row.get("employee_gap") or row.get("breakpoint") for row in rows
                       if (row.get("employee_gap") or row.get("breakpoint")) not in (None, "", "unknown", "missing"))
        strengths = Counter(row.get("verified_strength") for row in rows if row.get("verified_strength"))
        options = [item for item in trainings if item.get("target_id") in (employee_id, "team") and item.get("status") != "closed"]
        personal = [item for item in options if item.get("target_id") == employee_id]
        pool = personal or options
        training = max(pool, key=lambda item: item.get("updated_at") or "") if pool else {}
        employee_team_rows = [row for row in team_rows if str(row.get("employee_id") or row.get("employee_name")) == employee_id]
        arrivals = [row.get("arrivals") for row in employee_team_rows if row.get("arrivals") is not None]
        status_map = {"pending": "待执行", "in_training": "待复查", "awaiting_review": "待复查",
                      "passed": "行为改善", "closed": "已验证"}
        result.append({
            "employee_id": employee_id, "employee_name": names.get(employee_id, employee_id),
            "main_breakpoint": gaps.most_common(1)[0][0] if gaps else "未知",
            "verified_strength": strengths.most_common(1)[0][0] if strengths else "待验证",
            "training_action": training.get("key_action") or "待建立",
            "review_sample_count": len(training.get("review_samples") or []),
            "status": status_map.get(training.get("status"), "待执行"),
            "outcome_change": outcome_label(rows), "effective_arrivals": sum(arrivals) if arrivals else None,
            "sample_count": len(rows),
        })
    return result


def capability_counts(rows):
    counts = dict((key, 0) for key in STAGE_KEYS)
    latest = latest_by(rows, "capability_id")
    for row in latest.values():
        stage = row.get("stage")
        if stage in counts:
            counts[stage] += 1
    return counts


def management_metrics(events, trainings, employees):
    review_minutes = sum(float(row.get("duration_minutes") or 0) for row in events
                         if row.get("event") in ("complete", "review"))
    employee_ids = set(row.get("employee_id") for row in employees if row.get("employee_id") not in (None, "", "unknown"))
    adopted = set()
    passed = set()
    non_source_passed = set()
    for training in trainings:
        source = training.get("source_employee")
        for employee in training.get("adopted_employees") or []:
            adopted.add(employee)
        for employee in training.get("passed_employees") or []:
            passed.add(employee)
            if not source or employee != source:
                non_source_passed.add(employee)
    def rate(numerator, denominator):
        if not denominator:
            return {"status": "missing", "value": None, "numerator": numerator, "denominator": None}
        return {"status": "known", "value": float(numerator) / denominator,
                "numerator": numerator, "denominator": denominator}
    return {
        "manager_review_minutes": {"status": "known" if review_minutes else "missing",
                                   "value": review_minutes if review_minutes else None},
        "training_adoption_rate": rate(len(adopted), len(employee_ids)),
        "training_review_pass_rate": rate(len(passed), len(adopted)),
        "champion_replication_rate": rate(len(non_source_passed), max(len(employee_ids) - 1, 0)),
    }


def compute_data_completeness(samples, outcome_metrics):
    if not samples and outcome_metrics.get("record_count", 0) == 0:
        return "missing", 4
    missing = 0
    if not samples:
        missing += 1
    if not any(row.get("breakpoint") not in (None, "", "unknown", "missing") for row in samples):
        missing += 1
    if outcome_metrics.get("metrics", {}).get("appointment_arrival_rate", {}).get("status") != "known":
        missing += 1
    if outcome_metrics.get("metrics", {}).get("arrival_paid_rate", {}).get("status") != "known":
        missing += 1
    return ("complete" if missing == 0 else "partial"), missing


def build_weekly_trend(rows, date_value):
    anchor = datetime.datetime.strptime(date_value, "%Y-%m-%d").date()
    week_end = anchor + datetime.timedelta(days=6 - anchor.weekday())
    result = []
    for offset in range(3, -1, -1):
        end = week_end - datetime.timedelta(days=offset * 7)
        start = end - datetime.timedelta(days=6)
        period_rows = [row for row in rows if in_period(row.get("date"), start.isoformat(), end.isoformat())]
        metrics = aggregate_metrics(period_rows)
        result.append({"start": start.isoformat(), "end": end.isoformat(),
                       "effective_consultations": metrics["totals"].get("effective_consultations"),
                       "appointments": metrics["totals"].get("appointments"),
                       "arrivals": metrics["totals"].get("arrivals"),
                       "appointment_arrival_rate": metrics["metrics"].get("appointment_arrival_rate")})
    return result


def build_period(root, store, profile, period, date_value):
    start, end = period_bounds(period, date_value)
    all_samples = dedupe_samples(load_jsonl(store / SAMPLES_FILE))
    samples = [row for row in all_samples if in_period(row.get("date"), start, end)]
    raw_events = load_jsonl(store / EVENTS_FILE)
    period_events = [row for row in raw_events if in_period(row.get("created_at"), start, end)]
    tasks = [task for task in task_state(raw_events)
             if not task.get("due_date") or in_period(task.get("due_date"), start, end) or task.get("status") not in ("completed", "reviewed")]
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    tasks = sorted(tasks, key=lambda item: (priority_order.get(item.get("priority"), 9), item.get("due_date") or "9999"))
    trainings = list(latest_by(load_jsonl(store / TRAINING_FILE), "action_id").values())
    period_trainings = [row for row in trainings if in_period(row.get("updated_at") or row.get("created_at"), start, end)]
    active_trainings = [row for row in trainings if row.get("status") != "closed"]
    team_trainings = [row for row in active_trainings if row.get("scope") == "team"]
    training = sorted(team_trainings or active_trainings, key=lambda item: item.get("updated_at") or "", reverse=True)
    training = training[0] if training else {}
    if training:
        training = dict(training)
        training["command"] = "基于训练任务 {0} 生成10分钟晨会训练和3轮陪练".format(training.get("action_id"))
    all_team_rows = read_team_data(root)
    team_rows = [row for row in all_team_rows if in_period(row.get("date"), start, end)]
    outcomes = aggregate_metrics(team_rows)
    outcomes["weekly_trend"] = build_weekly_trend(all_team_rows, date_value)
    outcomes["note"] = ("数据完整，可以观察经营结果。" if outcomes["metrics"]["appointment_arrival_rate"]["status"] == "known"
                        else "缺少预约或到院结果，暂时只能观察行为和过程变化。")
    breakpoint = choose_breakpoint(samples)
    stable_breakpoint = breakpoint if breakpoint and breakpoint.get("status") == "stable" else None
    daily_projection = load_json(root / "_系统" / "每日复盘" / "projections" / (date_value + "-team-day.json"), {}) or {}
    patient_priorities = daily_projection.get("patient_priorities") or [] if period == "today" else []
    patient_tasks = [{
        "task_id": "PATIENT-" + str(item.get("patient_case_id")),
        "priority": item.get("priority") or "P2",
        "type": "patient_priority",
        "target": item.get("employee_id"),
        "status": "pending_manager_judgment",
        "reason": item.get("reason"),
        "action": "{0}：{1}".format(item.get("employee_name") or "咨询师", item.get("next_action") or "主管判断下一步"),
        "source_refs": item.get("evidence") or [],
        "command": "打开患者案例 {0}，复核证据并决定下一步".format(item.get("patient_case_id")),
        "report_path": item.get("report_path"),
    } for item in patient_priorities]
    tasks = patient_tasks + tasks
    if not tasks and stable_breakpoint:
        tasks = [{"task_id": "AUTO-{0}-{1}".format(date_value.replace("-", ""), period.upper()),
                  "priority": "P1", "type": "training", "target": "team", "status": "recommended",
                  "reason": "{0}名员工的{1}个患者案例出现同一断点".format(stable_breakpoint["employee_count"], stable_breakpoint["sample_count"]),
                  "action": "围绕“{0}”建立一个单动作训练".format(stable_breakpoint["label"]),
                  "source_refs": stable_breakpoint["refs"],
                  "command": "基于团队断点“{0}”生成晨会训练".format(stable_breakpoint["label"])}]
    summary = {
        "main_breakpoint": stable_breakpoint.get("label") if stable_breakpoint else "尚未达到跨2人、3个患者案例的团队断点门槛",
        "breakpoint_observation": breakpoint.get("label") if breakpoint and not stable_breakpoint else None,
        "sample_count": stable_breakpoint.get("sample_count", 0) if stable_breakpoint else 0,
        "affected_employee_count": stable_breakpoint.get("employee_count", 0) if stable_breakpoint else 0,
        "today_action": (training.get("key_action") if training else
                         ("围绕“{0}”建立一个可观察的单动作训练".format(stable_breakpoint.get("label")) if stable_breakpoint else
                          "先放入一通录音或一段微信，系统会从第一个片段开始")),
        "review_standard": training.get("pass_criteria") or "每名员工提交一条新样本，检查目标动作是否出现",
    }
    completeness, missing_count = compute_data_completeness(samples, outcomes)
    pending, failed = load_queue_status(root, date_value)
    evidence = []
    if stable_breakpoint:
        sample_by_id = dict((row.get("sample_id"), row) for row in samples if row.get("sample_id"))
        links = []
        refs = []
        for ref in stable_breakpoint["refs"]:
            sample = sample_by_id.get(ref, {})
            refs.extend(sample.get("evidence_refs") or [ref])
            if sample.get("source"):
                links.append(sample.get("source"))
        evidence.append({"label": "主要断点：" + stable_breakpoint["label"], "refs": refs,
                         "links": sorted(set(links))})
    employees = build_employees(samples, active_trainings, profile, team_rows)
    return {
        "period": period, "start": start, "end": end, "summary": summary,
        "tasks": tasks, "training": training, "employees": employees,
        "outcomes": outcomes, "management_metrics": management_metrics(period_events, period_trainings, employees),
        "capability_counts": capability_counts(load_jsonl(store / CAPABILITY_FILE)),
        "evidence": evidence, "patient_priorities": patient_priorities, "data_status": {
            "sample_count": len(samples), "pending_analysis_count": pending, "failed_item_count": failed,
            "data_completeness": completeness, "missing_count": missing_count,
            "capability_version": load_capability_version(root),
        },
    }


def build_dashboard(workspace, selected_period, date_value):
    root = locate_workspace(workspace)
    store = management_root(root)
    profile = load_profile(root)
    periods = dict((period, build_period(root, store, profile, period, date_value)) for period in ("today", "week", "month"))
    employee_options = {}
    for value in periods.values():
        for employee in value.get("employees", []):
            employee_options[employee["employee_id"]] = {"employee_id": employee["employee_id"], "employee_name": employee["employee_name"]}
    data = {
        "schema_version": "2.0-dashboard", "generated_at": now_iso(), "selected_period": selected_period,
        "anchor_date": date_value, "institution": profile["institution"], "team": profile["team"],
        "employee_options": [employee_options[key] for key in sorted(employee_options)], "periods": periods,
        "demo": bool((load_json(root / "_系统" / "演示标记.json", {}) or {}).get("synthetic_demo")),
    }
    save_json(store / "dashboard-data.json", data)
    output = root / "08_团队管理" / "04_团队报告" / "04_数据看板" / "咨询管理工作台.html"
    ensure_dir(output.parent)
    with io.open(str(TEMPLATE), "r", encoding="utf-8") as handle:
        template_text = handle.read()
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    with io.open(str(output), "w", encoding="utf-8") as handle:
        handle.write(template_text.replace("__DASHBOARD_DATA__", serialized))
    return data, output


def main():
    parser = argparse.ArgumentParser(description="Generate the offline AI consultation manager dashboard.")
    parser.add_argument("workspace_root")
    parser.add_argument("--period", choices=("today", "week", "month"), default="today")
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    args = parser.parse_args()
    try:
        datetime.datetime.strptime(args.date, "%Y-%m-%d")
        data, output = build_dashboard(args.workspace_root, args.period, args.date)
    except (ValueError, IOError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    selected = data["periods"][args.period]
    print(json.dumps({"status": "generated", "dashboard": str(output), "dashboard_generated_at": data["generated_at"],
                      "data_completeness": selected["data_status"]["data_completeness"],
                      "pending_analysis_count": selected["data_status"]["pending_analysis_count"],
                      "failed_item_count": selected["data_status"]["failed_item_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
