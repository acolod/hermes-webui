import socket
import subprocess

import pytest

from downstream_test_isolation import (
    assert_safe_network,
    assert_safe_path,
    assert_safe_subprocess,
)


def test_guard_blocks_canonical_agent_and_webui_target_access():
    for path in (
        "/home/alex/.hermes/hermes-agent/.git/HEAD",
        "/home/alex/hermes-webui/.git/HEAD",
    ):
        with pytest.raises(RuntimeError, match="downstream test isolation"):
            assert_safe_path(path, "r")


def test_guard_blocks_live_webui_loopback_ports():
    for port in (8787, 8789):
        with pytest.raises(RuntimeError, match="downstream test isolation"):
            assert_safe_network(("127.0.0.1", port))


def test_guard_blocks_service_managers_restart_helpers_and_installed_updater():
    commands = (
        ["systemctl", "is-active", "hermes-webui.service"],
        ["systemd-run", "--user", "true"],
        ["/home/alex/.local/bin/hermes-gateway-restart"],
        ["/home/alex/.local/bin/hermes-downstream-update", "check", "agent"],
    )
    for command in commands:
        with pytest.raises(RuntimeError, match="downstream test isolation"):
            assert_safe_subprocess(command)


def test_guard_blocks_github_push_and_non_fixture_push(tmp_path):
    with pytest.raises(RuntimeError, match="downstream test isolation"):
        assert_safe_subprocess(["git", "push", "https://github.com/example/repo.git", "HEAD:main"])
    with pytest.raises(RuntimeError, match="downstream test isolation"):
        assert_safe_subprocess(["git", "push", "origin", "HEAD:main"], cwd="/home/alex")

    repo = tmp_path / "real-remote-repo"
    subprocess.run(["git", "init", str(repo)], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/repo.git"],
        cwd=repo,
        check=True,
    )
    with pytest.raises(RuntimeError, match="downstream test isolation"):
        assert_safe_subprocess(["git", "push", "origin", "HEAD:main"], cwd=repo)


def test_guard_is_automatic_for_real_stdlib_entrypoints():
    with pytest.raises(RuntimeError, match="downstream test isolation"):
        open("/home/alex/.hermes/hermes-agent/.git/HEAD", "rb")
    with pytest.raises(RuntimeError, match="downstream test isolation"):
        socket.create_connection(("127.0.0.1", 8789), timeout=0.01)
    with pytest.raises(RuntimeError, match="downstream test isolation"):
        subprocess.run(["systemctl", "is-active", "hermes-webui.service"], check=False)
