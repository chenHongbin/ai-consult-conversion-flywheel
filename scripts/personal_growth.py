#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Manage a user's private growth overlay without changing team capability."""

import argparse
import datetime
import hashlib
import io
import json
import sys
from pathlib import Path

from compat import ensure_dir, expand_path


def now():
    return datetime.datetime.now().isoformat()


def root(workspace):
    return expand_path(workspace) / "咨询转化工作区" / "_系统" / "个人成长"


def file_paths(workspace):
    base = root(workspace)
    return {"root": base, "profile": base / "profile.json", "cases": base / "personal-cases.jsonl",
            "candidates": base / "personal-candidates.jsonl", "feedback": base / "personal-feedback.jsonl",
            "snapshots": base / "snapshots", "runtime": base / "runtime-manifest.json"}


def load_json(path, default):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def load_jsonl(path):
    rows = []
    path = Path(path)
    if not path.is_file():
        return rows
    with io.open(str(path), "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def append_jsonl(path, row):
    ensure_dir(Path(path).parent)
    with io.open(str(path), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_json(path, value):
    ensure_dir(Path(path).parent)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def ident(prefix, value):
    return prefix + "-" + hashlib.sha1((str(value) + now()).encode("utf-8")).hexdigest()[:12]


def command_init(args):
    paths = file_paths(args.workspace_root)
    ensure_dir(paths["root"])
    ensure_dir(paths["snapshots"])
    profile = load_json(paths["profile"], {})
    profile.setdefault("schema_version", "1.0")
    profile.setdefault("operator", args.operator or "待命名")
    profile.setdefault("created_at", now())
    profile["updated_at"] = now()
    profile["team_release"] = args.team_release or profile.get("team_release") or "base_only"
    profile.setdefault("personal_version", "Personal-v0.1")
    profile.setdefault("precedence", ["safety", "institution_facts", "team_rules", "personal_verified", "personal_candidate", "base"])
    save_json(paths["profile"], profile)
    save_json(paths["runtime"], {"schema_version": "1.0", "status": "active", "team_release": profile["team_release"],
                                  "personal_version": profile["personal_version"], "updated_at": now()})
    print(json.dumps({"status": "initialized", "profile": str(paths["profile"]),
                      "team_release": profile["team_release"], "personal_version": profile["personal_version"]}, ensure_ascii=False))


def command_case(args):
    paths = file_paths(args.workspace_root)
    profile = load_json(paths["profile"], {"team_release": "base_only", "personal_version": "Personal-v0.1"})
    row = {"case_id": ident("personal-case", args.source_id or args.summary), "created_at": now(),
           "source_id": args.source_id or "manual", "role": args.role, "summary": args.summary,
           "outcome": args.outcome, "evidence_refs": args.evidence_ref, "team_release": profile.get("team_release"),
           "status": "learning_material", "personal_only": True}
    append_jsonl(paths["cases"], row)
    print(json.dumps({"status": "recorded", "case_id": row["case_id"], "path": str(paths["cases"])}, ensure_ascii=False))


def command_rule(args):
    paths = file_paths(args.workspace_root)
    profile = load_json(paths["profile"], {"team_release": "base_only", "personal_version": "Personal-v0.1"})
    rule = {"candidate_id": ident("personal-rule", args.rule_id or args.text), "rule_id": args.rule_id or ident("personal", args.text),
            "created_at": now(), "text": args.text, "status": args.status,
            "based_on_team_release": args.based_on_team_release or profile.get("team_release"),
            "evidence_refs": args.evidence_ref, "personal_only": True}
    append_jsonl(paths["candidates"], rule)
    print(json.dumps({"status": "recorded", "candidate_id": rule["candidate_id"], "rule_status": rule["status"]}, ensure_ascii=False))


def command_feedback(args):
    paths = file_paths(args.workspace_root)
    row = {"feedback_id": ident("personal-feedback", args.candidate_id), "created_at": now(),
           "candidate_id": args.candidate_id, "decision": args.decision, "note": args.note,
           "evidence_refs": args.evidence_ref, "personal_only": True}
    append_jsonl(paths["feedback"], row)
    print(json.dumps({"status": "recorded", "feedback_id": row["feedback_id"]}, ensure_ascii=False))


def command_rebase(args):
    paths = file_paths(args.workspace_root)
    profile = load_json(paths["profile"], {"team_release": "base_only", "personal_version": "Personal-v0.1"})
    old_release = profile.get("team_release", "base_only")
    new_release = args.team_release
    rows = load_jsonl(paths["candidates"])
    changed = 0
    for row in rows:
        based = row.get("based_on_team_release", old_release)
        if based != new_release and row.get("status") in ("active", "candidate"):
            row["status"] = "needs_revalidation"
            row["rebase_from"] = based
            row["rebase_to"] = new_release
            row["updated_at"] = now()
            changed += 1
    if rows:
        ensure_dir(paths["candidates"].parent)
        with io.open(str(paths["candidates"]), "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    major = int(str(profile.get("personal_version", "Personal-v0.1")).split(".")[-1])
    profile["team_release"] = new_release
    profile["personal_version"] = "Personal-v0.{0}".format(major)
    profile["updated_at"] = now()
    save_json(paths["profile"], profile)
    save_json(paths["runtime"], {"schema_version": "1.0", "status": "active", "team_release": new_release,
                                  "personal_version": profile["personal_version"], "updated_at": now(),
                                  "revalidated_candidate_count": changed})
    print(json.dumps({"status": "rebased", "from_team_release": old_release, "to_team_release": new_release,
                      "needs_revalidation": changed, "personal_version": profile["personal_version"]}, ensure_ascii=False))


def command_compose(args):
    paths = file_paths(args.workspace_root)
    profile = load_json(paths["profile"], {"team_release": "base_only", "personal_version": "Personal-v0.1"})
    candidates = load_jsonl(paths["candidates"])
    active = [row for row in candidates if row.get("status") == "active"]
    pending = [row for row in candidates if row.get("status") in ("candidate", "needs_revalidation")]
    payload = {"schema_version": "1.0", "status": "active", "team_release": profile.get("team_release", "base_only"),
               "personal_version": profile.get("personal_version", "Personal-v0.1"),
               "layers": ["base", "team_release", "personal_growth"],
               "precedence": ["safety", "institution_facts", "team_rules", "personal_verified", "personal_candidate", "base"],
               "active_personal_rules": active, "pending_personal_rules": pending,
               "personal_case_count": len(load_jsonl(paths["cases"])), "updated_at": now()}
    save_json(paths["runtime"], payload)
    print(json.dumps({"status": "composed", "runtime_manifest": str(paths["runtime"]),
                      "active_personal_rules": len(active), "pending_personal_rules": len(pending)}, ensure_ascii=False))


def command_status(args):
    paths = file_paths(args.workspace_root)
    profile = load_json(paths["profile"], {})
    candidates = load_jsonl(paths["candidates"])
    cases = load_jsonl(paths["cases"])
    counts = {}
    for row in candidates:
        counts[row.get("status", "unknown")] = counts.get(row.get("status", "unknown"), 0) + 1
    print(json.dumps({"status": "ok", "profile": profile, "case_count": len(cases),
                      "candidate_status": counts, "root": str(paths["root"])}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Maintain a private personal consultation growth overlay")
    sub = parser.add_subparsers(dest="command")
    init = sub.add_parser("init")
    init.add_argument("workspace_root")
    init.add_argument("--operator", default="")
    init.add_argument("--team-release", default="")
    init.set_defaults(handler=command_init)
    case = sub.add_parser("case")
    case.add_argument("workspace_root")
    case.add_argument("--source-id", default="")
    case.add_argument("--role", choices=("positive", "negative", "learning", "unknown"), default="learning")
    case.add_argument("--summary", required=True)
    case.add_argument("--outcome", default="待确认")
    case.add_argument("--evidence-ref", action="append", default=[])
    case.set_defaults(handler=command_case)
    rule = sub.add_parser("rule")
    rule.add_argument("workspace_root")
    rule.add_argument("--rule-id", default="")
    rule.add_argument("--text", required=True)
    rule.add_argument("--status", choices=("candidate", "active"), default="candidate")
    rule.add_argument("--based-on-team-release", default="")
    rule.add_argument("--evidence-ref", action="append", default=[])
    rule.set_defaults(handler=command_rule)
    feedback = sub.add_parser("feedback")
    feedback.add_argument("workspace_root")
    feedback.add_argument("--candidate-id", required=True)
    feedback.add_argument("--decision", choices=("keep", "modify", "retire"), required=True)
    feedback.add_argument("--note", required=True)
    feedback.add_argument("--evidence-ref", action="append", default=[])
    feedback.set_defaults(handler=command_feedback)
    rebase = sub.add_parser("rebase")
    rebase.add_argument("workspace_root")
    rebase.add_argument("--team-release", required=True)
    rebase.set_defaults(handler=command_rebase)
    compose = sub.add_parser("compose")
    compose.add_argument("workspace_root")
    compose.set_defaults(handler=command_compose)
    status = sub.add_parser("status")
    status.add_argument("workspace_root")
    status.set_defaults(handler=command_status)
    args = parser.parse_args()
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    return args.handler(args) or 0


if __name__ == "__main__":
    sys.exit(main())
