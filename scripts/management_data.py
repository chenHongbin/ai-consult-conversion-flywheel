#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared, Python 3.4-compatible data helpers for the v2.0 manager workbench."""

import csv
import datetime
import io
import json
from pathlib import Path

from compat import ensure_dir, expand_path


WORKSPACE_NAME = "咨询转化工作区"
MANAGEMENT_DIR = "管理工作台"
SAMPLES_FILE = "communication-samples.jsonl"
EVENTS_FILE = "management-events.jsonl"
TRAINING_FILE = "training-actions.jsonl"
CAPABILITY_FILE = "capability-progression.jsonl"

NUMERIC_FIELDS = (
    "valid_leads", "first_responses", "effective_consultations",
    "contacts_obtained", "followups", "appointments", "arrivals",
    "paid_cases", "refunds", "complaints", "process_minutes",
)

ALIASES = {
    "日期": "date", "员工编号": "employee_id", "员工": "employee_name",
    "员工姓名": "employee_name", "机构": "institution", "科室": "department",
    "病种": "disease_or_project", "项目": "disease_or_project", "渠道": "channel",
    "有效线索": "valid_leads", "首响数": "first_responses", "有效咨询": "effective_consultations",
    "有效咨询量": "effective_consultations", "留资数": "contacts_obtained",
    "回访数": "followups", "预约数": "appointments", "到院数": "arrivals",
    "付费数": "paid_cases", "退款数": "refunds", "投诉数": "complaints",
    "处理分钟": "process_minutes",
}


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def locate_workspace(selected):
    selected = expand_path(selected)
    if (selected / "_系统").is_dir() and (selected / "08_团队管理").is_dir():
        return selected
    child = selected / WORKSPACE_NAME
    if (child / "_系统").is_dir() and (child / "08_团队管理").is_dir():
        return child
    raise ValueError("未找到标准咨询转化工作区：{0}".format(selected))


def management_root(workspace):
    root = locate_workspace(workspace)
    path = root / "_系统" / MANAGEMENT_DIR
    ensure_dir(path)
    return path


def load_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def save_json(path, value):
    path = Path(path)
    ensure_dir(path.parent)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def load_jsonl(path):
    rows = []
    path = Path(path)
    if not path.is_file():
        return rows
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    rows.append({"_invalid": True, "_line": line_number})
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except IOError:
        return []
    return rows


def append_jsonl(path, row):
    path = Path(path)
    ensure_dir(path.parent)
    with io.open(str(path), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def latest_by(rows, id_field, updated_field="updated_at"):
    latest = {}
    for row in rows:
        if row.get("_invalid"):
            continue
        key = row.get(id_field)
        if not key:
            continue
        stamp = row.get(updated_field) or row.get("created_at") or ""
        previous = latest.get(key)
        previous_stamp = (previous or {}).get(updated_field) or (previous or {}).get("created_at") or ""
        if previous is None or stamp >= previous_stamp:
            latest[key] = row
    return latest


def dedupe_samples(rows):
    """Keep the newest stable record, then collapse repeated source files."""
    stable_latest = latest_by(rows, "sample_id")
    candidates = list(stable_latest.values())
    candidates.extend(row for row in rows if not row.get("sample_id") and not row.get("_invalid"))
    latest = {}
    for row in candidates:
        if row.get("_invalid"):
            continue
        key = row.get("source_hash") or row.get("sample_id")
        if not key:
            continue
        stamp = row.get("updated_at") or row.get("created_at") or ""
        previous = latest.get(key)
        previous_stamp = (previous or {}).get("updated_at") or (previous or {}).get("created_at") or ""
        if previous is None or stamp >= previous_stamp:
            latest[key] = row
    return list(latest.values())


def period_bounds(period, date_value):
    anchor = datetime.datetime.strptime(date_value, "%Y-%m-%d").date()
    if period == "today":
        start = end = anchor
    elif period == "week":
        start = anchor - datetime.timedelta(days=anchor.weekday())
        end = start + datetime.timedelta(days=6)
    elif period == "month":
        start = anchor.replace(day=1)
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1)
        else:
            next_month = start.replace(month=start.month + 1)
        end = next_month - datetime.timedelta(days=1)
    else:
        raise ValueError("period must be today, week or month")
    return start.isoformat(), end.isoformat()


