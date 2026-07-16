import subprocess
from unittest.mock import MagicMock

from api import gateway_restart


SAFE_HELPER = "/usr/local/sbin/hermes-gateway-restart-safe"
USER_HERMES = "/home/alex/.local/bin/hermes"


def test_active_default_system_gateway_uses_only_root_owned_helper(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway_restart, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(gateway_restart, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(gateway_restart, "_system_gateway_state", lambda: True)
    monkeypatch.setattr(gateway_restart, "_resolve_hermes_command", lambda: USER_HERMES)

    command, env, scope = gateway_restart._restart_command_for_active_profile()

    assert command == ["sudo", "-n", SAFE_HELPER]
    assert USER_HERMES not in command
    assert env["HERMES_HOME"] == str(tmp_path)
    assert scope == "system"


def test_active_non_default_profile_keeps_upstream_profile_aware_command(monkeypatch, tmp_path):
    system_probe = MagicMock(side_effect=AssertionError("non-default profiles must not use the fixed helper"))
    monkeypatch.setattr(gateway_restart, "get_active_profile_name", lambda: "worker")
    monkeypatch.setattr(gateway_restart, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(gateway_restart, "_is_root_profile", lambda profile: False)
    monkeypatch.setattr(gateway_restart, "_system_gateway_state", system_probe)
    monkeypatch.setattr(gateway_restart, "_resolve_hermes_command", lambda: USER_HERMES)

    command, env, scope = gateway_restart._restart_command_for_active_profile()

    assert command == [USER_HERMES, "--profile", "worker", "gateway", "restart"]
    assert env["HERMES_HOME"] == str(tmp_path)
    assert scope == "profile"
    system_probe.assert_not_called()


def test_explicit_default_profile_does_not_use_fixed_system_helper(monkeypatch, tmp_path):
    system_probe = MagicMock(side_effect=AssertionError("explicit profile routing must stay upstream"))
    monkeypatch.setattr(gateway_restart, "get_hermes_home_for_profile", lambda profile: tmp_path)
    monkeypatch.setattr(gateway_restart, "_is_root_profile", lambda profile: False)
    monkeypatch.setattr(gateway_restart, "_system_gateway_state", system_probe)
    monkeypatch.setattr(gateway_restart, "_resolve_hermes_command", lambda: USER_HERMES)

    command, env, scope = gateway_restart._restart_command_for_active_profile("default")

    assert command == [USER_HERMES, "--profile", "default", "gateway", "restart"]
    assert env["HERMES_HOME"] == str(tmp_path)
    assert scope == "profile"
    system_probe.assert_not_called()


def test_active_default_user_gateway_keeps_upstream_hermes_command(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway_restart, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(gateway_restart, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(gateway_restart, "_system_gateway_state", lambda: False)
    monkeypatch.setattr(gateway_restart, "_resolve_hermes_command", lambda: USER_HERMES)

    command, env, scope = gateway_restart._restart_command_for_active_profile()

    assert command == [USER_HERMES, "--profile", "default", "gateway", "restart"]
    assert env["HERMES_HOME"] == str(tmp_path)
    assert scope == "profile"


def test_unknown_active_default_gateway_type_refuses_without_popen(monkeypatch, tmp_path):
    gateway_restart._GATEWAY_RESTART_LOCK = __import__("threading").Lock()
    monkeypatch.setattr(gateway_restart, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(gateway_restart, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(gateway_restart, "_system_gateway_state", lambda: None)
    popen = MagicMock(side_effect=AssertionError("uncertain gateway type must not restart"))
    monkeypatch.setattr(gateway_restart.subprocess, "Popen", popen)

    result = gateway_restart.restart_active_profile_gateway()

    assert result["status"] == "failed"
    assert "could not determine whether the active default gateway is system-managed" in result["message"].lower()
    popen.assert_not_called()


def test_system_gateway_probe_uses_fixed_service_name_and_is_tri_state(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="active\n", stderr="")

    monkeypatch.setenv("HERMES_GATEWAY_SERVICE_NAME", "attacker-controlled.service")
    monkeypatch.setattr(gateway_restart.subprocess, "run", fake_run)

    assert gateway_restart._system_gateway_state() is True
    assert calls[0][0] == ["systemctl", "is-active", "hermes-gateway.service"]
    assert calls[0][1]["timeout"] == 5
    assert calls[0][1]["check"] is False

    monkeypatch.setattr(
        gateway_restart.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 3, stdout="inactive\n", stderr=""
        ),
    )
    assert gateway_restart._system_gateway_state() is False

    monkeypatch.setattr(
        gateway_restart.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 4, stdout="unknown\n", stderr=""
        ),
    )
    assert gateway_restart._system_gateway_state() is None
