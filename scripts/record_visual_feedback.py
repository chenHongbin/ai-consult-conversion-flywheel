#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Record non-sensitive usage feedback for a visual asset."""

import argparse
import datetime
import io
import json
import sys
from pathlib import Path

from compat import ensure_dir, expand_path
from workspace_paths import locate_workspace


def main():
    parser = argparse.ArgumentParser(description="Record visual asset usage feedback.")
    parser.add_argument("workspace_root")
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--status", choices=("draft", "sent", "skipped", "revised"), default="sent")
    parser.add_argument("--channel", choices=("chat", "moments", "group", "training"), default="chat")
    parser.add_argument("--replied", choices=("unknown", "yes", "no"), default="unknown")
    parser.add_argument("--appointed", choices=("unknown", "yes", "no"), default="unknown")
    parser.add_argument("--arrived", choices=("unknown", "yes", "no"), default="unknown")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    root = expand_path(args.workspace_root)
    feedback_path = locate_workspace(root) / "_系统" / "视觉反馈" / "feedback.jsonl"
    ensure_dir(feedback_path.parent)
    record = {
        "schema_version": "1.0-visual-feedback",
        "recorded_at": datetime.datetime.now().isoformat(),
        "asset_id": args.asset_id,
        "status": args.status,
        "channel": args.channel,
        "replied": args.replied,
        "appointed": args.appointed,
        "arrived": args.arrived,
        "note": args.note,
    }
    with io.open(str(feedback_path), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    sys.stdout.write(json.dumps({"status": "recorded", "path": str(feedback_path), "record": record}, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
