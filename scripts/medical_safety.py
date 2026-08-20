#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic boundary checks for consultation drafts; not a diagnosis engine."""

import json
import re


PROMISE = re.compile(r"(?:保证|包治|一定|肯定|百分之百|100%)\s*(?:有效|治好|治愈|不复发|没问题)")
DIAGNOSIS = re.compile(r"(?:你|患者|这)\s*(?:就是|肯定是|一定是|已经是)\s*[^，。；\n]{1,30}(?:病|癌|感染|综合征)")
PRESCRIPTION = re.compile(r"(?:停药|换药|加量|减量|每天\s*\d+\s*(?:片|粒|mg|毫克)|每次\s*\d+\s*(?:片|粒|mg|毫克))", re.I)
URGENT = ("胸痛", "呼吸困难", "昏迷", "大出血", "意识不清", "自杀", "轻生", "抽搐")
ESCALATION = ("急诊", "120", "立即就医", "尽快就医", "转医生", "执业医生", "医疗人员")


def validate_patient_facing_text(text):
    errors = []
    value = str(text or "")
    if PROMISE.search(value):
        errors.append("text contains a treatment guarantee")
    if DIAGNOSIS.search(value):
        errors.append("text contains a diagnosis assertion")
    if PRESCRIPTION.search(value):
        errors.append("text contains medication instructions")
    if any(term in value for term in URGENT) and not any(term in value for term in ESCALATION):
        errors.append("urgent signal requires immediate medical escalation")
    return errors


def validate_analysis_safety(analysis):
    draft = str(analysis.get("safe_response_draft") or "")
    errors = validate_patient_facing_text(draft)
    serialized = json.dumps(analysis, ensure_ascii=False)
    if any(term in serialized for term in URGENT):
        action_text = " ".join((draft, str(analysis.get("next_service_action") or "")))
        if analysis.get("risk_level") != "P0" or not any(term in action_text for term in ESCALATION):
            errors.append("urgent signal requires P0 and immediate medical escalation")
    return errors
