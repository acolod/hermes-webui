#!/usr/bin/env python3
"""Complete a managed WebUI updater transaction after the WebUI re-execs."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from api import config as api_config


def _configured_health_url() -> str:
    return api_config.configured_health_url()


def _wait_for_health(url: str, previous_server_started_at: float, timeout: float = 90.0, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            current_started_at = float(payload.get("server_started_at"))
            if payload.get("status") == "ok" and current_started_at != previous_server_started_at:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--updater", required=True)
    parser.add_argument("--transaction", required=True)
    parser.add_argument("--target", choices=("webui",), required=True)
    parser.add_argument("--previous-server-started-at", type=float, required=True)
    args = parser.parse_args(argv)

    updater = Path(args.updater)
    if (
        not updater.is_absolute()
        or updater.is_symlink()
        or not updater.is_file()
        or not os.access(updater, os.X_OK)
        or not re.fullmatch(r"[0-9]+-[0-9a-f]{16}", args.transaction)
    ):
        print("invalid managed restart-completion authority", flush=True)
        return 70

    health_url = _configured_health_url()
    if not _wait_for_health(health_url, args.previous_server_started_at):
        print("WebUI did not become healthy; transaction remains safely resumable", flush=True)
        return 70

    completed = subprocess.run(
        [str(updater), "resume", args.transaction],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        shell=False,
    )
    if completed.stdout:
        print(completed.stdout.strip(), flush=True)
    if completed.stderr:
        print(completed.stderr.strip(), flush=True)
    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    try:
        payload = json.loads(lines[0]) if len(lines) == 1 else None
    except Exception:
        payload = None
    if (
        completed.returncode != 0
        or not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("transaction_id") != args.transaction
        or payload.get("state") != "completed"
    ):
        return completed.returncode or 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
