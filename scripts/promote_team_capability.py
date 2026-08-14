#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evaluate and record the five-stage Nuwa capability progression."""

import argparse
import json

from management_data import CAPABILITY_FILE, append_jsonl, latest_by, load_jsonl, management_root, now_iso


STAGES = ("candidate_experience", "trainable_action", "behavior_verified", "outcome_verified", "team_capability")


def split_values(value):
    return sorted(set(item.strip() for item in (value or "").split("|") if item.strip()))


def eligible_stage(args):
    support = split_values(args.support_cases)
    counterexamples = split_values(args.counterexamples)
    conditions = split_values(args.applicable_conditions)
    employees = split_values(args.non_source_employees)
    review_counts = {}
    for item in split_values(args.review_counts):
        pair = item.split(":", 1)
        if len(pair) == 2:
            try:
                review_counts[pair[0]] = int(pair[1])
            except ValueError:
                pass
    verified_employees = [employee for employee in employees if review_counts.get(employee, 0) >= 2]
    stable_total = sum(review_counts.get(employee, 0) for employee in employees)
    all_two = len(verified_employees) >= 2
    stage = "candidate_experience"
    reasons = []
    if len(support) >= 2 and conditions and counterexamples and args.manager_reviewed == "yes":
        stage = "trainable_action"
    else:
        reasons.append("可训练动作需要至少2个支持案例、1个适用条件、1个反例和主管审核")
    if stage == "trainable_action" and len(employees) >= 2 and all_two and stable_total >= 3:
        stage = "behavior_verified"
    elif args.request_stage in STAGES[2:]:
        reasons.append("行为验证需要至少2名非来源咨询师，每人至少2个复查样本")
    if stage == "behavior_verified" and args.outcome_data == "yes" and args.outcome_improved == "yes":
        stage = "outcome_verified"
    elif args.request_stage in STAGES[3:]:
        reasons.append("结果验证需要预约或到院结果数据且观察到改善")
    if stage == "outcome_verified" and args.release_approved == "yes":
        stage = "team_capability"
    elif args.request_stage == "team_capability":
        reasons.append("团队正式能力需要统一发布审核通过")
    return stage, reasons


def decide_stage(args, current_stage):
    eligible, reasons = eligible_stage(args)
    current_index = STAGES.index(current_stage)
    requested_index = STAGES.index(args.request_stage)
    eligible_index = STAGES.index(eligible)
    if requested_index < current_index:
        return current_stage, reasons + ["能力不能降级；需要回滚时使用统一发布回滚机制"]
    allowed_index = min(current_index + 1, eligible_index, requested_index)
    if requested_index > current_index + 1:
        reasons.append("能力必须按五级顺序晋升，本次最多前进一级")
    return STAGES[allowed_index], reasons


def main():
    parser = argparse.ArgumentParser(description="Promote one Nuwa capability through the v2.0 evidence gates.")
    parser.add_argument("workspace_root")
    parser.add_argument("--capability-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--request-stage", choices=STAGES, required=True)
    parser.add_argument("--support-cases", default="")
    parser.add_argument("--counterexamples", default="")
    parser.add_argument("--applicable-conditions", default="")
    parser.add_argument("--manager-reviewed", choices=("yes", "no"), default="no")
    parser.add_argument("--non-source-employees", default="")
    parser.add_argument("--review-counts", default="", help="A001:2|A002:2")
    parser.add_argument("--outcome-data", choices=("yes", "no"), default="no")
    parser.add_argument("--outcome-improved", choices=("yes", "no", "unknown"), default="unknown")
    parser.add_argument("--release-approved", choices=("yes", "no"), default="no")
    parser.add_argument("--personalization-chain", default="")
    parser.add_argument("--reassurance-chain", default="")
    parser.add_argument("--replication-chain", default="")
    args = parser.parse_args()
    path = management_root(args.workspace_root) / CAPABILITY_FILE
    existing = latest_by(load_jsonl(path), "capability_id").get(args.capability_id, {})
    current_stage = existing.get("stage") if existing.get("stage") in STAGES else "candidate_experience"
    stage, reasons = decide_stage(args, current_stage)
    row = {
        "schema_version": "2.0-capability-progression", "capability_id": args.capability_id,
        "name": args.name, "previous_stage": current_stage, "stage": stage, "requested_stage": args.request_stage,
        "support_case_ids": split_values(args.support_cases), "counterexample_ids": split_values(args.counterexamples),
        "applicable_conditions": split_values(args.applicable_conditions), "manager_reviewed": args.manager_reviewed == "yes",
        "non_source_employees": split_values(args.non_source_employees), "review_counts": split_values(args.review_counts),
        "outcome_data_available": args.outcome_data == "yes", "outcome_improved": args.outcome_improved,
        "release_approved": args.release_approved == "yes", "personalization_chain": args.personalization_chain,
        "reassurance_chain": args.reassurance_chain, "replication_chain": args.replication_chain,
        "blocked_reasons": reasons, "updated_at": now_iso(),
    }
    append_jsonl(path, row)
    print(json.dumps({"status": "recorded", "capability_id": args.capability_id, "stage": stage,
                      "requested_stage": args.request_stage, "blocked_reasons": reasons, "path": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
