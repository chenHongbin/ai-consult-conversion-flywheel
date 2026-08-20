#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single source of truth for the public Core and workspace schema versions."""

import io
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = PROJECT_ROOT / "VERSION"
WORKSPACE_SCHEMA_VERSION = "v2.1.3"
SUPPORTED_WORKSPACE_SCHEMAS = ("v1.9", "v2.0", "v2.1", "v2.1.2", "v2.1.3")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def core_version():
    with io.open(str(VERSION_FILE), "r", encoding="utf-8") as handle:
        value = handle.read().strip()
    if not VERSION_PATTERN.match(value):
        raise ValueError("VERSION must contain one semantic version such as 2.2.0")
    return value


def core_version_tag():
    return "v" + core_version()
