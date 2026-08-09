#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Archive simple team inboxes into a traceable hidden personal archive."""

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import shutil
from pathlib import Path

from compat import ensure_dir, expand_path


DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-_年](\d{1,2})[-_月](\d{1,2})[日]?(?!\d)")
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".amr"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
CHAT_EXTS = {".html", ".htm", ".txt", ".md", ".json"}
DATA_EXTS = {".csv", ".xlsx", ".xls", ".jsonl"}
IGNORED_NAMES = {"过程量数据模板.csv", "结果数据模板.csv"}


def sha256(path):
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_name(value):
    value = str(value or "待确认").strip()
    for char in "/\\:*?\"<>|":
        value = value.replace(char, "_")
    return value or "待确认"


def date_for(path):
    match = DATE_RE.search(path.name)
    if match:
        value = "{}-{:02d}-{:02d}".format(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return value, "filename"
    value = datetime.date.fromtimestamp(os.path.getmtime(str(path))).isoformat()
    return value, "filesystem_mtime"


def relative_parts(path, team_root):
    return path.relative_to(team_root).parts


def meeting_kind(path):
    value = DATE_RE.sub("", path.stem).strip(" _-__")
    pieces = [piece.strip() for piece in value.split("__") if piece.strip()]
    if pieces:
        return safe_name(pieces[0])
    return "待确认"


def category_for(path, team_root):
    parts = relative_parts(path, team_root)
    if len(parts) >= 3 and parts[0] == "01_成员":
        member = parts[1]
        if "03_个人报告" in parts:
            return None
        if "一对一" in path.name or "1对1" in path.name:
            kind = "03_一对一沟通"
        elif path.suffix.lower() in AUDIO_EXTS:
            kind = "01_咨询录音"
        elif path.suffix.lower() in IMAGE_EXTS or path.suffix.lower() in CHAT_EXTS:
            kind = "02_微信聊天"
        else:
            kind = "02_微信聊天"
        return {"category": "member", "owner": member, "kind": kind}
    if len(parts) >= 3 and parts[0] == "02_团队会议":
        if "02_会议报告" in parts:
            return None
        return {"category": "meeting", "owner": "团队会议", "kind": meeting_kind(path)}
    if len(parts) >= 3 and parts[0] == "03_团队数据":
        return {"category": "data", "owner": "团队数据", "kind": "数据表"}
    return None


def source_files(team_root):
    for path in sorted(team_root.rglob("*")):
        if not path.is_file() or path.name.startswith("~") or path.name in IGNORED_NAMES:
            continue
        if path.suffix.lower() in (".part", ".tmp", ".crdownload"):
            continue
        info = category_for(path, team_root)
        if info:
            yield path, info


def load_index(path):
    if not path.is_file():
        return {}
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (IOError, ValueError):
        return {}


def save_index(path, value):
    ensure_dir(path.parent)
    with io.open(str(path), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def archive_path(root, info, date_value, source, digest):
    year, month, _ = date_value.split("-")
    if info["category"] == "member":
        owner = safe_name(info["owner"])
        folder = root / "_系统" / "团队档案" / owner / year / month / date_value / info["kind"]
        employee_id = owner.split("_", 1)[0]
        name = "{}__{}__{}__待确认__待确认__待确认__{}{}".format(
            date_value, employee_id, info["kind"], digest[:8], source.suffix.lower()
        )
    elif info["category"] == "meeting":
        folder = root / "_系统" / "团队会议" / year / month / date_value / safe_name(info["kind"])
        name = "{}__团队会议__{}__{}{}".format(date_value, safe_name(info["kind"]), digest[:8], source.suffix.lower())
    else:
        folder = root / "_系统" / "团队数据" / year / month / date_value
        name = "{}__团队数据__{}{}".format(date_value, digest[:8], source.suffix.lower())
    return folder / name


def archive_workspace(root, run_date=None, force=False, stability_minutes=30):
    team_root = root / "08_团队管理"
    if not team_root.is_dir():
        return []
    run_date = run_date or datetime.date.today().isoformat()
    index_path = root / "_系统" / "团队档案" / "归档索引.json"
    index = load_index(index_path)
    results = []
    for source, info in source_files(team_root):
        age = __import__("time").time() - os.path.getmtime(str(source))
        if not force and age < stability_minutes * 60:
            results.append({"source": str(source.relative_to(root)),
                            "status": "still_changing"})
            continue
        digest = sha256(source)
        relative = str(source.relative_to(root))
        key = "{}|{}|{}|{}".format(info["category"], info["owner"], info["kind"], digest)
        date_value, date_source = date_for(source)
        target = archive_path(root, info, date_value, source, digest)
        if key in index and Path(index[key].get("archive_path", "")).is_file():
            results.append({"source": relative, "archive_path": index[key]["archive_path"],
                            "source_hash": digest, "category": info["category"],
                            "owner": info["owner"], "kind": info["kind"],
                            "status": "already_archived"})
            continue
        if target.exists() and not force:
            index[key] = {"archive_path": str(target), "source": relative, "source_hash": digest,
                          "category": info["category"], "owner": info["owner"],
                          "kind": info["kind"]}
            results.append({"source": relative, "archive_path": str(target),
                            "source_hash": digest, "category": info["category"],
                            "owner": info["owner"], "kind": info["kind"],
                            "status": "already_exists"})
            continue
        ensure_dir(target.parent)
        if not target.exists() or force:
            shutil.copy2(str(source), str(target))
        index[key] = {
            "archive_path": str(target),
            "source": relative,
            "source_hash": digest,
            "category": info["category"],
            "owner": info["owner"],
            "date": date_value,
            "date_provenance": date_source,
            "kind": info["kind"],
        }
        results.append({"source": relative, "archive_path": str(target),
                        "source_hash": digest, "date": date_value,
                        "category": info["category"], "owner": info["owner"],
                        "date_provenance": date_source, "kind": info["kind"],
                        "status": "archived"})
    save_index(index_path, index)
    log_dir = root / "_系统" / "团队自动化" / "02_运行日志"
    ensure_dir(log_dir)
    log_path = log_dir / "归档-{}.jsonl".format(run_date)
    with io.open(str(log_path), "w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return results


def main():
    parser = argparse.ArgumentParser(description="Archive team inbox files into the hidden personal archive.")
    parser.add_argument("workspace_root", help="咨询转化工作区路径")
    parser.add_argument("--date", help="本次运行日期 YYYY-MM-DD")
    parser.add_argument("--stability-minutes", type=int, default=30)
    parser.add_argument("--force", action="store_true", help="仅在确认需要重新归档时使用")
    args = parser.parse_args()
    root = expand_path(args.workspace_root)
    results = archive_workspace(root, args.date, args.force, args.stability_minutes)
    counts = {}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(json.dumps({"total": len(results), "counts": counts,
                      "archive_root": str(root / "_系统" / "团队档案")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
