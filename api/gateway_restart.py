"""Helpers for restarting the active-profile Hermes gateway."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from api.profiles import get_active_hermes_home

logger = logging.getLogger(__name__)

_GATEWAY_RESTART_LOCK = threading.Lock()


def _resolve_hermes_command() -> str:
    """Resolve the CLI path used for active-profile gateway restarts."""
    hermes_cmd = shutil.which("hermes")
    if hermes_cmd:
        return hermes_cmd

    sibling = Path(sys.executable).parent / "hermes"
    if sibling.exists():
        return str(sibling)
    return "hermes"


def _gateway_service_name() -> str:
    return os.getenv("HERMES_GATEWAY_SERVICE_NAME", "hermes-gateway.service")


def _system_gateway_is_active() -> bool:
    """Return True when the system-scoped gateway service is active."""
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", _gateway_service_name()],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip() == "active"


def _restart_command_for_active_scope(hermes_cmd: str) -> tuple[list[str], dict[str, str], str]:
    """Choose the gateway restart command for the currently active service scope."""
    active_home = get_active_hermes_home()
    env = os.environ.copy()
    env["HERMES_HOME"] = str(active_home)

    if _system_gateway_is_active():
        return ["sudo", "-n", hermes_cmd, "gateway", "restart", "--system"], env, "system"

    return [hermes_cmd, "gateway", "restart"], env, "user"


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
        pass


def restart_active_profile_gateway(
    *,
    quick_timeout_seconds: float = 2.0,
    background_wait_seconds: float = 240.0,
) -> dict:
    """Run a non-blocking gateway restart for the active profile/service scope."""
    if not _GATEWAY_RESTART_LOCK.acquire(blocking=False):
        return {
            "status": "busy",
            "message": "Restart already in progress. Please wait a moment and try again.",
        }

    try:
        hermes_cmd = _resolve_hermes_command()
        command, env, scope = _restart_command_for_active_scope(hermes_cmd)

        logger.info(
            "Restarting %s gateway service via CLI command: %s (HERMES_HOME=%s)",
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
                    "scope": scope,
                }

            logger.error("Gateway service restart failed with code %s: %s", proc.returncode, stderr)
            return {
                "status": "failed",
                "message": f"Restart failed: {stderr or stdout}",
                "detail": stdout or stderr,
                "returncode": proc.returncode,
                "scope": scope,
            }

        except subprocess.TimeoutExpired:
            logger.info(
                "Gateway restart is taking longer than %.1fs (likely draining in-flight runs); continuing in background",
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
                                logger.error("Gateway restart process refused to die even after SIGKILL.")
                    except Exception:
                        logger.exception("Failed to terminate timed out gateway restart process.")
                finally:
                    _release_lock()

            threading.Thread(target=_wait_and_release, daemon=True).start()
            return {
                "status": "in_progress",
                "message": "Gateway service restart initiated (in progress)",
                "scope": scope,
            }
    except Exception as exc:
        _release_lock()
        logger.exception("Failed to run gateway restart command")
        return {
            "status": "failed",
            "message": f"Internal error running restart: {type(exc).__name__}: {exc}",
        }
