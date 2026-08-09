#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a privacy-conscious weekly team management report from local CSV/JSON data."""

import argparse
import csv
import datetime
import io
import json
import os
import sys
from pathlib import Path

from compat import ensure_dir, expand_path


NUMERIC_FIELDS = [
    "valid_leads", "first_responses", "effective_consultations",
    "contacts_obtained", "followups", "appointments", "arrivals",
    "paid_cases", "refunds", "complaints", "process_minutes",
]

ALIASES = {
    "日期": "date", "员工编号": "employee_id", "员工": "employee_name",
    "员工姓名": "employee_name", "机构": "institution", "科室": "department",
    "病种": "disease_or_project", "项目": "disease_or_project", "渠道": "channel",
    "有效线索": "valid_leads", "首响数": "first_responses", "有效咨询": "effective_consultations",
    "有效咨询量": "effective_consultations", "留资数": "contacts_obtained",
    "回访数": "followups", "预约数": "appointments", "到院数": "arrivals",
    "付费数": "paid_cases", "退款数": "refunds", "投诉数": "complaints",
    "过程分钟": "process_minutes",
}


def normalize_key(value):
    key = (value or "").strip()
    return ALIASES.get(key, key.lower().replace(" ", "_"))


def parse_number(value):
    if value is None:
        return 0.0
    text = str(value).strip().replace(",", "")
    if not text:
        return 0.0
    if text.endswith("%"):
        try:
            return float(text[:-1]) / 100.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def read_csv(path):
    rows = []
    with io.open(str(path), "r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            row = {normalize_key(k): (v or "").strip() for k, v in raw.items() if k}
            row["_source"] = str(path)
            rows.append(row)
    return rows


def read_json(path):
    with io.open(str(path), "r", encoding="utf-8") as handle:
        text = handle.read()
    if path.suffix.lower() == ".jsonl":
        values = []
        for line in text.splitlines():
            if line.strip():
                values.append(json.loads(line))
    else:
        values = json.loads(text)
        if isinstance(values, dict):
            values = values.get("records", [values])
    rows = []
    for value in values:
        if not isinstance(value, dict):
            continue
        row = {normalize_key(k): value[k] for k in value}
        row["_source"] = str(path)
        rows.append(row)
    return rows


def read_records(folder):
    rows = []
    if not folder.is_dir():
        return rows
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("~"):
            continue
        try:
            if path.suffix.lower() == ".csv":
                rows.extend(read_csv(path))
            elif path.suffix.lower() in (".json", ".jsonl"):
                rows.extend(read_json(path))
        except (IOError, ValueError, TypeError) as exc:
            sys.stderr.write("skip {}: {}\n".format(path.name, exc))
    return rows


def in_period(value, start, end):
    text = str(value or "").strip()[:10].replace("/", "-")
    if not text:
        return False
    return start <= text <= end


def employee_key(row):
    return str(row.get("employee_id") or row.get("employee_name") or "未标记员工").strip()


def display_name(row):
    return str(row.get("employee_name") or row.get("employee_id") or "未标记员工").strip()


def aggregate(rows):
    result = {}
    for row in rows:
        key = employee_key(row)
        if key not in result:
            result[key] = {"employee_id": row.get("employee_id", ""),
                           "employee_name": display_name(row),
                           "records": 0}
            for field in NUMERIC_FIELDS:
                result[key][field] = 0.0
        item = result[key]
        item["records"] += 1
        for field in NUMERIC_FIELDS:
            item[field] += parse_number(row.get(field))
    return result


def ratio(numerator, denominator):
    if not denominator:
        return None
    return numerator / denominator


def pct(value):
    if value is None:
        return "无法判断"
    return "{:.1f}%".format(value * 100)


def load_json(path, default):
    if not path.is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def make_report(root, start, end, employee_filter=None):
    team = root / "08_团队管理"
    rows = []
    member_root = team / "01_成员"
    if member_root.is_dir():
        for data_folder in member_root.rglob("*"):
            if data_folder.is_dir() and data_folder.name in ("04_过程量数据", "05_结果数据"):
                rows.extend(read_records(data_folder))
    if not rows:
        rows = read_records(team / "03_团队数据")
        if not rows:
            rows = read_records(team / "03_过程量数据") + read_records(team / "04_结果数据")
    rows = [row for row in rows if in_period(row.get("date"), start, end)]
    if employee_filter:
        rows = [row for row in rows if employee_filter in (employee_key(row), display_name(row))]
    people = aggregate(rows)
    communication_count = 0
    communication_roots = []
    if member_root.is_dir():
        communication_roots = [
            path for path in member_root.rglob("03_一对一沟通") if path.is_dir()
        ]
    if not communication_roots:
        communication_roots = [team / "02_员工沟通记录"]
    for communication_folder in communication_roots:
        for path in communication_folder.rglob("*"):
            if path.is_file() and start <= path.name[:10].replace("/", "-") <= end:
                communication_count += 1
    baseline = load_json(root / "_系统" / "团队基线.json", {})
    metrics = load_json(root / "_系统" / "指标口径.json", {})
    baseline_ready = bool(baseline.get("metrics")) and baseline.get("status") not in ("待补充", "未完成")

    lines = [
        "# 团队管理周报",
        "",
        "周期：{} 至 {}".format(start, end),
        "",
        "## 三行结论",
        "",
        "- 本周状态：共 {} 名员工、{} 条结构化数据记录、{} 条一对一沟通资料。".format(
            len(people), len(rows), communication_count),
        "- 最主要断点：{}。".format("暂无足够数据判断" if not rows else "需要结合以下员工卡逐人判断"),
        "- 本周只做一个动作：先补齐缺失的结果数据，再为每名员工选择一个可观察的咨询动作。",
        "",
        "## 口径与数据质量",
        "",
        "- 咨询转化率默认定义为：预约数 / 有效咨询量。",
        "- 到院率默认定义为：到院数 / 预约数。",
        "- 当前 Skill 不会在分母为零、周期不完整或口径变化时制造百分比。",
        "- 机构自定义口径：{}。".format(json.dumps(metrics, ensure_ascii=False) if metrics else "尚未配置"),
        "",
    ]
    if not people:
        lines.extend([
            "## 当前无法判断",
            "",
            "请把包含 date、employee_id 或 employee_name 的 CSV/JSON/JSONL 放入 `08_团队管理/03_团队数据/01_今天放这里`，且日期落在本报告周期内。",
            "不要用本报告推断团队业绩或员工状态。",
        ])
    else:
        lines.extend(["## 员工卡", ""])
        for key in sorted(people):
            item = people[key]
            consultation_rate = ratio(item["appointments"], item["effective_consultations"])
            arrival_rate = ratio(item["arrivals"], item["appointments"])
            paid_rate = ratio(item["paid_cases"], item["arrivals"])
            contact_rate = ratio(item["contacts_obtained"], item["valid_leads"])
            lines.extend([
                "### {}（{}）".format(item["employee_name"], item["employee_id"] or "无编号"),
                "",
                "- 数据记录：{}；有效线索：{}；有效咨询：{}；回访：{}。".format(
                    item["records"], int(item["valid_leads"]), int(item["effective_consultations"]), int(item["followups"])),
                "- 留资率：{}；咨询转化率：{}；到院率：{}；付费率：{}。".format(
                    pct(contact_rate), pct(consultation_rate), pct(arrival_rate), pct(paid_rate)),
                "- 结果：预约 {}，到院 {}，付费 {}，退款 {}，投诉 {}。".format(
                    int(item["appointments"]), int(item["arrivals"]), int(item["paid_cases"]), int(item["refunds"]), int(item["complaints"])),
                "- 本周唯一管理动作：选择一个阶段和一个行为进行陪练，约定复查样本；不得仅凭数量排名。",
                "- 需要补充：员工近一个月一对一沟通摘要、渠道/病种口径和未完成结果事件。",
                "",
            ])
    lines.extend([
        "## 翻倍目标状态",
        "",
        "- 当前仅生成目标追踪框架，不把目标当作已实现结果。",
        "- 基线文件：{}。".format("已配置" if baseline_ready else "未完成，暂不能判断翻倍"),
        "- 只有在同机构、同科室/病种、同渠道、同口径且有完整基线和结果周期时，才能比较咨询转化率或到院率是否接近基线的 2 倍。",
        "",
        "## 下周复查",
        "",
        "- 管理者补齐本周缺失的结果数据；",
        "- 每名员工完成一个单动作陪练并记录实际采用情况；",
        "- 月度一对一沟通后，把员工确认的支持动作放入成员目录的 `02_辅导方案`；",
        "- 不将本报告自动写入绩效定级或机构能力包。",
    ])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Generate a team management report.")
    parser.add_argument("workspace_root", help="咨询转化工作区路径")
    parser.add_argument("--start", required=True, help="周期开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="周期结束日期 YYYY-MM-DD")
    parser.add_argument("--employee", help="只生成某位员工的报告")
    parser.add_argument("--kind", choices=["daily", "weekly", "monthly"], default="weekly")
    args = parser.parse_args()
    try:
        datetime.datetime.strptime(args.start, "%Y-%m-%d")
        datetime.datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError:
        parser.error("日期必须是 YYYY-MM-DD")
    root = expand_path(args.workspace_root)
    if args.end < args.start:
        parser.error("结束日期不能早于开始日期")
    report = make_report(root, args.start, args.end, args.employee)
    report_folders = {"daily": "01_日报", "weekly": "02_周报", "monthly": "03_月报"}
    output_dir = root / "08_团队管理" / "04_团队报告" / report_folders[args.kind]
    ensure_dir(output_dir)
    suffix = "-{}".format(args.employee) if args.employee else ""
    labels = {"daily": "日报", "weekly": "周报", "monthly": "月报"}
    output = output_dir / "{}-{}-{}{}.md".format(labels[args.kind], args.start, args.end, suffix)
    with io.open(str(output), "w", encoding="utf-8") as handle:
        handle.write(report)
    print(json.dumps({"report": str(output), "records_scanned": "see report"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
