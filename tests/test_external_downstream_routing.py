import json
from unittest.mock import patch

import api.updates as updates


def make_policy(tmp_path, target="agent"):
    repo = tmp_path / target
    (repo / ".git").mkdir(parents=True)
    updater = tmp_path / "hermes-downstream-update"
    updater.write_text("#!/usr/bin/env python3\nraise SystemExit(0)\n")
    updater.chmod(0o755)
    policy = tmp_path / "update-targets.json"
    policy.write_text(json.dumps({
        "schema_version": 1,
        "targets": {
            target: {
                "schema_version": 1,
                "target_name": target,
                "mode": "external",
                "canonical_repo_path": str(repo),
                "updater_executable_path": str(updater),
            }
        },
    }))
    policy.chmod(0o600)
    return repo, updater, policy


def test_external_check_routes_only_to_schema_valid_updater_payload(tmp_path):
    repo, _updater, policy = make_policy(tmp_path, "agent")
    payload = {
        "schema_version": 1,
        "updater_version": "0.2.0",
        "target": "agent",
        "state": None,
        "branch": "local/live",
        "head": "a" * 40,
        "dirty": False,
        "can_start": True,
        "failure_phase": None,
        "ahead_behind": {"ahead": 24, "behind": 361},
        "recovery_refs": {},
    }
    with patch.object(updates, "_MAINTENANCE_TARGET_PATHS", {"agent": repo}), \
         patch.object(updates, "_UPDATE_TARGET_POLICY_PATH", policy), \
         patch.object(updates, "_run_managed_updater", return_value=(payload, 0)) as run_updater, \
         patch.object(updates, "_run_git", side_effect=AssertionError("stock git must not run")):
        result = updates._maintenance_check_info("agent", repo)

    run_updater.assert_called_once_with("agent", "check")
    assert result["managed_external"] is True
    assert result["can_apply"] is True
    assert result["behind"] == 361
    assert result["apply_reason"] == "managed_external_update"


def test_external_apply_uses_start_and_never_stock_or_force_paths(tmp_path):
    repo, _updater, policy = make_policy(tmp_path, "webui")
    payload = {
        "schema_version": 1,
        "updater_version": "0.2.0",
        "target": "webui",
        "transaction_id": "txn-1",
        "state": "restart_required",
        "failure_phase": None,
        "restart_state": "restart_required",
        "tested_candidate_oid": "b" * 40,
        "published_oid": "b" * 40,
        "recovery_refs": {"local": "refs/hermes-updater/recovery/txn-1/original"},
    }
    with patch.object(updates, "_MAINTENANCE_TARGET_PATHS", {"webui": repo}), \
         patch.object(updates, "_UPDATE_TARGET_POLICY_PATH", policy), \
         patch.object(updates, "REPO_ROOT", repo), \
         patch.object(updates, "_run_managed_updater", return_value=(payload, 0)) as run_updater, \
         patch.object(updates, "_run_git", side_effect=AssertionError("stock git must not run")), \
         patch.object(updates, "_schedule_managed_resume_after_restart") as schedule_resume, \
         patch.object(updates, "_schedule_restart") as schedule_restart:
        result = updates.apply_update("webui")

    run_updater.assert_called_once_with("webui", "start")
    schedule_resume.assert_called_once_with("webui", "txn-1", str(_updater))
    schedule_restart.assert_called_once()
    assert result["ok"] is True
    assert result["transaction_id"] == "txn-1"
    assert result["state"] == "restart_required"
    assert result["restart_scheduled"] is True


def test_current_webui_marker_uses_configured_port_and_loopback_safe_host(monkeypatch):
    payload = json.dumps({"status": "ok", "server_started_at": 456.75}).encode()
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return payload

    monkeypatch.setattr(updates.api_config, "HOST", "::")
    monkeypatch.setattr(updates.api_config, "PORT", 43124)
    monkeypatch.setattr(
        updates.urllib.request,
        "urlopen",
        lambda url, **kwargs: seen.append((url, kwargs)) or Response(),
    )

    assert updates._current_webui_server_started_at() == 456.75
    assert seen == [("http://127.0.0.1:43124/health", {"timeout": 3})]


