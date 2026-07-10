# Local carry notes

This file is the source-of-truth runbook for local Hermes WebUI customizations that must survive upstream updates.

## Why this exists

This checkout intentionally tracks upstream `origin/master` **plus** a small local carry layer.

That means a plain fast-forward-only update is not always correct. The supported path is:

- inspect update state
- update through `hermes-webui-local-update`
- keep intentional local changes as commits
- document the carry layer here

## Current update model

- Upstream branch: `origin/master`
- Local working branch: `master`
- Supported updater: `~/.local/bin/hermes-webui-local-update`
- Agent updater used by WebUI: `~/.local/bin/hermes-local-update`
- Destructive escape hatch: WebUI **Force Update** / `/api/updates/force`

## Source-of-truth files when update is not working

### WebUI repo
- `api/updates.py`
  - update check/apply logic
  - applyability metadata
  - wrapper routing
  - force-update and lock-recovery behavior
- `api/gateway_restart.py`
  - post-update gateway restart behavior
  - user-vs-system restart scope
  - sudo-backed system restart path
- `api/routes.py`
  - HTTP entrypoints for `/api/updates/check`, `/api/updates/apply`, `/api/updates/force`, `/api/updates/clear_lock`
- `static/ui.js`
  - browser-side update/apply/force-update requests and user-facing error handling
- `tests/test_updates.py`
  - regression coverage for wrapper routing, lock handling, and update responses
- `tests/test_gateway_restart_helper.py`
  - regression coverage for gateway restart scope and sudo-backed restart path
- `hermes-webui-gateway-restart.sudoers`
  - exact sudoers rule required for system gateway restart from WebUI

### Hermes Agent repo
- `/home/alex/.hermes/hermes-agent/LOCAL_LIVE_CARRIES.md`
  - current runtime carry ledger for the agent `local/live` branch
- `/home/alex/.local/bin/hermes-local-update`
  - updater wrapper for the agent runtime

## Live inspection commands

### WebUI carry status
```bash
cd ~/hermes-webui
~/.local/bin/hermes-webui-local-update --check
```

### WebUI dry-run update
```bash
cd ~/hermes-webui
~/.local/bin/hermes-webui-local-update --dry-run
```

### WebUI local carry commits
```bash
cd ~/hermes-webui
git log --oneline origin/master..HEAD
```

### Agent carry status
```bash
~/.local/bin/hermes-local-update --check
```

### Agent local/live carry commits
```bash
git -C ~/.hermes/hermes-agent log --oneline main..local/live
```

## Carry policy

1. **Never rely on uncommitted local edits** for important behavior.
2. Every intentional local customization should exist as a commit.
3. Keep the carry layer small and easy to explain.
4. Prefer wrapper-based updates over manual `git pull`.
5. Use **Force Update** only when you explicitly want to discard local changes.
6. If a carry commit becomes stale after upstream changes, reapply it as a fresh commit and update this file.

## Normal update flow

### WebUI
1. Run `hermes-webui-local-update --check` if you need a preflight view.
2. Use the WebUI update button or run `hermes-webui-local-update` directly.
3. If the update succeeds but restart fails, inspect `api/gateway_restart.py` behavior and service scope.
4. Re-check carry state after the update.

### Agent
1. Use the WebUI agent update button or `hermes-local-update` directly.
2. Treat `local/live` being ahead of `main` as expected carry state, not generic divergence.
3. If the dashboard/gateway says fast-forward is impossible, verify whether the wrong updater path was used.

## When troubleshooting an update failure

Classify the failure first:

1. **Update check problem**
   - wrong branch/behind/ahead/applyability metadata
   - inspect `api/updates.py`
2. **Wrapper routing problem**
   - WebUI or Agent tried plain git update instead of wrapper
   - inspect `api/updates.py`, `api/routes.py`, `static/ui.js`
3. **Git conflict / stale carry problem**
   - wrapper rebase failed replaying a local commit
   - inspect `git log origin/master..HEAD`, this file, and any backup branch
4. **Restart-only problem**
   - repo updated but service restart failed
   - inspect `api/gateway_restart.py` and service scope
5. **Lock-file problem**
   - `.git/index.lock` or related git lock blocks update
   - inspect lock-recovery response and `api/updates.py`

## Maintenance note

Whenever the carry layer changes materially:
- update this file
- update `docs/troubleshooting.md` if the operator workflow changed
- update `README.md` / `docs/CONTRACTS.md` if the primary documentation entrypoints changed
