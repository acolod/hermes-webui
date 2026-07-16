"""Helpers for restarting the active-profile Hermes gateway."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from api.profiles import (
    _PROFILE_ID_RE,
    _is_root_profile,
    get_active_hermes_home,
    get_active_profile_name,
    get_hermes_home_for_profile,
)

logger = logging.getLogger(__name__)

_GATEWAY_RESTART_LOCK = threading.Lock()
_SYSTEM_GATEWAY_SERVICE = "hermes-gateway.service"
_SYSTEM_GATEWAY_RESTART_HELPER = "/usr/local/sbin/hermes-gateway-restart-safe"


def _resolve_hermes_command() -> str:
    """Resolve the CLI path used for active-profile gateway restarts."""
    hermes_cmd = shutil.which("hermes")
    if hermes_cmd:
        return hermes_cmd

    sibling = Path(sys.executable).parent / "hermes"
    if sibling.exists():
        return str(sibling)
    return "hermes"


def _system_gateway_state() -> bool | None:
    """Return whether the fixed system gateway is active, or None if unknown."""
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", _SYSTEM_GATEWAY_SERVICE],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None

    state = (proc.stdout or "").strip().lower()
    if proc.returncode == 0 and state == "active":
        return True
    if proc.returncode == 3 and state in {"inactive", "failed"}:
        return False
    return None


def _consume_stream(stream) -> None:
    """Drain a subprocess stream to prevent stdout/stderr pipe deadlocks."""
    try:
        while stream and stream.read(4096):
            pass
    except Exception:
        pass


def _release_lock() -> None:
    try:
        _GATEWAY_RESTART_LOCK.release()
    except RuntimeError:
        # The lock may already have been released by another path.
        pass


def _gateway_restart_profile_details(
    profile: str | None = None,
) -> tuple[Path, str | None, str]:
    """Resolve one profile identity for both routing and command construction."""
    if profile is None:
        raw_profile = str(get_active_profile_name() or "default").strip()
        active_home = Path(get_active_hermes_home())
    else:
        raw_profile = str(profile or "")
        if not raw_profile or not _PROFILE_ID_RE.fullmatch(raw_profile):
            raise ValueError(f"Invalid profile for gateway restart: {profile!r}")
        active_home = Path(get_hermes_home_for_profile(raw_profile))

    if (
        raw_profile == "default"
        and active_home.name == "default"
        and active_home.parent.name == "profiles"
    ):
        return active_home, None, raw_profile
    if not raw_profile or not _PROFILE_ID_RE.fullmatch(raw_profile) or _is_root_profile(raw_profile):
        return active_home, "default", raw_profile
    return active_home, raw_profile, raw_profile


def _gateway_restart_profile_context(profile: str | None = None) -> tuple[Path, str | None]:
    """Return the upstream HERMES_HOME and CLI profile argument."""
    active_home, cli_profile, _ = _gateway_restart_profile_details(profile)
    return active_home, cli_profile


def _restart_command_for_active_profile(
    profile: str | None = None,
) -> tuple[list[str], dict[str, str], str]:
    """Select the fixed system helper only for the active default gateway."""
    active_home, cli_profile, raw_profile = _gateway_restart_profile_details(profile)
    env = os.environ.copy()
    env["HERMES_HOME"] = str(active_home)

    active_default = (
        profile is None
        and (raw_profile == "default" or _is_root_profile(raw_profile))
    )
    if active_default:
        system_state = _system_gateway_state()
        if system_state is None:
            raise RuntimeError(
                "Could not determine whether the active default gateway is "
                "system-managed; refusing to restart"
            )
        if system_state:
            return ["sudo", "-n", _SYSTEM_GATEWAY_RESTART_HELPER], env, "system"

    hermes_cmd = _resolve_hermes_command()
    command = [hermes_cmd]
    if cli_profile is not None:
        command.extend(["--profile", cli_profile])
    command.extend(["gateway", "restart"])
    return command, env, "profile"


def restart_active_profile_gateway(
    *,
    profile: str | None = None,
    quick_timeout_seconds: float = 2.0,
    background_wait_seconds: float = 240.0,
) -> dict:
    """Run a non-blocking ``hermes gateway restart`` for the active profile.

    Returns a short status dict with these values:
    - completed: command finished quickly and succeeded.
    - in_progress: command did not finish within ``quick_timeout_seconds``.
    - failed: command finished quickly with non-zero exit status.
    - busy: restart already in progress from another caller.
    """
    if not _GATEWAY_RESTART_LOCK.acquire(blocking=False):
        return {
            "status": "busy",
            "message": "Restart already in progress. Please wait a moment and try again.",
        }

    try:
        command, env, scope = _restart_command_for_active_profile(profile)

        logger.info(
            "Restarting %s gateway via command: %s (HERMES_HOME=%s)",
            scope,
            " ".join(command),
            env.get("HERMES_HOME"),
        )
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        try:
            stdout, stderr = proc.communicate(timeout=quick_timeout_seconds)
            _release_lock()
            stdout = (stdout or "").strip()
            stderr = (stderr or "").strip()
            if proc.returncode == 0:
                logger.info("Gateway service restarted successfully: %s", stdout)
                return {
                    "status": "completed",
                    "message": "Gateway service restarted successfully",
                    "detail": stdout or stderr,
                }

            logger.error("Gateway service restart failed with code %s: %s", proc.returncode, stderr)
            return {
                "status": "failed",
                "message": f"Restart failed: {stderr or stdout}",
                "detail": stdout or stderr,
                "returncode": proc.returncode,
            }

        except subprocess.TimeoutExpired:
            logger.info(
                "Gateway restart is taking longer than %.1fs (likely draining in-flight runs);"
                " continuing in background",
                quick_timeout_seconds,
            )

            threading.Thread(target=_consume_stream, args=(proc.stdout,), daemon=True).start()
            threading.Thread(target=_consume_stream, args=(proc.stderr,), daemon=True).start()

            def _wait_and_release() -> None:
                try:
                    proc.wait(timeout=background_wait_seconds)
                except subprocess.TimeoutExpired:
                    logger.error(
                        "Gateway restart process timed out after %.1fs. Terminating process.",
                        background_wait_seconds,
                    )
                    try:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5.0)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            try:
                                proc.wait(timeout=5.0)
                            except subprocess.TimeoutExpired:
                                logger.error(
                                    "Gateway restart process refused to die even after SIGKILL.",
                                )
                    except Exception:
                        logger.exception("Failed to terminate timed out gateway restart process.")
                finally:
                    _release_lock()

            threading.Thread(target=_wait_and_release, daemon=True).start()
            return {
                "status": "in_progress",
                "message": "Gateway service restart initiated (in progress)",
            }
    except Exception as exc:
        _release_lock()
        logger.exception("Failed to run gateway restart command")
        return {
            "status": "failed",
            "message": f"Internal error running restart: {type(exc).__name__}: {exc}",
        }
