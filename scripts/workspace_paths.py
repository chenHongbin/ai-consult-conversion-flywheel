#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical workspace path resolution shared by every public Core release."""

import os
from pathlib import Path

from compat import expand_path


WORKSPACE_NAME = "咨询转化工作区"


def is_workspace_root(path):
    path = Path(path)
    return (path / "_系统").is_dir() and (
        (path / "07_我的产出").is_dir() or (path / "08_团队管理").is_dir()
    )


def locate_workspace(selected, must_exist=True):
    """Accept a standard workspace, its parent, or a legacy/uninitialised root.

    New initialisation always creates ``咨询转化工作区``.  The final return is
    deliberately retained for V1.x callers that used an empty directory as the
    workspace itself; it prevents path-contract fixes from breaking safe
    base-runtime and migration operations.
    """
    selected = expand_path(selected)
    if selected.name == WORKSPACE_NAME:
        return selected
    if is_workspace_root(selected):
        return selected
    child = selected / WORKSPACE_NAME
    if is_workspace_root(child):
        return child
    # Legacy workspaces may only have _系统 and not the complete current
    # layout.  An otherwise empty selected directory is a parent folder and
    # keeps the long-standing contract of using its standard child container.
    if (selected / "_系统").is_dir():
        return selected
    if not must_exist or (selected.exists() and selected.is_dir()):
        return child
    raise ValueError("未找到标准咨询转化工作区：{0}".format(selected))


def system_root(selected):
    return locate_workspace(selected) / "_系统"


def assert_within(path, root, label="path"):
    """Return a resolved path only when it remains inside the approved root."""
    # pathlib.Path.resolve() raises for a missing leaf on Python 3.4.  A path
    # boundary check must still be able to reject a missing or traversing
    # candidate cleanly, so canonicalise with realpath first.
    path = Path(os.path.realpath(str(expand_path(path))))
    root = Path(os.path.realpath(str(expand_path(root))))
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("{0} must stay inside workspace: {1}".format(label, path))
    return path
