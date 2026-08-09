#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate the sanitized JSONL regression set without exposing its contents."""
import json
import io
import re
import sys
from pathlib import Path

REQUIRED = {
    "case_id", "source_id", "source_nature", "department", "disease_or_project",
    "channel", "medium", "stage", "input", "patient_intent", "patient_concern",
    "consultant_actions", "outcome", "evidence_locator", "authorization_status",
    "redaction_status", "consent_status", "clinical_risk", "privacy_risk",
    "external_action_risk", "quality_label", "label_confidence", "review_status", "split",
}
PII_PATTERNS = [
    re.compile(r"(?<!\d)1\d{10}(?!\d)"),
    re.compile(r"(?<!\d)\d{15}(?:\d{2}[0-9Xx])?(?!\d)"),
    re.compile(r"(?i)wxid_[a-z0-9_-]+"),
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
]

def main():
    if len(sys.argv) != 2:
        print("usage: validate_test_set.py CASES.jsonl", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print("missing file", file=sys.stderr)
        return 2
    issues = []
    count = 0
    ids = set()
    with io.open(str(path), "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        count += 1
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append("line {}: invalid JSON ({})".format(line_no, exc.msg))
            continue
        case_id = item.get("case_id", "line-{}".format(line_no))
        if case_id in ids:
            issues.append("line {}: duplicate case_id".format(line_no))
        ids.add(case_id)
        missing = sorted(REQUIRED - set(item))
        if missing:
            issues.append("{}: missing {}".format(case_id, ",".join(missing)))
        blob = json.dumps(item, ensure_ascii=False)
        if any(pattern.search(blob) for pattern in PII_PATTERNS):
            issues.append("{}: possible PII detected".format(case_id))
        if item.get("redaction_status") != "complete":
            issues.append("{}: redaction_status is not complete".format(case_id))
    if issues:
        print("FAIL: {} issue(s) in {} case(s)".format(len(issues), count))
        for issue in issues:
            print("- {}".format(issue))
        return 1
    print("PASS: {} sanitized case(s), no duplicate IDs or obvious PII".format(count))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
