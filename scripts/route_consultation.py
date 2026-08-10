#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic front-door router for AI咨询转化飞轮 v1.9.

The router does not generate a consultation answer. It identifies the primary
specialist capability, reports the runtime layers available to the caller, and
enforces the manager/frontline boundary for manager-only workflows.
"""

import argparse
import io
import json
import re
import sys
from pathlib import Path

from compat import expand_path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "references" / "specialist-routing.json"
BASE_RUNTIME = ROOT / "runtime" / "base-runtime.json"


def load_json(path, default=None):
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def contains(text, term):
    return term in text


def load_registry():
    return load_json(REGISTRY, {"routes": [], "default_route": "conversation_diagnosis"})


def path_from_workspace(workspace, *parts):
    return expand_path(workspace) / "咨询转化工作区" / "_系统" / Path(*parts)


def read_runtime_state(workspace):
    state = {
        "mode": "base_only",
        "base_runtime": "active",
        "team_runtime": "inactive",
        "personal_runtime": "inactive",
        "institution_facts": "unavailable",
        "team_rules": "unavailable",
    }
    if not workspace:
        return state
    workspace = expand_path(workspace)
    release = load_json(path_from_workspace(workspace, "发布", "active.json"), {}) or {}
    capability = load_json(path_from_workspace(workspace, "当前能力包", "active.json"), {}) or {}
    knowledge = load_json(path_from_workspace(workspace, "当前机构知识", "active.json"), {}) or {}
    personal = load_json(path_from_workspace(workspace, "个人成长", "runtime-manifest.json"), {}) or {}
    if release.get("status") == "active" or capability.get("status") == "active":
        state["team_runtime"] = "active"
        state["team_rules"] = "available"
        state["institution_facts"] = "available" if knowledge.get("status") == "active" else "unavailable"
    elif (workspace / "咨询转化工作区" / "_系统" / "当前能力包" / "active.json").is_file():
        state["institution_facts"] = "available" if knowledge.get("status") == "active" else "unavailable"
    if personal.get("status") == "active":
        state["personal_runtime"] = "active"
    if state["team_runtime"] == "active" and state["personal_runtime"] == "active":
        state["mode"] = "team_plus_personal"
    elif state["team_runtime"] == "active":
        state["mode"] = "team_active"
    elif state["personal_runtime"] == "active":
        state["mode"] = "base_plus_personal"
    return state


def infer_role(workspace, requested):
    if requested and requested != "auto":
        return requested
    if workspace:
        workspace = expand_path(workspace)
        role_file = path_from_workspace(workspace, "运行时角色.json")
        role = load_json(role_file, {}) or {}
        if role.get("role") in ("manager", "frontline", "consultant"):
            return role["role"]
        for manifest_path in (
            workspace / "institution-pack" / "manifest.json",
            ROOT / "institution-pack" / "manifest.json",
        ):
            manifest = load_json(manifest_path, {}) or {}
            if manifest.get("runtime_role") == "frontline":
                return "frontline"
    return "consultant"


def score_route(text, route):
    terms = list(route.get("aliases", [])) + list(route.get("keywords", []))
    matched = [term for term in terms if contains(text, term)]
    score = sum(4 if term in route.get("aliases", []) else 1 for term in matched)
    # Exact requests such as “费用异议模式” should beat a generic “分析” match.
    exact_alias = any(alias in text for alias in route.get("aliases", []))
    if exact_alias:
        score += 5
    return score, matched


def select_route(text, registry, route_id=None):
    routes = registry.get("routes", [])
    if route_id:
        for route in routes:
            if route.get("id") == route_id:
                return route, ["--route:{0}".format(route_id)], 100
        return None, [], 0
    ranked = []
    for route in routes:
        score, matched = score_route(text, route)
        if score:
            ranked.append((score, route, matched))
    if not ranked:
        fallback_id = registry.get("default_route")
        route = next((item for item in routes if item.get("id") == fallback_id), None)
        return route, [], 0
    ranked.sort(key=lambda item: (-item[0], routes.index(item[1])))
    score, route, matched = ranked[0]
    return route, matched, score


def confidence(score, explicit=False):
    if explicit or score >= 9:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def render_route(route, matched, score, runtime, role, explicit=False):
    if not route:
        return {
            "schema_version": "1.0-route-result",
            "status": "unmatched",
            "message": "没有识别到专项任务，请直接说明你要分析、回复、回访、陪练还是蒸馏。",
            "runtime": runtime,
            "role": role,
        }
    manager_only = route.get("type") == "manager"
    allowed = not manager_only or role == "manager"
    status = "routed" if allowed else "manager_confirmation_required"
    message = ""
    if not allowed:
        message = "该专项能力只允许主管端执行；当前先保留为管理者任务，不执行团队蒸馏、发布或回滚。"
    elif route.get("base_available"):
        message = "当前专项能力可在无蒸馏的基础运行时中直接使用；机构专属事实仍需初始化或团队发布包提供。"
    else:
        message = "该专项能力需要主管端的机构工作区、候选资料或已审核运行时。"
    return {
        "schema_version": "1.0-route-result",
        "status": status,
        "route_id": route.get("id"),
        "label": route.get("label"),
        "route_type": route.get("type"),
        "permission": "allowed" if allowed else "manager_only",
        "confidence": confidence(score, explicit),
        "matched_terms": matched,
        "runtime_mode": runtime.get("mode"),
        "runtime": runtime,
        "references": route.get("references", []),
        "required_inputs": route.get("required_inputs", []),
        "output_contract": route.get("output_contract", []),
        "message": message,
        "fallback": "conversation_diagnosis" if route.get("id") != "conversation_diagnosis" else None,
        "role": role,
    }


def main():
    parser = argparse.ArgumentParser(description="Route an AI咨询转化飞轮 task to one specialist capability.")
    parser.add_argument("text", nargs="?", default="", help="the user's natural-language task")
    parser.add_argument("--workspace-root", default="", help="selected workspace root")
    parser.add_argument("--route", default="", help="explicit route id, for direct specialist invocation")
    parser.add_argument("--role", choices=("auto", "manager", "frontline", "consultant"), default="auto")
    parser.add_argument("--list", action="store_true", help="list available specialist routes")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    registry = load_registry()
    if args.list:
        result = {
            "schema_version": "1.0-route-registry",
            "default_route": registry.get("default_route"),
            "routes": [
                {"id": item.get("id"), "label": item.get("label"), "type": item.get("type"),
                 "base_available": item.get("base_available", False), "aliases": item.get("aliases", [])}
                for item in registry.get("routes", [])
            ],
        }
    else:
        workspace = args.workspace_root or None
        role = infer_role(workspace, args.role)
        runtime = read_runtime_state(workspace)
        route, matched, score = select_route(args.text, registry, args.route or None)
        result = render_route(route, matched, score, runtime, role, bool(args.route))
        result["base_runtime_manifest"] = str(BASE_RUNTIME)
    if args.format == "markdown":
        if result.get("routes") is not None:
            lines = ["# 专项能力路由", ""]
            for item in result["routes"]:
                lines.append("- `{0}`：{1}（{2}）".format(item["id"], item["label"], item["type"]))
            sys.stdout.write("\n".join(lines) + "\n")
        else:
            sys.stdout.write("# {0}\n\n状态：{1}\n\n运行模式：{2}\n\n".format(
                result.get("label", "未匹配"), result.get("status"), result.get("runtime_mode", "unknown")))
            sys.stdout.write("下一步：{0}\n".format(result.get("message", "")))
    else:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
