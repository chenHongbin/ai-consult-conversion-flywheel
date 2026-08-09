#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Report local runtime and IMA credential availability without making network calls."""

import json
import os
import shutil
import sys
from pathlib import Path


def configured_ima():
    config = Path(os.path.expanduser("~")) / ".config" / "ima"
    env_ready = bool(
        (os.environ.get("IMA_OPENAPI_CLIENTID") or os.environ.get("IMA_CLIENT_ID"))
        and (os.environ.get("IMA_OPENAPI_APIKEY") or os.environ.get("IMA_API_KEY"))
    )
    file_ready = (config / "client_id").is_file() and (config / "api_key").is_file()
    return {"credentials_available": env_ready or file_ready, "native_context": os.environ.get("IMA_NATIVE_CONTEXT", "unknown")}


def main():
    env = os.environ
    runtime = {
        "workbuddy": bool(env.get("WORKBUDDY_RUNTIME") or env.get("WORKBUDDY")),
        "trae": bool(env.get("TRAE_RUNTIME") or env.get("TRAE")),
        "codex": bool(env.get("CODEX_RUNTIME") or shutil.which("codex")),
        "claude": bool(env.get("CLAUDE_CODE") or shutil.which("claude")),
    }
    print(json.dumps({"runtime": runtime, "ima": configured_ima()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
