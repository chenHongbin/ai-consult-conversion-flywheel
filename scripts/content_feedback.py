#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frontline-only content feedback recorder; it cannot approve team assets."""

import argparse
import datetime
import hashlib
import io
import json
import os
import sys

from compat import ensure_dir
from privacy_guard import scan_value
from workspace_paths import locate_workspace


CONTENT_TYPES = ("followup", "moments", "education", "private_message")
STATUSES = ("draft", "revised", "sent", "skipped")
OUTCOMES = ("unknown", "yes", "no")
REPLY_QUALITIES = ("unknown", "positive", "neutral", "negative")


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def make_id(*values):
    raw = "|".join(str(value or "") for value in values) + "|" + now_iso()
    return "content-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def load_rows(path):
    rows = []
    if not path.is_file():
        return rows
    with io.open(str(path), "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("asset_id"):
                rows.append(row)
    return rows


def append_row(path, row):
    ensure_dir(path.parent)
    descriptor = os.open(str(path), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description="Record content usage without manager approval capability.")
    parser.add_argument("workspace_root")
    parser.add_argument("--asset-id", default="")
    parser.add_argument("--content-type", choices=CONTENT_TYPES, required=True)
    parser.add_argument("--status", choices=STATUSES, required=True)
    parser.add_argument("--output-ref", required=True)
    parser.add_argument("--case-id", default="")
    parser.add_argument("--consultant-id", default="")
    parser.add_argument("--channel", default="")
    parser.add_argument("--patient-stage", default="")
    parser.add_argument("--concern", default="")
    parser.add_argument("--voice-scope", choices=("generic", "personal", "institution"), default="generic")
    parser.add_argument("--knowledge-ref", action="append", default=[])
    parser.add_argument("--evidence-ref", action="append", default=[])
    parser.add_argument("--replied", choices=OUTCOMES, default="unknown")
    parser.add_argument("--reply-quality", choices=REPLY_QUALITIES, default="unknown")
    parser.add_argument("--appointed", choices=OUTCOMES, default="unknown")
    parser.add_argument("--arrived", choices=OUTCOMES, default="unknown")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    root = locate_workspace(args.workspace_root) / "_系统" / "内容资产"
    events_path = root / "content-events.jsonl"
    asset_id = args.asset_id or make_id(args.content_type, args.case_id, args.output_ref)
    existing = [row for row in load_rows(events_path) if row.get("asset_id") == asset_id]
    if not existing and args.status != "draft":
        print(json.dumps({"status": "rejected", "reason": "first_event_must_be_draft"}, ensure_ascii=False))
        return 2
    fields = {
        "output_ref": args.output_ref, "case_id": args.case_id, "consultant_id": args.consultant_id,
        "channel": args.channel, "patient_stage": args.patient_stage, "concern": args.concern,
        "note": args.note, "evidence_refs": args.evidence_ref, "knowledge_refs": args.knowledge_ref,
    }
    findings = scan_value(fields)
    if findings:
        print(json.dumps({"status": "rejected", "reason": "possible_personal_identifier", "findings": findings}, ensure_ascii=False))
        return 2
    row = dict(fields, schema_version="2.1.3-content-event", event_id=make_id(asset_id, args.status),
               created_at=now_iso(), asset_id=asset_id, content_type=args.content_type, status=args.status,
               voice_scope=args.voice_scope, replied=args.replied, reply_quality=args.reply_quality,
               appointed=args.appointed, arrived=args.arrived, contains_raw_patient_material=False)
    append_row(events_path, row)
    print(json.dumps({"status": "recorded", "asset_id": asset_id, "record": row}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
