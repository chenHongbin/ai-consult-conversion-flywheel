#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create a manager approval receipt before publishing a candidate."""

import argparse
import json
import sys

from approval_ledger import COMPONENTS, create_approval


def main():
    parser = argparse.ArgumentParser(description="Approve a candidate hash in the independent manager ledger.")
    parser.add_argument("workspace_root")
    parser.add_argument("--component", choices=COMPONENTS, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--note", default="")
    parser.add_argument("--check", action="append", default=[],
                        choices=("evaluation", "coverage", "privacy", "clinical"))
    args = parser.parse_args()
    try:
        receipt = create_approval(args.workspace_root, args.component, args.candidate, args.reviewer, args.note, args.check)
    except (IOError, OSError, ValueError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "approved", "approval": receipt}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
