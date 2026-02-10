# Bibliotheca Operational Runbook

This runbook is for operating the production service safely and recovering from common failures.

## 1. Scope and Assumptions

- Runtime: single host, SQLite, one gunicorn worker.
- Service name: `bibliotheca` (systemd).
- App root: `/opt/bibliotheca` with `current` symlink and `releases/` layout.
- Health endpoints:
  - `/ping` for basic liveness.
  - `/health` for scheduler + database readiness.

## 2. Routine Operations

### Start / Stop / Restart

```bash
sudo systemctl start bibliotheca
sudo systemctl stop bibliotheca
sudo systemctl restart bibliotheca
sudo systemctl status bibliotheca --no-pager
```

### Logs

```bash
journalctl -u bibliotheca -n 200 --no-pager
journalctl -u bibliotheca -f
```

Application logs are also written to `logs/bibliotheca.log` with rotation.

### Verify Environment Safety

Before restart/deploy, confirm:

```bash
grep '^FLASK_ENV=' /opt/bibliotheca/shared/.env
grep '^WEB_CONCURRENCY=' /opt/bibliotheca/shared/.env
```

Requirements:

- `FLASK_ENV=production`
- `WEB_CONCURRENCY` unset or `1`

## 3. Deploy and Rollback

### Deploy (from trusted workstation)

```bash
DEPLOY_SERVER=user@host ./deploy.sh
```

What this script guarantees:

- Creates a new timestamped release in `releases/`.
- Atomically swaps `current` symlink.
- Restarts service.
- Checks `/ping`.
- Automatically rolls back to prior release on failed health check.

### Manual Rollback (if needed)

```bash
cd /opt/bibliotheca/releases
ls -1dt */
```

Pick the prior known-good release, then:

```bash
sudo ln -sfn /opt/bibliotheca/releases/<release_name> /opt/bibliotheca/current
sudo systemctl restart bibliotheca
curl -sf http://127.0.0.1:8080/health
```

## 4. Health Monitoring and Triage

### Quick Check

```bash
curl -s http://127.0.0.1:8080/ping
curl -s http://127.0.0.1:8080/health
```

### `/health` Interpretation

- `status=ok`: database reachable and scheduler healthy.
- `status=degraded`: investigate immediately.
- Scheduler section includes:
  - `running`
  - `failing_jobs`
  - `failure_threshold`
  - per-job `consecutive_failures`, `last_error`, `last_run_at`

If `failing_jobs` is non-empty:

1. Inspect `journalctl -u bibliotheca -f`
2. Identify repeated job errors.
3. Correct root cause.
4. Restart service to reset in-process scheduler state if required.

## 5. Backups and Restore Verification

### Run Backup Manually

```bash
/opt/bibliotheca/current/scripts/backup.sh
```

### Verify a Backup

```bash
/opt/bibliotheca/current/scripts/restore-verify.sh /opt/bibliotheca/current/storage/backups/<timestamp>
```

### Offsite Sync

Set `BACKUP_REMOTE` in environment (or cron env), example:

```bash
BACKUP_REMOTE=user@backup-host:/backups/bibliotheca
```

### Recovery Drill (Quarterly)

1. Pick latest backup folder.
2. Run `restore-verify.sh`.
3. Record checksum, integrity status, and restore result.
4. Confirm row-count sanity check passes.

## 6. Incident Playbooks

### A) Database Unavailable / Locked

Symptoms:

- `/health` returns database error.
- Logs show SQLite lock/IO errors.

Actions:

1. Confirm single worker (`WEB_CONCURRENCY` <= 1).
2. Ensure no overlapping maintenance process touching DB.
3. Restart service.
4. If DB corruption suspected, restore from latest verified backup.

### B) Scheduler Jobs Repeatedly Failing

Symptoms:

- `/health` shows `failing_jobs`.
- Repeated scheduler exceptions in logs.

Actions:

1. Capture exact exception.
2. Fix dependency/config issue (email API key, filesystem, permissions, etc.).
3. Restart service.
4. Recheck `/health` until scheduler recovers.

### C) Email Delivery Outage

Symptoms:

- Frequent Brevo HTTP errors in logs.

Actions:

1. Verify `BREVO_API_KEY` and sender settings.
2. Check provider status and network egress.
3. Service can continue operating; lending must not be blocked by email issues.

### D) Failed Deploy

Symptoms:

- `deploy.sh` reports failed health check.

Actions:

1. Confirm auto-rollback occurred (`current` points to prior release).
2. Inspect release logs and systemd logs.
3. Fix issue and redeploy.

## 7. Change Management Checklist

Before production changes:

1. `ruff check .`
2. `pytest -q`
3. `pytest -q --cov=app --cov-fail-under=50`
4. Confirm migrations apply cleanly.
5. Confirm backup + restore verification succeeded recently.

