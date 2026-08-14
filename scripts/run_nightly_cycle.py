#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scan one manager team and create a deduplicated nightly Agent task queue."""

import argparse
import datetime
import hashlib
import io
import json
import os
import sys
from pathlib import Path

from compat import ensure_dir, expand_path
from archive_team_inbox import archive_workspace
from generate_management_dashboard import build_dashboard


AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".amr"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
CHAT_EXTS = {".html", ".htm", ".txt", ".md", ".json"}
DATA_EXTS = {".csv", ".xlsx", ".xls", ".jsonl"}
IGNORED_PARTS = {
    "03_团队报表", "04_团队报告", "04_团队自动化", "03_个人报告",
    "02_会议报告", "_系统", "__pycache__",
}
IGNORED_NAMES = {"过程量数据模板.csv", "结果数据模板.csv"}


def sha256(path):
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path, default):
    if not path.is_file():
        return default
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def save_json(path, value):
    ensure_dir(path.parent)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_jsonl(path):
    rows = []
    if not path.is_file():
        return rows
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict) and value.get("task_id"):
                    rows.append(value)
    except IOError:
        return []
    return rows


def classify(path, relative):
    suffix = path.suffix.lower()
    if "02_团队会议" in relative:
        return "meeting_analysis"
    if suffix in AUDIO_EXTS:
        return "audio_transcription_and_consult_analysis"
    if suffix in IMAGE_EXTS:
        return "image_slice_ocr_and_chat_analysis"
    if suffix in DATA_EXTS:
        return "data_validation_and_dashboard"
    if suffix in CHAT_EXTS:
        return "chat_or_text_analysis"
    return "manual_review"


def classify_archived(row, archive_path):
    """Use the archive metadata because archived meeting paths hide the front label."""
    category = row.get("category")
    kind = row.get("kind", "")
    suffix = archive_path.suffix.lower()
    if category == "meeting":
        return "meeting_analysis"
    if category == "data":
        return "data_validation_and_dashboard"
    if kind == "01_咨询录音" or kind == "03_一对一沟通":
        if suffix in AUDIO_EXTS:
            return "audio_transcription_and_consult_analysis"
        return "chat_or_text_analysis"
    if suffix in IMAGE_EXTS:
        return "image_slice_ocr_and_chat_analysis"
    if suffix in AUDIO_EXTS:
        return "audio_transcription_and_consult_analysis"
    if suffix in DATA_EXTS:
        return "data_validation_and_dashboard"
    if suffix in CHAT_EXTS:
        return "chat_or_text_analysis"
    return "manual_review"


def should_scan(path, team_root, now, stability_minutes):
    if not path.is_file() or path.name.startswith("~") or path.name in IGNORED_NAMES:
        return False, "ignored"
    try:
        relative_parts = path.relative_to(team_root).parts
    except ValueError:
        return False, "outside_team"
    if any(part in IGNORED_PARTS for part in relative_parts):
        return False, "system_or_output"
    if path.suffix.lower() in (".part", ".tmp", ".crdownload"):
        return False, "incomplete_upload"
    age = now - os.path.getmtime(str(path))
    if age < stability_minutes * 60:
        return False, "still_changing"
    return True, "ready"


