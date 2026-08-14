#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append one manager task event without rewriting prior history."""

import argparse
import json

from management_data import EVENTS_FILE, append_jsonl, management_root, now_iso


def split_values(value):
    return [item.strip() for item in (value or "").split("|") if item.strip()]


def main():
    parser = argparse.ArgumentParser(description="Record a manager workbench task event.")
    parser.add_argument("workspace_root")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--event", choices=("create", "start", "complete", "review", "reject"), required=True)
    parser.add_argument("--note", default="")
    parser.add_argument("--review-sample", default="")
    parser.add_argument("--priority", choices=("P0", "P1", "P2"), default="")
    parser.add_argument("--type", default="")
    parser.add_argument("--target", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--action", default="")
    parser.add_argument("--due-date", default="")
    parser.add_argument("--source-refs", default="")
    parser.add_argument("--minutes", type=float, default=0, help="本次主管处理耗时，分钟")
    args = parser.parse_args()
    row = {
        "schema_version": "2.0-management-event", "task_id": args.task_id,
        "event": args.event, "created_at": now_iso(), "note": args.note,
        "review_sample": args.review_sample, "priority": args.priority, "type": args.type,
        "target": args.target, "reason": args.reason, "action": args.action,
        "due_date": args.due_date, "source_refs": split_values(args.source_refs),
        "duration_minutes": args.minutes,
        "command": "处理管理任务 {0}".format(args.task_id),
    }
    path = management_root(args.workspace_root) / EVENTS_FILE
    append_jsonl(path, row)
    print(json.dumps({"status": "recorded", "task_id": args.task_id, "event": args.event,
                      "path": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
