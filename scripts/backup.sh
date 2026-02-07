#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# Bibliotheca Oratorii Sacratissimorum Cordium — Daily Backup
# Copies the database and master files to storage/backups/
# Retains the last 7 daily backups.
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

# Back up the database
if [ -f "$DB_FILE" ]; then
    cp "$DB_FILE" "$DAILY_BACKUP_DIR/bibliotheca_${TIMESTAMP}.db"
    echo "[backup] Database copied to $DAILY_BACKUP_DIR/bibliotheca_${TIMESTAMP}.db"
else
    echo "[backup] WARNING: Database file not found at $DB_FILE"
fi

# Back up master files
if [ -d "$MASTERS_DIR" ]; then
    cp -r "$MASTERS_DIR" "$DAILY_BACKUP_DIR/masters"
    echo "[backup] Masters directory copied to $DAILY_BACKUP_DIR/masters/"
else
    echo "[backup] WARNING: Masters directory not found at $MASTERS_DIR"
fi

echo "[backup] Backup complete: $DAILY_BACKUP_DIR"

# Prune old backups — keep only the 7 most recent
BACKUP_COUNT=$(ls -1d "$BACKUP_DIR"/20* 2>/dev/null | wc -l | tr -d ' ')
if [ "$BACKUP_COUNT" -gt 7 ]; then
    REMOVE_COUNT=$((BACKUP_COUNT - 7))
    ls -1d "$BACKUP_DIR"/20* | head -n "$REMOVE_COUNT" | while read -r old_backup; do
        rm -rf "$old_backup"
        echo "[backup] Removed old backup: $old_backup"
    done
fi

echo "[backup] Done. $BACKUP_COUNT backup(s) in $BACKUP_DIR."
