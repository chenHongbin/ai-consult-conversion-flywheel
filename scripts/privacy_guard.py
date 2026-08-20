#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared privacy checks for patient-facing and distributable artifacts."""

import io
import json
import re
import unicodedata
from pathlib import Path


PHONE = re.compile(r"(?<!\d)1[3-9](?:[\s\-_.·（）()]*\d){9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)(?:\d[\s\-_.·]*?){17}[\dXx](?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WECHAT = re.compile(
    r"(?:微信号|微信|wechat|wxid)\s*[:：_-]?\s*(?:wxid[_-])?[A-Za-z][A-Za-z0-9_-]{4,}",
    re.I,
)
MEDICAL_ID = re.compile(r"(?:病历号|住院号|门诊号|就诊号|患者编号)\s*[:：#]?\s*[A-Za-z0-9_-]{4,}", re.I)
ADDRESS = re.compile(r"(?:家庭住址|现住址|联系地址)\s*[:：]?\s*[^\n,，]{6,80}")
SECRET = re.compile(r"(?:api[_-]?key|client[_-]?secret|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}", re.I)


def normalize_text(value):
    return unicodedata.normalize("NFKC", str(value or ""))


def scan_text(value, include_secrets=False):
    text = normalize_text(value)
    checks = (
        ("phone", PHONE),
        ("id_card", ID_CARD),
        ("email", EMAIL),
        ("wechat", WECHAT),
        ("medical_id", MEDICAL_ID),
        ("address", ADDRESS),
    )
    if include_secrets:
        checks = checks + (("secret", SECRET),)
    return sorted(set(label for label, pattern in checks if pattern.search(text)))


def scan_value(value, include_secrets=False):
    return scan_text(json.dumps(value, ensure_ascii=False), include_secrets=include_secrets)


def scan_file(path, include_secrets=False, max_bytes=4 * 1024 * 1024,
              include_personal_data=True):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > max_bytes:
        return []
    try:
        with io.open(str(path), "r", encoding="utf-8") as handle:
            text = handle.read()
            if include_personal_data:
                return scan_text(text, include_secrets=include_secrets)
            return ["secret"] if include_secrets and SECRET.search(normalize_text(text)) else []
    except (IOError, UnicodeDecodeError):
        return []
