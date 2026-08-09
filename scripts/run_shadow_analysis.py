#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create the first-use shadow analysis queue after a full distillation.

The Agent performs the language-model analysis from this queue. This script
selects representative, redacted cases and makes the next action automatic;
it never requires the user to classify every source first.
"""

import argparse
import datetime
import io
import json
from pathlib import Path

from compat import ensure_dir, expand_path


def load_rows(path):
    rows = []
    if not path or not path.is_file():
        return rows
    with io.open(str(path), "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("text"):
                rows.append(row)
    return rows


def choose_rows(rows, limit):
    chosen = []
    used_clusters = set()
    role_order = ("positive_reference", "negative_reference", "comparison_case", "unknown_case")
    for role in role_order:
        for row in rows:
            cluster = row.get("dedup_cluster_id") or row.get("case_id")
            if row.get("sample_role") != role or cluster in used_clusters:
                continue
            chosen.append(row)
            used_clusters.add(cluster)
            break
        if len(chosen) >= limit:
            return chosen[:limit]
    for row in rows:
        cluster = row.get("dedup_cluster_id") or row.get("case_id")
        if cluster in used_clusters:
            continue
        chosen.append(row)
        used_clusters.add(cluster)
        if len(chosen) >= limit:
            break
    return chosen


def find_latest_batch(workspace):
    root = expand_path(workspace) / "咨询转化工作区" / "_系统" / "案例标准化"
    batches = sorted(root.glob("蒸馏批次-*.jsonl"))
    return batches[-1] if batches else None


def main():
    parser = argparse.ArgumentParser(description="Create a first-use shadow analysis queue.")
    parser.add_argument("workspace_root")
    parser.add_argument("--batch", help="standardized batch JSONL; defaults to latest batch")
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    workspace = expand_path(args.workspace_root)
    batch = expand_path(args.batch) if args.batch else find_latest_batch(workspace)
    rows = load_rows(batch)
    if not rows:
        print(json.dumps({"status": "waiting_for_cases", "batch": str(batch) if batch else None}, ensure_ascii=False))
        return 3
    selected = choose_rows(rows, max(1, args.count))
    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = workspace / "咨询转化工作区" / "_系统" / "影子试用" / run_id
    ensure_dir(output_root)
    queue_path = output_root / "analysis-queue.jsonl"
    tasks = []
    for index, row in enumerate(selected, 1):
        task_id = "shadow-{0:02d}-{1}".format(index, row.get("case_id", "case"))
        tasks.append({
            "task_id": task_id,
            "case_id": row.get("case_id"),
            "source_path": row.get("source_path"),
            "derived_from_source": row.get("derived_from_source"),
            "sample_role": row.get("sample_role"),
            "dedup_cluster_id": row.get("dedup_cluster_id"),
            "outcome": row.get("outcome", "待确认"),
            "transcript_quality": row.get("transcript_quality", row.get("ocr_quality", "unknown")),
            "prompt": (
                "请使用当前候选咨询能力包、机构知识（未确认事实不得补写）、患者决策洞察和咨询转化八步法，"
                "分析这条材料。输出赖老师自动化分析器风格的咨询复盘报告：阶段、患者原话、候选顾虑、"
                "咨询师做对的地方、流失节点、销冠动作、下一步建议、禁用表达、医疗边界和一个新人训练动作。"
                "明确区分已观察事实与 AI 假设；不要因为结果未知而跳过。"
            ),
            "text": row.get("text"),
            "status": "queued",
        })
    with io.open(str(queue_path), "w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task, ensure_ascii=False) + "\n")
    report_path = output_root / "影子试用说明.md"
    with io.open(str(report_path), "w", encoding="utf-8") as handle:
        handle.write("# 首次蒸馏后影子试用\n\n")
        handle.write("这不是正式团队发布版，而是让管理者先验证候选能力是否能分析真实案例。\n\n")
        handle.write("- 样本总数：{0}\n- 本次试用：{1}\n- 批次：`{2}`\n\n".format(len(rows), len(tasks), batch))
        handle.write("## 自动选择的案例\n\n")
        for task in tasks:
            handle.write("- `{0}`：{1}；来源 `{2}`；结果 `{3}`\n".format(
                task["task_id"], task.get("sample_role"), task.get("source_path"), task.get("outcome")))
        handle.write("\n请先完成这几条分析，再决定是否补充机构价格、医生和地址等事实。\n")
    print(json.dumps({"status": "ready", "run_id": run_id, "selected": len(tasks),
                      "queue": str(queue_path), "report": str(report_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