def in_period(value, start, end):
    value = str(value or "")[:10]
    return bool(value and start <= value <= end)


def normalize_row(row):
    normalized = {}
    for key, value in row.items():
        key = ALIASES.get(str(key).strip(), str(key).strip())
        normalized[key] = value.strip() if isinstance(value, str) else value
    for field in NUMERIC_FIELDS:
        value = normalized.get(field)
        if value in (None, ""):
            normalized[field] = None
            continue
        try:
            normalized[field] = float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            normalized[field] = None
    return normalized


def read_team_data(workspace):
    root = locate_workspace(workspace)
    data_root = root / "08_团队管理" / "03_团队数据"
    rows = []
    if not data_root.is_dir():
        return rows
    for path in sorted(data_root.rglob("*")):
        if not path.is_file() or path.name in ("过程量数据模板.csv", "结果数据模板.csv"):
            continue
        if path.suffix.lower() == ".csv":
            try:
                with io.open(str(path), "r", encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        item = normalize_row(row)
                        item["_source"] = str(path.relative_to(root))
                        rows.append(item)
            except (IOError, UnicodeError):
                continue
        elif path.suffix.lower() == ".json":
            value = load_json(path, [])
            if isinstance(value, dict):
                value = value.get("records", [value])
            if isinstance(value, list):
                for row in value:
                    if isinstance(row, dict):
                        item = normalize_row(row)
                        item["_source"] = str(path.relative_to(root))
                        rows.append(item)
    return rows


def metric(numerator, denominator, numerator_known, denominator_known):
    if not numerator_known or not denominator_known or denominator in (None, 0):
        return {"status": "missing", "value": None, "numerator": numerator if numerator_known else None,
                "denominator": denominator if denominator_known else None}
    return {"status": "known", "value": numerator / denominator,
            "numerator": numerator, "denominator": denominator}


def sum_field(rows, field):
    values = [row.get(field) for row in rows if row.get(field) is not None]
    return (sum(values), bool(values))


def aggregate_metrics(rows):
    totals = {}
    known = {}
    for field in NUMERIC_FIELDS:
        totals[field], known[field] = sum_field(rows, field)
        if not known[field]:
            totals[field] = None
    return {
        "totals": totals,
        "metrics": {
            "consultation_conversion_rate": metric(totals.get("appointments"), totals.get("effective_consultations"), known["appointments"], known["effective_consultations"]),
            "appointment_arrival_rate": metric(totals.get("arrivals"), totals.get("appointments"), known["arrivals"], known["appointments"]),
            "effective_consultation_arrival_rate": metric(totals.get("arrivals"), totals.get("effective_consultations"), known["arrivals"], known["effective_consultations"]),
            "arrival_paid_rate": metric(totals.get("paid_cases"), totals.get("arrivals"), known["paid_cases"], known["arrivals"]),
        },
        "record_count": len(rows),
        "data_sources": sorted(set(row.get("_source") for row in rows if row.get("_source"))),
    }


def task_state(events):
    """Fold append-only management events into current tasks."""
    tasks = {}
    status_map = {"create": "pending", "start": "in_progress", "complete": "completed",
                  "review": "reviewed", "reject": "rejected"}
    for event in sorted(events, key=lambda item: item.get("created_at") or ""):
        task_id = event.get("task_id")
        if not task_id:
            continue
        task = tasks.setdefault(task_id, {"task_id": task_id})
        for field in ("priority", "type", "target", "reason", "action", "due_date", "source_refs", "command", "duration_minutes"):
            if event.get(field) not in (None, ""):
                task[field] = event.get(field)
        task["status"] = event.get("status") or status_map.get(event.get("event"), task.get("status", "pending"))
        task["updated_at"] = event.get("created_at") or task.get("updated_at")
        if event.get("note"):
            task["latest_note"] = event.get("note")
        if event.get("review_sample"):
            task.setdefault("review_samples", []).append(event.get("review_sample"))
    return list(tasks.values())


def safe_relative(path, root):
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)
