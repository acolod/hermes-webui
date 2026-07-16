"""Thin WebUI adapter for the external updater.

Integrate these functions into api/updates.py for configured managed targets.
No caller may fall back to stock Git behavior after this adapter is selected.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

UPDATER = Path("/home/alex/.local/bin/hermes-safe-update")
CONFIG = Path("/home/alex/.config/hermes-safe-update/targets.json")


def _call(*args: str, timeout: int) -> dict[str, Any]:
    if not UPDATER.is_file():
        return {
            "ok": False,
            "reason": "managed_updater_unavailable",
            "message": f"Managed updater is unavailable: {UPDATER}",
        }
    proc = subprocess.run(
        [str(UPDATER), "--config", str(CONFIG), "--json", *args],
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return {
            "ok": False,
            "reason": "managed_updater_invalid_response",
            "message": (proc.stderr or proc.stdout or "No updater output")[-1000:],
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "reason": "managed_updater_invalid_response",
            "message": "Updater did not return a JSON object",
        }
    return payload


def managed_check(target: str) -> dict[str, Any]:
    return _call("check", target, timeout=60)


def managed_update(target: str) -> dict[str, Any]:
    return _call("update", target, timeout=3600)


def managed_force_refusal(target: str) -> dict[str, Any]:
    return {
        "ok": False,
        "target": target,
        "reason": "managed_force_update_disabled",
        "message": "Force Update is disabled for managed downstream targets.",
    }
