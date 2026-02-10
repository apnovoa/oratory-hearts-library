#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# Bibliotheca — Backup Restore Verification
# Validates that a backup directory produced by scripts/backup.sh
# can actually be restored.
#
# Usage:
#   scripts/restore-verify.sh <backup-directory>
#
# Example:
#   scripts/restore-verify.sh storage/backups/2025-06-01_020000
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

umask 077

# ── Helpers ──────────────────────────────────────────────────────
log()  { echo "[verify] $*"; }
fail() { echo "[verify] FAIL: $*" >&2; EXIT_CODE=1; }

EXIT_CODE=0
TMPFILES=()

cleanup() {
    for f in "${TMPFILES[@]}"; do
        rm -f "$f"
    done
}
trap cleanup EXIT

# ── Usage ────────────────────────────────────────────────────────
if [ $# -lt 1 ]; then
    echo "Usage: $(basename "$0") <backup-directory>"
    echo ""
    echo "Verifies that a backup produced by scripts/backup.sh can be restored."
    echo ""
    echo "Example:"
    echo "  $(basename "$0") storage/backups/2025-06-01_020000"
    exit 1
fi

BACKUP_DIR="$1"

if [ ! -d "$BACKUP_DIR" ]; then
    echo "[verify] ERROR: Not a directory: $BACKUP_DIR" >&2
    exit 1
fi

# ── Locate the database backup ──────────────────────────────────
DB_FILE=$(find "$BACKUP_DIR" -maxdepth 1 -name 'bibliotheca_*.db' ! -name '*.sha256' | head -n 1)

if [ -z "$DB_FILE" ]; then
    echo "[verify] ERROR: No bibliotheca_*.db found in $BACKUP_DIR" >&2
    exit 1
fi

CHECKSUM_FILE="${DB_FILE}.sha256"

if [ ! -f "$CHECKSUM_FILE" ]; then
    echo "[verify] ERROR: Checksum file not found: $CHECKSUM_FILE" >&2
    exit 1
fi

log "Database file: $DB_FILE"
log "Checksum file: $CHECKSUM_FILE"

# ── 1. Verify checksum ──────────────────────────────────────────
log "Verifying SHA-256 checksum..."
if (cd "$(dirname "$CHECKSUM_FILE")" && sha256sum -c "$(basename "$CHECKSUM_FILE")" > /dev/null 2>&1); then
    log "Checksum OK."
else
    fail "Checksum verification failed."
fi

# ── 2. PRAGMA integrity_check ───────────────────────────────────
log "Running PRAGMA integrity_check..."
INTEGRITY=$(sqlite3 "$DB_FILE" "PRAGMA integrity_check;" 2>&1)
if [ "$INTEGRITY" = "ok" ]; then
    log "Integrity check passed."
else
    fail "Integrity check failed: $INTEGRITY"
fi

# ── 3. Trial restore to temp file ───────────────────────────────
log "Testing restore to temporary file..."
TMPDB=$(mktemp /tmp/bibliotheca_verify_XXXXXX.db)
TMPFILES+=("$TMPDB")

if sqlite3 "$TMPDB" ".restore '$DB_FILE'" 2>/dev/null; then
    log "Restore to temp file succeeded."
else
    fail "Restore to temp file failed."
fi

# ── 4. Row-count sanity check ───────────────────────────────────
log "Running row-count sanity check..."
USER_COUNT=$(sqlite3 "$TMPDB" "SELECT COUNT(*) FROM users;" 2>/dev/null || echo "0")
if [ "$USER_COUNT" -gt 0 ] 2>/dev/null; then
    log "Sanity check passed ($USER_COUNT users found)."
else
    fail "Sanity check failed: users table is empty or unreadable."
fi

# ── Result ───────────────────────────────────────────────────────
echo ""
if [ "$EXIT_CODE" -eq 0 ]; then
    log "ALL CHECKS PASSED for $BACKUP_DIR"
else
    log "SOME CHECKS FAILED — review output above."
fi

exit "$EXIT_CODE"