def test_agent_restart_is_health_completed_before_success(tmp_path):
    repo, _updater, policy = make_policy(tmp_path, "agent")
    started = {
        "schema_version": 1,
        "target": "agent",
        "transaction_id": "txn-agent",
        "state": "restart_required",
        "failure_phase": None,
    }
    completed = {
        "schema_version": 1,
        "target": "agent",
        "transaction_id": "txn-agent",
        "state": "completed",
        "failure_phase": None,
        "restart_state": "healthy",
    }
    with patch.object(updates, "_MAINTENANCE_TARGET_PATHS", {"agent": repo}), \
         patch.object(updates, "_UPDATE_TARGET_POLICY_PATH", policy), \
         patch.object(updates, "_AGENT_DIR", repo), \
         patch.object(updates, "_run_managed_updater", return_value=(started, 0)), \
         patch.object(updates, "_ensure_gateway_restart_for_agent_update", return_value=(True, {"status": "completed"})), \
         patch.object(updates, "_run_managed_resume", return_value=(completed, 0)) as resume, \
         patch.object(updates, "_schedule_restart") as schedule_restart:
        result = updates.apply_update("agent")

    resume.assert_called_once_with("agent", "txn-agent", str(_updater))
    schedule_restart.assert_not_called()
    assert result["ok"] is True
    assert result["state"] == "completed"
    assert result["restart_state"] == "healthy"


def test_missing_or_invalid_policy_refuses_without_any_fallback(tmp_path):
    repo = tmp_path / "agent"
    (repo / ".git").mkdir(parents=True)
    missing = tmp_path / "missing.json"
    with patch.object(updates, "_MAINTENANCE_TARGET_PATHS", {"agent": repo}), \
         patch.object(updates, "_UPDATE_TARGET_POLICY_PATH", missing), \
         patch.object(updates, "_AGENT_DIR", repo), \
         patch.object(updates, "_run_git", side_effect=AssertionError("stock git must not run")), \
         patch.object(updates, "_run_managed_updater", side_effect=AssertionError("updater must not run")):
        checked = updates._maintenance_check_info("agent", repo)
        applied = updates.apply_update("agent")
        forced = updates.apply_force_update("agent")
        cleared = updates.apply_clear_lock("agent")

    for result in (checked, applied, forced, cleared):
        assert result["can_apply"] is False
        assert result["reason"] == "managed_policy_unavailable"


def test_missing_updater_and_updater_failure_are_fail_closed(tmp_path):
    repo, updater, policy = make_policy(tmp_path, "agent")
    updater.unlink()
    with patch.object(updates, "_MAINTENANCE_TARGET_PATHS", {"agent": repo}), \
         patch.object(updates, "_UPDATE_TARGET_POLICY_PATH", policy), \
         patch.object(updates, "_AGENT_DIR", repo), \
         patch.object(updates, "_run_git", side_effect=AssertionError("stock git must not run")):
        missing = updates.apply_update("agent")
    assert missing["reason"] == "managed_updater_unavailable"

    updater.write_text("#!/usr/bin/env python3\nraise SystemExit(1)\n")
    updater.chmod(0o755)
    failure = {"schema_version": 1, "updater_version": "0.2.0", "state": "failed", "failure_phase": "probe_failure", "message": "probe failed"}
    with patch.object(updates, "_MAINTENANCE_TARGET_PATHS", {"agent": repo}), \
         patch.object(updates, "_UPDATE_TARGET_POLICY_PATH", policy), \
         patch.object(updates, "_AGENT_DIR", repo), \
         patch.object(updates, "_run_managed_updater", return_value=(failure, 20)), \
         patch.object(updates, "_run_git", side_effect=AssertionError("stock git must not run")):
        result = updates.apply_update("agent")
    assert result["ok"] is False
    assert result["reason"] == "probe_failure"


def test_force_and_clear_lock_remain_unavailable_for_valid_external_targets(tmp_path):
    repo, _updater, policy = make_policy(tmp_path, "webui")
    with patch.object(updates, "_MAINTENANCE_TARGET_PATHS", {"webui": repo}), \
         patch.object(updates, "_UPDATE_TARGET_POLICY_PATH", policy), \
         patch.object(updates, "REPO_ROOT", repo), \
         patch.object(updates, "_run_git", side_effect=AssertionError("stock git must not run")), \
         patch.object(updates, "_run_managed_updater", side_effect=AssertionError("updater must not run")):
        forced = updates.apply_force_update("webui")
        cleared = updates.apply_clear_lock("webui")

    assert forced["reason"] == "managed_force_update_unavailable"
    assert cleared["reason"] == "managed_clear_lock_unavailable"
    assert forced["can_apply"] is False and cleared["can_apply"] is False
