#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Record a team or employee single-action training assignment."""

import argparse
import json

from management_data import TRAINING_FILE, append_jsonl, management_root, now_iso


def split_values(value):
    return [item.strip() for item in (value or "").split("|") if item.strip()]


def main():
    parser = argparse.ArgumentParser(description="Record one v2.0 training action.")
    parser.add_argument("workspace_root")
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--scope", choices=("team", "employee"), default="team")
    parser.add_argument("--target-id", default="team")
    parser.add_argument("--title", required=True)
    parser.add_argument("--reason", default="")
    parser.add_argument("--key-action", required=True)
    parser.add_argument("--pass-criteria", required=True)
    parser.add_argument("--review-method", default="每人提交一条新沟通样本复查")
    parser.add_argument("--champion-refs", default="")
    parser.add_argument("--failure-refs", default="")
    parser.add_argument("--review-samples", default="")
    parser.add_argument("--source-employee", default="", help="该动作最初来自哪位销冠/咨询师")
    parser.add_argument("--adopted-employees", default="")
    parser.add_argument("--passed-employees", default="")
    parser.add_argument("--status", choices=("pending", "in_training", "awaiting_review", "passed", "closed"), default="pending")
    args = parser.parse_args()
    timestamp = now_iso()
    row = {
        "schema_version": "2.0-training-action", "action_id": args.action_id,
        "scope": args.scope, "target_id": args.target_id, "title": args.title, "topic": args.title,
        "reason": args.reason, "key_action": args.key_action, "pass_criteria": args.pass_criteria,
        "review_method": args.review_method, "champion_refs": split_values(args.champion_refs),
        "failure_refs": split_values(args.failure_refs), "review_samples": split_values(args.review_samples),
        "source_employee": args.source_employee, "adopted_employees": split_values(args.adopted_employees),
        "passed_employees": split_values(args.passed_employees),
        "status": args.status, "created_at": timestamp, "updated_at": timestamp,
    }
    path = management_root(args.workspace_root) / TRAINING_FILE
    append_jsonl(path, row)
    print(json.dumps({"status": "recorded", "action_id": args.action_id, "path": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
