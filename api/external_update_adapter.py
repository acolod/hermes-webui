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


def _refusal(reason: str, message: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "message": message}


def _validated_payload(
    operation: str,
    target: str,
    payload: Any,
) -> dict[str, Any]:
    """Validate and normalize the updater's closed single-target contract."""
    if (
        not isinstance(payload, dict)
        or set(payload) != {"ok", "results"}
        or not isinstance(payload.get("ok"), bool)
    ):
        return _refusal(
            "managed_updater_invalid_response",
            "Managed updater returned an invalid response.",
        )
    results = payload["results"]
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        return _refusal(
            "managed_updater_invalid_response",
            "Managed updater returned an invalid results contract.",
        )
    result = results[0]
    if result.get("target") != target:
        return _refusal(
            "managed_updater_target_mismatch",
            "Managed updater returned a result for the wrong target.",
        )

    check_keys = {
        "target", "head", "upstream_ref", "local_only", "upstream_only",
        "upstream_commits", "up_to_date",
    }

    def valid_check(item: dict[str, Any]) -> bool:
        return (
            set(item) == check_keys
            and isinstance(item.get("head"), str)
            and bool(item["head"])
            and isinstance(item.get("upstream_ref"), str)
            and bool(item["upstream_ref"])
            and isinstance(item.get("local_only"), int)
            and not isinstance(item.get("local_only"), bool)
            and item["local_only"] >= 0
            and isinstance(item.get("upstream_only"), int)
            and not isinstance(item.get("upstream_only"), bool)
            and item["upstream_only"] >= 0
            and isinstance(item.get("upstream_commits"), list)
            and all(isinstance(commit, str) for commit in item["upstream_commits"])
            and isinstance(item.get("up_to_date"), bool)
            and item["up_to_date"] is (item["upstream_only"] == 0)
        )

    if operation == "check":
        if payload["ok"] is not True or not valid_check(result):
            return _refusal(
                "managed_updater_invalid_response",
                "Managed updater returned an invalid check result.",
            )
        return {"ok": True, "results": [dict(result)]}

    if operation != "update":
        return _refusal(
            "managed_updater_invalid_response",
            "Managed updater operation was not recognized.",
        )

    up_to_date_keys = {"ok", "target", "up_to_date", "summary"}
    if set(result) == up_to_date_keys:
        summary = result.get("summary")
        if (
            payload["ok"] is True
            and result.get("ok") is True
            and result.get("up_to_date") is True
            and isinstance(summary, dict)
            and summary.get("target") == target
            and valid_check(summary)
            and summary["up_to_date"] is True
        ):
            return {
                "ok": True,
                "target": target,
                "up_to_date": True,
                "message": f"{target.capitalize()} is already up to date.",
            }
        return _refusal(
            "managed_updater_invalid_response",
            "Managed updater returned an invalid up-to-date result.",
        )

    conflict_keys = {
        "ok", "target", "conflict", "candidate_id", "candidate_path",
        "message", "recovery_tag", "bundle",
    }
    if set(result) == conflict_keys:
        if (
            payload["ok"] is False
            and result.get("ok") is False
            and result.get("conflict") is True
            and all(
                isinstance(result.get(field), str) and bool(result[field])
                for field in ("candidate_id", "candidate_path", "message", "recovery_tag", "bundle")
            )
        ):
            return {
                "ok": False,
                "target": target,
                "reason": "managed_update_conflict",
                "message": "Managed update requires manual conflict resolution.",
            }
        return _refusal(
            "managed_updater_invalid_response",
            "Managed updater returned an invalid conflict result.",
        )

    completion_keys = {
        "ok", "target", "candidate_id", "original_head", "new_head",
        "recovery_tag", "bundle", "restart_required", "message",
    }
    if set(result) == completion_keys:
        valid_completion = (
            isinstance(result.get("ok"), bool)
            and payload["ok"] is result["ok"]
            and isinstance(result.get("restart_required"), bool)
            and result["ok"] is (not result["restart_required"])
            and all(
                isinstance(result.get(field), str) and bool(result[field])
                for field in (
                    "candidate_id", "original_head", "new_head", "recovery_tag",
                    "bundle", "message",
                )
            )
        )
        if not valid_completion:
            return _refusal(
                "managed_updater_invalid_response",
                "Managed updater returned an invalid completion result.",
            )
        if result["restart_required"]:
            return {
                "ok": False,
                "target": target,
                "reason": "managed_update_restart_required",
                "message": (
                    f"{target.capitalize()} code was updated, but restart or "
                    "health verification did not complete."
                ),
            }
        return {
            "ok": True,
            "target": target,
            "message": f"{target.capitalize()} updated successfully.",
        }

    return _refusal(
        "managed_updater_invalid_response",
        "Managed updater returned an unknown update result shape.",
    )


def _call(*args: str, timeout: int) -> dict[str, Any]:
    if not UPDATER.is_file():
        return {
            "ok": False,
            "reason": "managed_updater_unavailable",
            "message": "Managed updater is unavailable.",
        }
    try:
        proc = subprocess.run(
            [str(UPDATER), "--config", str(CONFIG), "--json", *args],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "reason": "managed_updater_timeout",
            "message": f"Managed updater timed out after {timeout} seconds.",
        }
    except OSError:
        return {
            "ok": False,
            "reason": "managed_updater_execution_failed",
            "message": "Managed updater could not be started.",
        }
    if proc.returncode != 0:
        return {
            "ok": False,
            "reason": "managed_updater_nonzero_exit",
            "message": f"Managed updater exited with status {proc.returncode}.",
        }
    try:
        payload = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return _refusal(
            "managed_updater_invalid_response",
            "Managed updater returned invalid JSON.",
        )
    operation = args[0] if len(args) > 0 else ""
    target = args[1] if len(args) > 1 else ""
    return _validated_payload(operation, target, payload)


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
