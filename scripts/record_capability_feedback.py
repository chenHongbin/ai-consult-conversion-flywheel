#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append manager feedback for the next capability-package cycle."""

import argparse
import datetime
import hashlib
import io
import json
import sys
from pathlib import Path

from compat import ensure_dir, expand_path


def main():
    parser = argparse.ArgumentParser(description="Record feedback linked to an institution capability rule.")
    parser.add_argument("workspace_root")
    parser.add_argument("--rule-id", required=True)
    parser.add_argument("--decision", choices=("keep", "modify", "reject"), required=True)
    parser.add_argument("--note", required=True)
    parser.add_argument("--evidence-ref", action="append", default=[])
    parser.add_argument("--operator", default="管理者")
    args = parser.parse_args()
    workspace = expand_path(args.workspace_root)
    output = workspace / "咨询转化工作区" / "_系统" / "当前能力包" / "feedback.jsonl"
    ensure_dir(output.parent)
    row = {
        "feedback_id": "fb-" + hashlib.sha1((args.rule_id + args.note + datetime.datetime.now().isoformat()).encode("utf-8")).hexdigest()[:12],
        "created_at": datetime.datetime.now().isoformat(),
        "rule_id": args.rule_id,
        "decision": args.decision,
        "note": args.note,
        "evidence_refs": args.evidence_ref,
        "operator": args.operator,
    }
    with io.open(str(output), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"status": "recorded", "feedback_path": str(output), "feedback_id": row["feedback_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
