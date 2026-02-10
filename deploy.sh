#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Bibliotheca — Atomic Deploy with Rollback
#
# Uses a symlink-swap pattern so a failed deploy never leaves the
# application in a partially-updated state.
#
# Server directory layout:
#   /opt/bibliotheca/
#     current  -> releases/<release>/    (symlink, swapped atomically)
#     releases/                          (timestamped release directories)
#     shared/                            (persistent: .env, storage/, bibliotheca.db)
#
# One-time migration from the old flat layout:
#   OLD=/opt/bibliotheca  NEW=/opt/bibliotheca-new
#   mkdir -p $NEW/{releases,shared}
#   mv $OLD/.env $NEW/shared/
#   mv $OLD/storage $NEW/shared/
#   mv $OLD/bibliotheca.db $NEW/shared/
#   cp -a $OLD $NEW/releases/initial
#   ln -sfn $NEW/releases/initial $NEW/current
#   mv $OLD $OLD.bak && mv $NEW $OLD
#   # Update systemd WorkingDirectory to /opt/bibliotheca/current
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

SERVER="${DEPLOY_SERVER:?Set DEPLOY_SERVER env var (e.g. user@host)}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/oratory-lib}"
REMOTE_DIR="${DEPLOY_REMOTE_DIR:-/opt/bibliotheca}"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
KEEP_RELEASES="${DEPLOY_KEEP_RELEASES:-5}"

echo "==> Adding SSH key to agent (enter passphrase once)..."
ssh-add "$KEY" 2>/dev/null || true

echo "==> Verifying remote production config..."
ssh -i "$KEY" "$SERVER" \
    "grep -q '^FLASK_ENV=production' $REMOTE_DIR/shared/.env" \
    || { echo "FATAL: FLASK_ENV not set to production in $REMOTE_DIR/shared/.env"; exit 1; }

GIT_HASH=$(git rev-parse --short HEAD)
RELEASE_NAME="$(date +%Y%m%d_%H%M%S)_${GIT_HASH}"

echo "==> Creating deploy tarball (${GIT_HASH})..."
cd "$PROJECT_DIR"
TARBALL=$(mktemp /tmp/bibliotheca-deploy.XXXXXX.tar.gz)
tar -czf "$TARBALL" \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='storage' \
    --exclude='*.db' \
    --exclude='.env' \
    --exclude='deploy.sh' \
    .

echo "==> Uploading to server..."
scp -i "$KEY" "$TARBALL" "$SERVER:/tmp/bibliotheca-deploy.tar.gz"
rm -f "$TARBALL"

echo "==> Deploying release ${RELEASE_NAME}..."
ssh -i "$KEY" "$SERVER" bash <<REMOTE_SCRIPT
set -euo pipefail

RELEASES_DIR="$REMOTE_DIR/releases"
RELEASE_DIR="\$RELEASES_DIR/$RELEASE_NAME"
CURRENT_LINK="$REMOTE_DIR/current"
SHARED_DIR="$REMOTE_DIR/shared"

# Extract into a fresh release directory
mkdir -p "\$RELEASE_DIR"
tar -xzf /tmp/bibliotheca-deploy.tar.gz -C "\$RELEASE_DIR"
rm -f /tmp/bibliotheca-deploy.tar.gz

# Link shared resources into the release
ln -sfn "\$SHARED_DIR/.env"           "\$RELEASE_DIR/.env"
ln -sfn "\$SHARED_DIR/storage"        "\$RELEASE_DIR/storage"
ln -sfn "\$SHARED_DIR/bibliotheca.db" "\$RELEASE_DIR/bibliotheca.db"

# Bump service-worker cache version
sed -i 's/bibliotheca-v[0-9a-zA-Z_-]*/bibliotheca-${GIT_HASH}/' "\$RELEASE_DIR/app/static/sw.js"

# Set ownership
chown -R bib:bib "\$RELEASE_DIR"

# Save previous release path for rollback
PREVIOUS=\$(readlink -f "\$CURRENT_LINK" 2>/dev/null || echo "")

# Atomic symlink swap
ln -sfn "\$RELEASE_DIR" "\${CURRENT_LINK}.tmp"
mv -T "\${CURRENT_LINK}.tmp" "\$CURRENT_LINK"

# Restart and health-check
systemctl restart bibliotheca
sleep 3

if systemctl is-active --quiet bibliotheca \
   && curl -sf http://127.0.0.1:8080/health \
      | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("status") == "ok" else 1)'; then
    echo "Health check passed."
else
    echo "FATAL: Health check failed — rolling back..."
    if [ -n "\$PREVIOUS" ] && [ -d "\$PREVIOUS" ]; then
        ln -sfn "\$PREVIOUS" "\${CURRENT_LINK}.tmp"
        mv -T "\${CURRENT_LINK}.tmp" "\$CURRENT_LINK"
        systemctl restart bibliotheca
        echo "Rolled back to \$PREVIOUS"
    fi
    # Remove the failed release
    rm -rf "\$RELEASE_DIR"
    exit 1
fi

# Prune old releases — keep the $KEEP_RELEASES most recent
cd "\$RELEASES_DIR"
CURRENT_TARGET=\$(readlink -f "\$CURRENT_LINK")
ls -1dt */ 2>/dev/null | tail -n +\$(($KEEP_RELEASES + 1)) | while read -r old; do
    OLD_PATH=\$(readlink -f "\$old")
    [ "\$OLD_PATH" = "\$CURRENT_TARGET" ] && continue
    rm -rf "\$old"
    echo "Pruned old release: \$old"
done
REMOTE_SCRIPT

echo "==> Deploy complete (${RELEASE_NAME})!"