def main():
    parser = argparse.ArgumentParser(description="Scan a manager team for nightly consultation tasks.")
    parser.add_argument("workspace_root", help="咨询转化工作区路径")
    parser.add_argument("--date", help="报告日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--stability-minutes", type=int, default=30)
    parser.add_argument("--force", action="store_true", help="忽略文件稳定等待时间，仅用于补跑")
    args = parser.parse_args()
    root = expand_path(args.workspace_root)
    team_root = root / "08_团队管理"
    if not team_root.is_dir():
        print("ERROR: 08_团队管理 not found", file=sys.stderr)
        return 2
    report_date = args.date or datetime.date.today().isoformat()
    automation_root = root / "_系统" / "团队自动化"
    queue_dir = automation_root / "01_待处理队列"
    log_dir = automation_root / "02_运行日志"
    ensure_dir(queue_dir)
    ensure_dir(log_dir)
    state_path = log_dir / "已处理文件.json"
    state = load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    now = time_now = __import__("time").time()
    tasks = []
    skipped = {}
    archive_rows = archive_workspace(
        root, report_date, args.force, 0 if args.force else args.stability_minutes
    )
    archived_front_sources = set()
    for row in archive_rows:
        if row.get("source"):
            archived_front_sources.add(row["source"])
        if row.get("status") not in ("archived", "already_exists", "already_archived"):
            skipped[row.get("status", "archive_skipped")] = skipped.get(
                row.get("status", "archive_skipped"), 0
            ) + 1
            continue
        archive_value = row.get("archive_path", "")
        archive_path = Path(archive_value)
        if not archive_path.is_absolute():
            archive_path = root / archive_path
        if not archive_path.is_file():
            skipped["archive_missing"] = skipped.get("archive_missing", 0) + 1
            continue
        relative = str(archive_path.relative_to(root))
        fingerprint = row.get("source_hash") or sha256(archive_path)
        key = "archive:" + relative
        if state.get(key) == fingerprint:
            skipped["already_processed"] = skipped.get("already_processed", 0) + 1
            continue
        task_id = hashlib.sha1((relative + "\n" + fingerprint).encode("utf-8")).hexdigest()[:16]
        tasks.append({
            "task_id": task_id,
            "source": relative,
            "original_source": row.get("source"),
            "source_hash": fingerprint,
            "task_type": classify_archived(row, archive_path),
            "status": "queued",
            "next_action": "由定时 Agent 执行并将报告写入对应成员/团队报告目录",
            "created_at": datetime.datetime.now().isoformat(),
        })
        state[key] = fingerprint

    for path in sorted(team_root.rglob("*")):
        ok, reason = should_scan(path, team_root, now, 0 if args.force else args.stability_minutes)
        if not ok:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        relative = str(path.relative_to(root))
        if relative in archived_front_sources:
            skipped["archived_to_hidden_history"] = skipped.get("archived_to_hidden_history", 0) + 1
            continue
        fingerprint = sha256(path)
        key = relative
        if state.get(key) == fingerprint:
            skipped["already_processed"] = skipped.get("already_processed", 0) + 1
            continue
        task_id = hashlib.sha1((relative + "\n" + fingerprint).encode("utf-8")).hexdigest()[:16]
        tasks.append({
            "task_id": task_id,
            "source": relative,
            "source_hash": fingerprint,
            "task_type": classify(path, relative),
            "status": "queued",
            "next_action": "由定时 Agent 执行并将报告写入对应成员/团队报告目录",
            "created_at": datetime.datetime.now().isoformat(),
        })
        state[key] = fingerprint

    queue_path = queue_dir / "夜间任务-{}.jsonl".format(report_date)
    existing_tasks = load_jsonl(queue_path)
    merged_tasks = []
    task_ids = set()
    for task in existing_tasks + tasks:
        if task.get("task_id") in task_ids:
            continue
        task_ids.add(task.get("task_id"))
        merged_tasks.append(task)
    with io.open(str(queue_path), "w", encoding="utf-8") as handle:
        for task in merged_tasks:
            handle.write(json.dumps(task, ensure_ascii=False) + "\n")
    save_json(state_path, state)
    dashboard_path = ""
    dashboard_generated_at = ""
    data_completeness = "missing"
    pending_analysis_count = len([item for item in merged_tasks if item.get("status", "queued") not in ("completed", "skipped")])
    failed_root = root / "_系统" / "失败记录"
    failed_item_count = sum(1 for item in failed_root.rglob("*") if item.is_file()) if failed_root.is_dir() else 0
    try:
        dashboard_data, dashboard_output = build_dashboard(root, "today", report_date)
        dashboard_path = str(dashboard_output)
        dashboard_generated_at = dashboard_data.get("generated_at", "")
        data_completeness = dashboard_data.get("periods", {}).get("today", {}).get("data_status", {}).get("data_completeness", "missing")
    except (ValueError, IOError) as exc:
        skipped["dashboard_generation_failed"] = str(exc)
    log_path = log_dir / "夜间运行-{}.json".format(report_date)
    save_json(log_path, {
        "date": report_date,
        "workspace": str(root),
        "schedule": "22:00",
        "tasks_created": len(tasks),
        "queue_items": len(merged_tasks),
        "archive_items": len(archive_rows),
        "skipped": skipped,
        "queue": str(queue_path),
        "dashboard_path": dashboard_path,
        "dashboard_generated_at": dashboard_generated_at,
        "data_completeness": data_completeness,
        "pending_analysis_count": pending_analysis_count,
        "failed_item_count": failed_item_count,
        "note": "本脚本负责扫描、去重和排队；转写、OCR、咨询分析和看板生成由定时 Agent 继续执行。",
    })
    print(json.dumps({"date": report_date, "tasks_created": len(tasks),
                      "queue": str(queue_path), "log": str(log_path), "dashboard_path": dashboard_path,
                      "dashboard_generated_at": dashboard_generated_at, "data_completeness": data_completeness,
                      "pending_analysis_count": pending_analysis_count, "failed_item_count": failed_item_count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
