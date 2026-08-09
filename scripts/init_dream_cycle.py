#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backward-compatible entry point; v0.4 uses the learner-facing workspace layout."""

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from init_consult_workspace import main  # noqa: E402


if __name__ == "__main__":
    # Older callers passed the target workspace directly. Keep that behavior,
    # but use the v0.4 visible folders and system area.
    sys.argv.insert(2, "--use-root")
    main()
