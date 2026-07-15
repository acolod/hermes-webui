"""Fail-closed isolation guard for downstream updater and routing tests."""
from __future__ import annotations

import configparser
import os
import sys
import tempfile
from pathlib import Path

_AGENT = Path("/home/alex/.hermes/hermes-agent")
_WEBUI = Path("/home/alex/hermes-webui")
_INSTALLED_UPDATER = Path("/home/alex/.local/bin/hermes-downstream-update")
_LIVE_PORTS = {8787, 8789}
_SERVICE_EXECUTABLES = {"systemctl", "systemd-run"}
_RESTART_HELPERS = {"hermes-gateway-restart", "restart-webui", "managed_update_resume.py"}
_AUDIT_INSTALLED = False


def _failure(detail: str) -> RuntimeError:
    return RuntimeError(f"downstream test isolation: {detail}")


def _as_path(value) -> Path | None:
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return Path(os.path.abspath(os.path.expanduser(value)))
    except (OSError, ValueError):
        return None


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def assert_safe_path(value, mode="r") -> None:
    path = _as_path(value)
    if path is None:
        return
    mode_text = str(mode or "r")
    for root, label in ((_AGENT, "Agent"), (_WEBUI, "WebUI")):
        if not _inside(path, root):
            continue
        is_git = path == root / ".git" or root / ".git" in path.parents
        writes = any(flag in mode_text for flag in ("w", "a", "+", "x"))
        if is_git or writes:
            raise _failure(f"canonical {label} target access blocked: {path}")


def assert_safe_network(address) -> None:
    try:
        host, port = address[0], int(address[1])
    except (TypeError, ValueError, IndexError):
        return
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    host = str(host).strip().lower().strip("[]")
    if port in _LIVE_PORTS and host in {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}:
        raise _failure(f"live WebUI loopback port blocked: {host}:{port}")


def _fixture_git_remote(parts: list[str], cwd_path: Path, safe_roots: set[Path]) -> bool:
    try:
        push_at = parts.index("push")
        remote = next(part for part in parts[push_at + 1:] if not part.startswith("-"))
        config_path = cwd_path / ".git" / "config"
        parser = configparser.ConfigParser()
        if not config_path.is_file() or not parser.read(config_path):
            return False
        url = parser.get(f'remote "{remote}"', "url")
        for section in parser.sections():
            if not section.startswith('url "') or not section.endswith('"'):
                continue
            prefix = parser.get(section, "insteadof", fallback="")
            if prefix and url.startswith(prefix):
                url = section[5:-1] + url[len(prefix):]
                break
        if url.startswith("file://"):
            url = url[7:]
        resolved = _as_path(url)
        return resolved is not None and any(_inside(resolved.resolve(), root) for root in safe_roots)
    except (ValueError, StopIteration, configparser.Error, OSError):
        return False


def assert_safe_subprocess(argv, cwd=None) -> None:
    if isinstance(argv, (str, bytes)):
        parts = [os.fsdecode(argv)]
    else:
        parts = [os.fsdecode(part) if isinstance(part, bytes) else str(part) for part in (argv or [])]
    if not parts:
        return
    executable = Path(parts[0]).name
    joined = "\0".join(parts)
    inspected_parts = parts[1:] if _as_path(parts[0]) == _as_path(sys.executable) else parts
    inspected = "\0".join(inspected_parts)
    if executable in _SERVICE_EXECUTABLES:
        raise _failure(f"service manager blocked: {executable}")
    if any(helper in inspected for helper in _RESTART_HELPERS):
        raise _failure("real restart helper blocked")
    if str(_INSTALLED_UPDATER) in inspected:
        raise _failure("installed updater binary blocked")
    for root, label in ((_AGENT, "Agent"), (_WEBUI, "WebUI")):
        if str(root) in inspected:
            raise _failure(f"canonical {label} target subprocess blocked")
    cwd_path = _as_path(cwd or os.getcwd())
    if cwd_path and (_inside(cwd_path, _AGENT) or _inside(cwd_path, _WEBUI)):
        raise _failure(f"canonical target subprocess cwd blocked: {cwd_path}")
    if executable == "git" and "push" in parts[1:]:
        if "github.com" in joined.lower():
            raise _failure("GitHub push blocked")
        safe_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve(), Path("/var/tmp").resolve()}
        if (
            cwd_path is None
            or not any(_inside(cwd_path.resolve(), root) for root in safe_roots)
            or not _fixture_git_remote(parts, cwd_path.resolve(), safe_roots)
        ):
            raise _failure("non-fixture git push blocked")


def _audit_active() -> bool:
    return (
        os.environ.get("HERMES_DOWNSTREAM_TEST_GUARD") == "1"
        and os.environ.get("HERMES_DOWNSTREAM_TEST_GUARD_ACTIVE") == "1"
        and bool(os.environ.get("PYTEST_CURRENT_TEST"))
    )


def install_audit_guard() -> None:
    global _AUDIT_INSTALLED
    if _AUDIT_INSTALLED:
        return

    def hook(event, args):
        if not _audit_active():
            return
        if event == "open" and args:
            mode = args[1] if len(args) > 1 else "r"
            flags = args[2] if len(args) > 2 else 0
            if isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
                mode = "w"
            assert_safe_path(args[0], mode)
        elif event == "subprocess.Popen" and len(args) >= 3:
            assert_safe_subprocess(args[1], cwd=args[2])
        elif event == "socket.connect" and len(args) >= 2:
            assert_safe_network(args[1])

    sys.addaudithook(hook)
    _AUDIT_INSTALLED = True
