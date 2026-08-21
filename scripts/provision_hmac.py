#!/usr/bin/env python3
"""Create one owner/executor pair key in two explicitly named, exclusive files."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def write(path: Path, key: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--owner-output", type=Path, required=True)
    p.add_argument("--executor-output", type=Path, required=True)
    a = p.parse_args()
    key = os.urandom(32)
    write(a.owner_output, key)
    write(a.executor_output, key)
    print(
        "created two 32-byte pair-key files; "
        "transfer each only to its named role"
    )


if __name__ == "__main__":
    main()