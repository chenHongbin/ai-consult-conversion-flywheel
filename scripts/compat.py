"""Small Python 3.4 compatibility helpers used by bundled scripts."""

import os
from pathlib import Path


def expand_path(value):
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def ensure_dir(path):
    directory = str(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
