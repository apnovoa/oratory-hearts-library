#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# Bibliotheca Oratorii Sacratissimorum Cordium — Daily Backup
# Copies the database and master files to storage/backups/
# Retains the last 7 daily backups.
#
# Optional offsite sync: set BACKUP_REMOTE to an rsync-compatible
# destination (e.g. user@backup-host:/backups/bibliotheca) and the
# script will push each backup offsite after local verification.
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

DB_FILE="$PROJECT_DIR/bibliotheca.db"
MASTERS_DIR="$PROJECT_DIR/storage/masters"
BACKUP_DIR="$PROJECT_DIR/storage/backups"

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
DAILY_BACKUP_DIR="$BACKUP_DIR/$TIMESTAMP"

# Ensure backup directory exists
mkdir -p "$DAILY_BACKUP_DIR"

# ── Database backup ───────────────────────────────────────────────
if [ -f "$DB_FILE" ]; then
    DB_BACKUP="$DAILY_BACKUP_DIR/bibliotheca_${TIMESTAMP}.db"
    cp "$DB_FILE" "$DB_BACKUP"
    echo "[backup] Database copied to $DB_BACKUP"

    # Verify backup integrity
    if command -v sqlite3 >/dev/null 2>&1; then
        INTEGRITY=$(sqlite3 "$DB_BACKUP" "PRAGMA integrity_check;" 2>&1)
        if [ "$INTEGRITY" = "ok" ]; then
            echo "[backup] Integrity check passed."
        else
            echo "[backup] ERROR: Integrity check failed: $INTEGRITY" >&2
            exit 1
        fi
    else
        echo "[backup] WARNING: sqlite3 not found — skipping integrity check."
    fi

    # Generate checksum
    sha256sum "$DB_BACKUP" > "$DB_BACKUP.sha256"
    echo "[backup] Checksum written to $DB_BACKUP.sha256"
else
    echo "[backup] WARNING: Database file not found at $DB_FILE"
fi

# ── Master files backup ──────────────────────────────────────────
if [ -d "$MASTERS_DIR" ]; then
    cp -r "$MASTERS_DIR" "$DAILY_BACKUP_DIR/masters"
    echo "[backup] Masters directory copied to $DAILY_BACKUP_DIR/masters/"
else
    echo "[backup] WARNING: Masters directory not found at $MASTERS_DIR"
fi

echo "[backup] Backup complete: $DAILY_BACKUP_DIR"

# ── Offsite sync ──────────────────────────────────────────────────
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
if [ -n "$BACKUP_REMOTE" ]; then
    echo "[backup] Syncing to offsite: $BACKUP_REMOTE"
    if rsync -az --delete "$BACKUP_DIR/" "$BACKUP_REMOTE/"; then
        echo "[backup] Offsite sync complete."
    else
        echo "[backup] ERROR: Offsite sync failed." >&2
        # Don't exit — local backup is still valid
    fi
else
    echo "[backup] No BACKUP_REMOTE set — skipping offsite sync."
fi

# ── Prune old backups — keep only the 7 most recent ──────────────
BACKUP_COUNT=$(ls -1d "$BACKUP_DIR"/20* 2>/dev/null | wc -l | tr -d ' ')
if [ "$BACKUP_COUNT" -gt 7 ]; then
    REMOVE_COUNT=$((BACKUP_COUNT - 7))
    ls -1d "$BACKUP_DIR"/20* | head -n "$REMOVE_COUNT" | while read -r old_backup; do
        rm -rf "$old_backup"
        echo "[backup] Removed old backup: $old_backup"
    done
fi

echo "[backup] Done. $BACKUP_COUNT backup(s) in $BACKUP_DIR."
