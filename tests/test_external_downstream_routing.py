"""Focused routing contract for externally managed downstream updates."""
from unittest.mock import MagicMock, patch

import pytest

from api import external_update_adapter as adapter
from api import updates


@pytest.fixture(autouse=True)
def managed_machine_policy(monkeypatch):
    monkeypatch.setattr(updates, "_EXTERNALLY_MANAGED_TARGET_PATHS", {
        "webui": updates.REPO_ROOT,
        "agent": updates._AGENT_DIR,
    })


def _check_payload(target: str, *, upstream_only: int = 3) -> dict:
    return {
        "ok": True,
        "results": [
            {
                "target": target,
                "head": "abc123def456",
                "upstream_ref": "origin/main" if target == "agent" else "origin/master",
                "local_only": 2,
                "upstream_only": upstream_only,
                "upstream_commits": ["1111111 first", "2222222 second"],
                "up_to_date": upstream_only == 0,
            }
        ],
    }


def test_managed_check_invokes_external_adapter_and_maps_summary():
    def managed_check(target: str) -> dict:
        return _check_payload(target)

    with patch.object(updates, "managed_check", side_effect=managed_check) as external, \
         patch.object(updates, "_check_repo") as stock_check, \
         patch.object(updates, "_run_git") as stock_git, \
         patch.dict(updates._update_cache, {
             "webui": None,
             "agent": None,
             "checked_at": 0,
             "include_agent": True,
             "channel": "stable",
         }, clear=True):
        result = updates.check_for_updates(force=True, include_agent=True, channel="stable")

    assert [call.args[0] for call in external.call_args_list] == ["webui", "agent"]
    assert result["agent"]["behind"] == 3
    assert result["agent"]["local_ahead"] == 2
    assert result["agent"]["current_sha"] == "abc123def456"
    assert result["agent"]["branch"] == "origin/main"
    assert result["agent"]["upstream_commits"] == ["1111111 first", "2222222 second"]
    stock_check.assert_not_called()
    stock_git.assert_not_called()


def test_managed_apply_invokes_external_adapter():
    payload = {"ok": True, "target": "agent", "message": "updated"}
    with patch.object(updates, "managed_update", return_value=payload) as external, \
         patch.object(updates, "_apply_update_inner") as stock_apply, \
         patch.object(updates, "_run_git") as stock_git:
        result = updates.apply_update("agent")

    assert result == payload
    external.assert_called_once_with("agent")
    stock_apply.assert_not_called()
    stock_git.assert_not_called()


def test_missing_managed_updater_refuses_without_stock_fallback(tmp_path):
    with patch.object(adapter, "UPDATER", tmp_path / "missing-updater"), \
         patch.object(updates, "managed_check", adapter.managed_check), \
         patch.object(updates, "_check_repo") as stock_check, \
         patch.object(updates, "_run_git") as stock_git, \
         patch.dict(updates._update_cache, {
             "webui": None,
             "agent": None,
             "checked_at": 0,
             "include_agent": False,
             "channel": "stable",
         }, clear=True):
        result = updates.check_for_updates(force=True, include_agent=False, channel="stable")

    assert result["webui"]["reason"] == "managed_updater_unavailable"
    assert result["webui"]["can_apply"] is False
    assert str(tmp_path) not in result["webui"]["message"]
    stock_check.assert_not_called()
    stock_git.assert_not_called()


def test_invalid_managed_updater_json_refuses_without_stock_fallback(tmp_path):
    updater = tmp_path / "updater"
    updater.write_text("stub", encoding="utf-8")
    proc = MagicMock(
        stdout="not json SECRET_TOKEN",
        stderr="invalid JSON SECRET_TOKEN",
        returncode=0,
    )
    with patch.object(adapter, "UPDATER", updater), \
         patch.object(adapter.subprocess, "run", return_value=proc), \
         patch.object(updates, "managed_check", adapter.managed_check), \
         patch.object(updates, "_check_repo") as stock_check, \
         patch.object(updates, "_run_git") as stock_git, \
         patch.dict(updates._update_cache, {
             "webui": None,
             "agent": None,
             "checked_at": 0,
             "include_agent": False,
             "channel": "stable",
         }, clear=True):
        result = updates.check_for_updates(force=True, include_agent=False, channel="stable")

    assert result["webui"]["reason"] == "managed_updater_invalid_response"
    assert result["webui"]["can_apply"] is False
    assert "SECRET_TOKEN" not in result["webui"]["message"]
    stock_check.assert_not_called()
    stock_git.assert_not_called()


