#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create local, privacy-screened product feedback cards without uploading data."""

import argparse
import datetime
import hashlib
import io
import json
import os
import sys
from pathlib import Path

from compat import ensure_dir
from privacy_guard import scan_value
from project_version import core_version, WORKSPACE_SCHEMA_VERSION
from workspace_paths import locate_workspace


FEEDBACK_FOLDER = Path("07_我的产出") / "08_产品反馈"
CATEGORIES = ("bug", "suggestion", "analysis_quality", "compatibility", "privacy_security")


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def safe_text(value):
    return str(value or "").strip()


def atomic_write(path, content):
    ensure_dir(path.parent)
    temporary = path.with_name(path.name + ".tmp")
    with io.open(str(temporary), "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    if hasattr(os, "replace"):
        os.replace(str(temporary), str(path))
    else:
        if path.exists():
            os.remove(str(path))
        os.rename(str(temporary), str(path))


def feedback_id(payload):
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    return "FB-{0}-{1}".format(datetime.datetime.now().strftime("%Y%m%d%H%M%S"), digest)


def safe_diagnostics(args):
    if not args.include_diagnostics:
        return {"included": False}
    return {
        "included": True,
        "core_version": core_version(),
        "workspace_schema_version": WORKSPACE_SCHEMA_VERSION,
        "python_version": "{0}.{1}.{2}".format(sys.version_info[0], sys.version_info[1], sys.version_info[2]),
        "platform": sys.platform,
    }


def render_card(payload, contact):
    labels = {
        "bug": "功能故障",
        "suggestion": "功能建议",
        "analysis_quality": "分析质量",
        "compatibility": "安装或兼容性",
        "privacy_security": "隐私或安全",
    }
    return """# AI咨询转化飞轮反馈卡

- 反馈编号：{feedback_id}
- Core 版本：{core_version}
- 分类：{category}
- 使用环境：{host}
- 创建时间：{created_at}

## 我遇到的问题

{summary}

## 我原本希望

{expected}

## 实际发生

{actual}

## 如何复现

{steps}

## 分享边界

- 本卡不包含患者原始材料、机构名称、成员名单、工作区路径或凭证。
- 如需案例，请重新编写完全合成的示例，不要附真实截图、录音或聊天记录。
- 处理方式：{contact}
""".format(
        feedback_id=payload["feedback_id"], core_version=payload["core_version"],
        category=labels.get(payload["category"], payload["category"]), host=payload["host"] or "未填写",
        created_at=payload["created_at"], summary=payload["summary"],
        expected=payload["expected"] or "未填写", actual=payload["actual"] or "未填写",
        steps=payload["steps"] or "未填写", contact=contact,
    )


def create(args):
    workspace = locate_workspace(args.workspace_root)
    base = {
        "schema_version": "2.2-product-feedback",
        "core_version": core_version(),
        "workspace_schema_version": WORKSPACE_SCHEMA_VERSION,
        "category": args.category,
        "summary": safe_text(args.summary),
        "expected": safe_text(args.expected),
        "actual": safe_text(args.actual),
        "steps": safe_text(args.steps),
        "host": safe_text(args.host),
        "diagnostics": safe_diagnostics(args),
        "contains_raw_patient_material": False,
        "contains_institution_identity": False,
        "auto_uploaded": False,
        "status": "new",
        "created_at": now_iso(),
    }
    findings = scan_value({key: base[key] for key in ("summary", "expected", "actual", "steps", "host")},
                          include_secrets=True)
    if findings:
        print(json.dumps({
            "status": "rejected",
            "reason": "feedback_contains_possible_sensitive_data",
            "findings": findings,
            "message": "请删除手机号、微信、身份证、邮箱、地址、病历号或凭证，并改用完全合成示例。",
        }, ensure_ascii=False))
        return 2
    base["feedback_id"] = feedback_id(base)
    output = workspace / FEEDBACK_FOLDER / base["feedback_id"]
    ensure_dir(output)
    json_path = output / "feedback.json"
    card_path = output / "反馈卡.md"
    atomic_write(json_path, json.dumps(base, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    atomic_write(card_path, render_card(base, args.contact))
    print(json.dumps({
        "status": "created",
        "feedback_id": base["feedback_id"],
        "card": str(card_path),
        "record": str(json_path),
        "next_step": args.contact,
        "auto_uploaded": False,
    }, ensure_ascii=False, indent=2))
    return 0


def list_feedback(args):
    workspace = locate_workspace(args.workspace_root)
    root = workspace / FEEDBACK_FOLDER
    rows = []
    if root.is_dir():
        for path in sorted(root.glob("FB-*/feedback.json")):
            try:
                with io.open(str(path), "r", encoding="utf-8") as handle:
                    value = json.load(handle)
                rows.append({key: value.get(key) for key in ("feedback_id", "category", "summary", "status", "created_at")})
            except (IOError, ValueError):
                continue
    print(json.dumps({"status": "ok", "count": len(rows), "feedback": rows}, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Create a local privacy-screened product feedback card.")
    subparsers = parser.add_subparsers(dest="command")
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("workspace_root")
    create_parser.add_argument("--category", choices=CATEGORIES, required=True)
    create_parser.add_argument("--summary", required=True)
    create_parser.add_argument("--expected", default="")
    create_parser.add_argument("--actual", default="")
    create_parser.add_argument("--steps", default="")
    create_parser.add_argument("--host", default="WorkBuddy")
    create_parser.add_argument("--include-diagnostics", action="store_true")
    create_parser.add_argument("--contact", default="请把这张反馈卡发给向你提供安装包的服务人员")
    create_parser.set_defaults(handler=create)
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("workspace_root")
    list_parser.set_defaults(handler=list_feedback)
    args = parser.parse_args()
    if not getattr(args, "handler", None):
        parser.print_help()
        return 2
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
