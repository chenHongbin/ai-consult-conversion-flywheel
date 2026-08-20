#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append or update one structured communication sample for manager analysis."""

import argparse
import hashlib
import json
from pathlib import Path

from management_data import SAMPLES_FILE, append_jsonl, management_root, now_iso
from daily_review import stable_id


def split_values(value):
    return [item.strip() for item in (value or "").split("|") if item.strip()]


def main():
    parser = argparse.ArgumentParser(description="Record a V2.1 evidence-linked communication sample card.")
    parser.add_argument("workspace_root")
    parser.add_argument("--sample-id", default="")
    parser.add_argument("--artifact-id", default="")
    parser.add_argument("--conversation-id", default="")
    parser.add_argument("--patient-case-id", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--source-hash", default="")
    parser.add_argument("--employee-id", required=True)
    parser.add_argument("--employee-name", default="")
    parser.add_argument("--date", required=True)
    parser.add_argument("--medium", choices=("audio", "wechat", "chat", "image", "other"), default="other")
    parser.add_argument("--stage", default="unknown")
    parser.add_argument("--patient-facts", default="")
    parser.add_argument("--uncertainty", default="unknown")
    parser.add_argument("--breakpoint", default="unknown")
    parser.add_argument("--consultant-actions", default="")
    parser.add_argument("--evidence", default="")
    parser.add_argument("--next-service-action", default="")
    parser.add_argument("--employee-gap", default="")
    parser.add_argument("--verified-strength", default="")
    parser.add_argument("--team-pattern", default="")
    parser.add_argument("--outcome", default="unknown")
    parser.add_argument("--outcome-provenance", default="missing")
    parser.add_argument("--response-draft", default="")
    parser.add_argument("--content-prescription", default="")
    args = parser.parse_args()
    source_hash = args.source_hash
    if not source_hash:
        source_path = Path(args.source)
        if source_path.is_file():
            digest = hashlib.sha256()
            with source_path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            source_hash = digest.hexdigest()
        else:
            identity = json.dumps({
                "source": args.source, "employee_id": args.employee_id, "date": args.date,
                "medium": args.medium, "evidence": args.evidence,
                "patient_facts": args.patient_facts, "consultant_actions": args.consultant_actions,
            }, ensure_ascii=False, sort_keys=True)
            source_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    artifact_id = args.artifact_id or stable_id("ART", source_hash)
    conversation_id = args.conversation_id or stable_id("CONV", args.employee_id, source_hash)
    patient_case_id = args.patient_case_id or stable_id("PC", conversation_id)
    consultant_day_id = stable_id("CD", args.employee_id, args.date)
    team_day_id = stable_id("TD", args.date)
    sample_id = args.sample_id or conversation_id
    timestamp = now_iso()
    row = {
        "schema_version": "2.1-communication-sample", "sample_id": sample_id,
        "artifact_id": artifact_id, "conversation_id": conversation_id,
        "patient_case_id": patient_case_id, "consultant_day_id": consultant_day_id,
        "team_day_id": team_day_id,
        "source": args.source, "source_hash": source_hash, "employee_id": args.employee_id,
        "employee_name": args.employee_name, "date": args.date, "medium": args.medium,
        "stage": args.stage, "patient_facts": split_values(args.patient_facts),
        "patient_uncertainty": args.uncertainty, "uncertainties": split_values(args.uncertainty), "breakpoint": args.breakpoint,
        "consultant_actions": split_values(args.consultant_actions), "evidence_refs": split_values(args.evidence),
        "next_patient_service_action": args.next_service_action, "patient_next_action": args.next_service_action,
        "employee_gap": args.employee_gap,
        "verified_strength": args.verified_strength, "team_candidate_pattern": args.team_pattern,
        "team_pattern_candidate": args.team_pattern, "outcome": args.outcome,
        "outcome_provenance": args.outcome_provenance, "outcome_source": args.outcome_provenance,
        "response_draft": args.response_draft, "content_prescription": args.content_prescription,
        "created_at": timestamp, "updated_at": timestamp,
    }
    path = management_root(args.workspace_root) / SAMPLES_FILE
    append_jsonl(path, row)
    print(json.dumps({"status": "recorded", "sample_id": sample_id, "path": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