def test_force_update_refuses_for_managed_target():
    with patch.object(updates, "managed_force_refusal", return_value={
        "ok": False,
        "target": "webui",
        "reason": "managed_force_update_disabled",
        "message": "disabled",
    }) as refusal, patch.object(updates, "_run_git") as stock_git:
        result = updates.apply_force_update("webui")

    assert result["reason"] == "managed_force_update_disabled"
    refusal.assert_called_once_with("webui")
    stock_git.assert_not_called()


def test_clear_lock_refuses_for_managed_target():
    with patch.object(updates, "_apply_update_inner") as stock_apply, \
         patch.object(updates, "_inventory_locks") as stock_lock, \
         patch.object(updates, "_run_git") as stock_git:
        result = updates.apply_clear_lock("agent")

    assert result["ok"] is False
    assert result["reason"] == "managed_lock_owned_externally"
    stock_apply.assert_not_called()
    stock_lock.assert_not_called()
    stock_git.assert_not_called()


def test_managed_apply_failure_never_calls_stock_update():
    payload = {
        "ok": False,
        "reason": "managed_updater_invalid_response",
        "message": "bad response",
    }
    stock_apply = MagicMock(name="stock_apply")
    with patch.object(updates, "managed_update", return_value=payload), \
         patch.object(updates, "_apply_update_inner", stock_apply), \
         patch.object(updates, "_run_git") as stock_git:
        result = updates.apply_update("webui")

    assert result == payload
    stock_apply.assert_not_called()
    stock_git.assert_not_called()


def test_managed_updater_timeout_fails_closed(tmp_path):
    updater = tmp_path / "updater"
    updater.write_text("stub", encoding="utf-8")
    with patch.object(adapter, "UPDATER", updater), patch.object(
        adapter.subprocess, "run", side_effect=adapter.subprocess.TimeoutExpired("updater", 60)
    ):
        result = adapter.managed_check("agent")

    assert result["ok"] is False
    assert result["reason"] == "managed_updater_timeout"


def test_managed_updater_os_error_fails_closed(tmp_path):
    updater = tmp_path / "updater"
    updater.write_text("stub", encoding="utf-8")
    with patch.object(adapter, "UPDATER", updater), patch.object(
        adapter.subprocess, "run", side_effect=OSError("exec failed")
    ):
        result = adapter.managed_check("webui")

    assert result["ok"] is False
    assert result["reason"] == "managed_updater_execution_failed"


def test_managed_updater_nonzero_json_cannot_report_success(tmp_path):
    updater = tmp_path / "updater"
    updater.write_text("stub", encoding="utf-8")
    proc = MagicMock(stdout='{"ok": true, "message": "updated"}', stderr="failed", returncode=7)
    with patch.object(adapter, "UPDATER", updater), patch.object(
        adapter.subprocess, "run", return_value=proc
    ):
        result = adapter.managed_update("agent")

    assert result["ok"] is False
    assert result["reason"] == "managed_updater_nonzero_exit"


def test_managed_updater_success_requires_results_contract(tmp_path):
    updater = tmp_path / "updater"
    updater.write_text("stub", encoding="utf-8")
    proc = MagicMock(stdout='{"ok": true}', stderr="", returncode=0)
    with patch.object(adapter, "UPDATER", updater), patch.object(
        adapter.subprocess, "run", return_value=proc
    ):
        result = adapter.managed_update("agent")

    assert result["ok"] is False
    assert result["reason"] == "managed_updater_invalid_response"


