import json
from types import SimpleNamespace

import pytest

from api import config as webui_config
from api import managed_update_resume as managed_resume
from api import updates as webui_updates


def test_api_config_is_the_single_health_endpoint_source(monkeypatch):
    monkeypatch.setattr(webui_config, "HOST", "0.0.0.0")
    monkeypatch.setattr(webui_config, "PORT", 43123)

    assert webui_config.configured_health_url() == "http://127.0.0.1:43123/health"
    assert managed_resume._configured_health_url() == webui_config.configured_health_url()
    assert webui_updates._configured_webui_health_url() == webui_config.configured_health_url()


def test_configured_health_url_uses_actual_port_and_loopback_safe_host(monkeypatch):
    from api import managed_update_resume as helper

    monkeypatch.setattr(helper.api_config, "HOST", "0.0.0.0")
    monkeypatch.setattr(helper.api_config, "PORT", 43123)

    assert helper._configured_health_url() == "http://127.0.0.1:43123/health"


def test_health_waiter_rejects_old_process_until_started_marker_changes(monkeypatch):
    from api import managed_update_resume as helper

    payloads = iter((
        {"status": "ok", "server_started_at": 123.5},
        {"status": "ok", "server_started_at": 123.5},
        {"status": "ok", "server_started_at": 124.0},
    ))
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            payload = next(payloads)
            seen.append(payload["server_started_at"])
            return json.dumps(payload).encode()

    monkeypatch.setattr(helper.urllib.request, "urlopen", lambda *_a, **_k: Response())
    monkeypatch.setattr(helper.time, "sleep", lambda _interval: None)

    assert helper._wait_for_health("http://127.0.0.1:43123/health", 123.5, timeout=1.0, interval=0) is True
    assert seen == [123.5, 123.5, 124.0]


def test_restart_completion_waits_for_health_then_resumes_exact_transaction(monkeypatch, tmp_path):
    from api import managed_update_resume as helper

    updater = tmp_path / "updater"
    updater.write_text("x")
    updater.chmod(0o755)
    calls = []
    seen_health = []
    monkeypatch.setattr(helper, "_wait_for_health", lambda url, previous: seen_health.append((url, previous)) or True)

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"schema_version": 1, "state": "completed", "transaction_id": "1720000000-aaaaaaaaaaaaaaaa"}) + "\n",
            stderr="",
        )

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    rc = helper.main(["--updater", str(updater), "--transaction", "1720000000-aaaaaaaaaaaaaaaa", "--target", "webui", "--previous-server-started-at", "123.5"])

    assert rc == 0
    assert seen_health == [(helper._configured_health_url(), 123.5)]
    assert calls[0][0] == [str(updater), "resume", "1720000000-aaaaaaaaaaaaaaaa"]
    assert calls[0][1]["shell"] is False


def test_restart_completion_never_resumes_before_health(monkeypatch, tmp_path):
    from api import managed_update_resume as helper

    updater = tmp_path / "updater"
    updater.write_text("x")
    updater.chmod(0o755)
    monkeypatch.setattr(helper, "_wait_for_health", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(helper.subprocess, "run", lambda *_a, **_k: pytest.fail("resume must not run before health"))

    assert helper.main(["--updater", str(updater), "--transaction", "1720000001-bbbbbbbbbbbbbbbb", "--target", "webui", "--previous-server-started-at", "123.5"]) == 70
