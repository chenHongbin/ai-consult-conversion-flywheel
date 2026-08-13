#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Select visual assets and bundles for a consultation decision point.

This is a production router, not an image generator. It turns a natural-language
request plus optional stage/barrier/channel fields into an auditable asset brief.
"""

import argparse
import io
import json
import re
import sys
from pathlib import Path

from compat import expand_path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "references" / "visual-asset-catalog.json"
MATRIX = ROOT / "references" / "visual-decision-matrix.json"


def load_json(path, default=None):
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return default


def unique(items):
    output = []
    for item in items:
        if item and item not in output:
            output.append(item)
    return output


def infer_values(text, aliases):
    found = []
    for label, value in aliases.items():
        if label in text:
            found.append(value)
    return unique(found)


def explicit_asset(text, assets, requested):
    if requested:
        return next((item for item in assets if item.get("id") == requested), None)
    for item in assets:
        if item.get("label") in text or item.get("id") in text:
            return item
        if any(keyword in text for keyword in item.get("keywords", [])):
            return item
    return None


def score_asset(asset, text, stages, barriers, channels, goal):
    score = 0
    matched = []
    asset_stages = asset.get("stages", [])
    asset_barriers = asset.get("barriers", [])
    asset_channels = asset.get("channels", [])
    if stages:
        overlap = [value for value in stages if value in asset_stages or "any" in asset_stages]
        score += len(overlap) * 6
        matched.extend(overlap)
    if barriers:
        overlap = [value for value in barriers if value in asset_barriers]
        score += len(overlap) * 5
        matched.extend(overlap)
    if channels:
        overlap = [value for value in channels if value in asset_channels]
        score += len(overlap) * 3
        matched.extend(overlap)
    if goal and goal in asset_stages:
        score += 4
        matched.append(goal)
    keyword_hits = [keyword for keyword in asset.get("keywords", []) if keyword in text]
    score += len(keyword_hits) * 2
    matched.extend(keyword_hits)
    if asset.get("id") == "asset_bundle" and (len(stages) > 1 or len(barriers) > 1):
        score += 2
        matched.append("组合需求")
    return score, unique(matched)


def output_brief(asset, stages, barriers, channels, goal, matched):
    purpose = asset.get("purpose", "")
    cta = asset.get("cta", "")
    stage_text = "、".join(stages) if stages else "待识别阶段"
    barrier_text = "、".join(barriers) if barriers else "待识别顾虑"
    channel_text = "、".join(channels) if channels else "chat"
    return {
        "asset_id": asset.get("id"),
        "asset_type": asset.get("label"),
        "decision_job": purpose,
        "stage": stage_text,
        "barrier": barrier_text,
        "channels": channels or ["chat"],
        "goal": goal or "推动下一步沟通",
        "matched_signals": matched,
        "production_modes": asset.get("production_modes", []),
        "recommended_cta": cta,
        "copy_brief": "围绕{0}，用{1}解释当前问题；结尾只提出一个可选择的下一步：{2}。".format(
            barrier_text, purpose, cta),
        "visual_brief": "制作{0}，用于{1}，表达{2}；画面信息聚焦一个结论，适配{3}。".format(
            asset.get("label"), stage_text, purpose, channel_text),
    }


def main():
    parser = argparse.ArgumentParser(description="Select an AI consultation visual asset.")
    parser.add_argument("text", nargs="?", default="", help="natural-language consultation or content request")
    parser.add_argument("--asset-type", default="", help="asset id, e.g. objection_qa")
    parser.add_argument("--stage", action="append", default=[], help="stage id or Chinese stage label")
    parser.add_argument("--barrier", action="append", default=[], help="barrier id or Chinese barrier label")
    parser.add_argument("--channel", action="append", default=[], help="chat, moments or group")
    parser.add_argument("--goal", default="", help="goal id or Chinese goal label")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    catalog = load_json(CATALOG, {"assets": []}) or {"assets": []}
    matrix = load_json(MATRIX, {}) or {}
    assets = catalog.get("assets", [])
    text = args.text or ""
    stages = unique(args.stage + infer_values(text, matrix.get("stage_aliases", {})))
    barriers = unique(args.barrier + infer_values(text, matrix.get("barrier_aliases", {})))
    goals = infer_values(args.goal, matrix.get("goal_aliases", {})) if args.goal else []
    goal = goals[0] if goals else (args.goal or "")
    channels = unique(args.channel)
    direct = explicit_asset(text, assets, args.asset_type)
    ranked = []
    for asset in assets:
        score, matched = score_asset(asset, text, stages, barriers, channels, goal)
        if direct and asset.get("id") == direct.get("id"):
            score += 100
            matched.insert(0, "explicit_asset")
        ranked.append((score, asset, matched))
    ranked.sort(key=lambda item: (-item[0], assets.index(item[1])))
    selected = ranked[:max(1, args.limit)]
    selected_briefs = []
    for score, asset, matched in selected:
        brief = output_brief(asset, stages, barriers, channels, goal, matched)
        brief["score"] = score
        selected_briefs.append(brief)
    bundle_stage = stages[0] if stages else "trust_building"
    if direct:
        direct_stages = [value for value in direct.get("stages", []) if value != "any"]
        if direct_stages:
            bundle_stage = direct_stages[0]
    bundle_ids = matrix.get("default_bundles", {}).get(bundle_stage, [])
    if direct and direct.get("id") not in bundle_ids:
        bundle_ids = [direct.get("id")] + bundle_ids
    bundle = []
    for asset_id in bundle_ids:
        asset = next((item for item in assets if item.get("id") == asset_id), None)
        if asset:
            bundle.append(output_brief(asset, stages, barriers, channels, goal, ["default_bundle"]))
    result = {
        "schema_version": "1.0-visual-selection",
        "catalog_version": catalog.get("catalog_version", "unknown"),
        "query": text,
        "inferred": {"stages": stages, "barriers": barriers, "channels": channels, "goal": goal},
        "selected": selected_briefs[0] if selected_briefs else None,
        "alternatives": selected_briefs[1:],
        "recommended_bundle": bundle,
        "next_action": "把 selected 作为主素材；如需连续触达，再按 recommended_bundle 依次编排。",
        "feedback_command": "scripts/record_visual_feedback.py <workspace-root> --asset-id <asset_id> --channel <channel> --status sent",
    }
    if args.format == "markdown":
        selected_item = result.get("selected") or {}
        sys.stdout.write("# 视觉素材选择\n\n")
        sys.stdout.write("主素材：{0}\n\n".format(selected_item.get("asset_type", "未识别")))
        sys.stdout.write("决策任务：{0}\n\n".format(selected_item.get("decision_job", "")))
        sys.stdout.write("配文方向：{0}\n\n".format(selected_item.get("copy_brief", "")))
        sys.stdout.write("视觉方向：{0}\n".format(selected_item.get("visual_brief", "")))
    else:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