def test_managed_updater_refuses_wrong_target_result(tmp_path):
    updater = tmp_path / "updater"
    updater.write_text("stub", encoding="utf-8")
    proc = MagicMock(
        stdout='{"ok": true, "results": [{"ok": true, "target": "webui"}]}',
        stderr="",
        returncode=0,
    )
    with patch.object(adapter, "UPDATER", updater), patch.object(
        adapter.subprocess, "run", return_value=proc
    ):
        result = adapter.managed_update("agent")

    assert result["ok"] is False
    assert result["reason"] == "managed_updater_target_mismatch"


def test_managed_check_accepts_authoritative_single_target_contract(tmp_path):
    updater = tmp_path / "updater"
    updater.write_text("stub", encoding="utf-8")
    payload = {
        "ok": True,
        "results": [{
            "target": "agent",
            "head": "abc",
            "upstream_ref": "upstream/main",
            "local_only": 1,
            "upstream_only": 2,
            "upstream_commits": ["123 change"],
            "up_to_date": False,
        }],
    }
    proc = MagicMock(stdout=__import__("json").dumps(payload), stderr="", returncode=0)
    with patch.object(adapter, "UPDATER", updater), patch.object(
        adapter.subprocess, "run", return_value=proc
    ):
        result = adapter.managed_check("agent")

    assert result == payload


def test_managed_check_rejects_inconsistent_up_to_date_invariant(tmp_path):
    updater = tmp_path / "updater"
    updater.write_text("stub", encoding="utf-8")
    payload = {
        "ok": True,
        "results": [{
            "target": "agent", "head": "abc", "upstream_ref": "upstream/main",
            "local_only": 1, "upstream_only": 2,
            "upstream_commits": ["123 change"], "up_to_date": True,
        }],
    }
    proc = MagicMock(stdout=__import__("json").dumps(payload), stderr="", returncode=0)
    with patch.object(adapter, "UPDATER", updater), patch.object(
        adapter.subprocess, "run", return_value=proc
    ):
        result = adapter.managed_check("agent")
    assert result["ok"] is False
    assert result["reason"] == "managed_updater_invalid_response"


def test_managed_check_rejects_unknown_fields(tmp_path):
    updater = tmp_path / "updater"
    updater.write_text("stub", encoding="utf-8")
    payload = {
        "ok": True,
        "unexpected": "must not flow through",
        "results": [{
            "target": "agent", "head": "abc", "upstream_ref": "upstream/main",
            "local_only": 1, "upstream_only": 0,
            "upstream_commits": [], "up_to_date": True,
        }],
    }
    proc = MagicMock(stdout=__import__("json").dumps(payload), stderr="", returncode=0)
    with patch.object(adapter, "UPDATER", updater), patch.object(
        adapter.subprocess, "run", return_value=proc
    ):
        result = adapter.managed_check("agent")
    assert result["ok"] is False
    assert result["reason"] == "managed_updater_invalid_response"


def test_managed_update_failure_message_is_normalized(tmp_path):
    updater = tmp_path / "updater"
    updater.write_text("stub", encoding="utf-8")
    payload = {
        "ok": False,
        "results": [{
            "ok": False, "target": "agent", "candidate_id": "1234567890-abcd1234",
            "original_head": "a" * 40, "new_head": "b" * 40,
            "recovery_tag": "recovery/agent-test",
            "bundle": "/private/path/SECRET_TOKEN.bundle",
            "restart_required": True,
            "message": "restart failed with SECRET_TOKEN",
        }],
    }
    proc = MagicMock(stdout=__import__("json").dumps(payload), stderr="", returncode=0)
    with patch.object(adapter, "UPDATER", updater), patch.object(
        adapter.subprocess, "run", return_value=proc
    ):
        result = adapter.managed_update("agent")
    assert result == {
        "ok": False,
        "target": "agent",
        "reason": "managed_update_restart_required",
        "message": "Agent code was updated, but restart or health verification did not complete.",
    }
    assert "SECRET_TOKEN" not in __import__("json").dumps(result)
