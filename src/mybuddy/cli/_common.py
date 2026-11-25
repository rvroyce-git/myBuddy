"""Helpers shared by the command modules."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tempfile
from collections.abc import Collection
from pathlib import Path
from typing import Any

from mybuddy.config import DEFAULT_CONFIG_PATH

EX_CONFIG = 78  # "not configured": the systemd unit does not restart on this exit code


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _config_path(args: argparse.Namespace) -> Path:
    if args.config:
        return Path(args.config)
    env = os.environ.get("MYBUDDY_CONFIG")
    return Path(env) if env else DEFAULT_CONFIG_PATH


def write_private(path: Path, text: str) -> None:
    """Atomically replace `path` with a 0600 file (it holds secrets; never briefly 0644)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# The factories live on the `mybuddy.cli` package (make_http, make_bot_http, make_clock) so
# tests can monkeypatch them there; look them up at call time, never at import time.
def new_http(allowed_hosts: Collection[str]) -> Any:
    from mybuddy import cli

    return cli.make_http(allowed_hosts)


def new_bot_http(allowed_hosts: Collection[str]) -> Any:
    from mybuddy import cli

    return cli.make_bot_http(allowed_hosts)
