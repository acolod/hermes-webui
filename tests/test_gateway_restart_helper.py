from api import gateway_restart


def test_restart_command_uses_system_scope_with_sudo_when_system_gateway_active(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway_restart, "_system_gateway_is_active", lambda: True)
    monkeypatch.setattr(gateway_restart, "get_active_hermes_home", lambda: tmp_path)

    command, env, scope = gateway_restart._restart_command_for_active_scope("/home/alex/.local/bin/hermes")

    assert command == [
        "sudo",
        "-n",
        "/home/alex/.local/bin/hermes",
        "gateway",
        "restart",
        "--system",
    ]
    assert env["HERMES_HOME"] == str(tmp_path)
    assert scope == "system"


def test_restart_command_uses_user_scope_when_system_gateway_inactive(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway_restart, "_system_gateway_is_active", lambda: False)
    monkeypatch.setattr(gateway_restart, "get_active_hermes_home", lambda: tmp_path)

    command, env, scope = gateway_restart._restart_command_for_active_scope("/home/alex/.local/bin/hermes")

    assert command == ["/home/alex/.local/bin/hermes", "gateway", "restart"]
    assert env["HERMES_HOME"] == str(tmp_path)
    assert scope == "user"


def test_system_gateway_is_active_checks_systemctl(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = "active\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Result()

    monkeypatch.setattr(gateway_restart.subprocess, "run", fake_run)
    monkeypatch.setenv("HERMES_GATEWAY_SERVICE_NAME", "custom-gateway.service")

    assert gateway_restart._system_gateway_is_active() is True
    assert calls[0][0] == ["systemctl", "is-active", "custom-gateway.service"]
    assert calls[0][1]["timeout"] == 5
    assert calls[0][1]["check"] is False
