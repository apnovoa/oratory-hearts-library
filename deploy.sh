#!/bin/bash
set -e

SERVER="${DEPLOY_SERVER:?Set DEPLOY_SERVER env var (e.g. user@host)}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/oratory-lib}"
REMOTE_DIR="${DEPLOY_REMOTE_DIR:-/opt/bibliotheca}"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Adding SSH key to agent (enter passphrase once)..."
ssh-add "$KEY" 2>/dev/null || true

echo "==> Verifying remote production config..."
ssh -i "$KEY" "$SERVER" "grep -q '^FLASK_ENV=production' $REMOTE_DIR/.env" || { echo "FATAL: FLASK_ENV is not set to production in remote $REMOTE_DIR/.env"; exit 1; }

GIT_HASH=$(git rev-parse --short HEAD)
echo "==> Creating deploy tarball (${GIT_HASH})..."
cd "$PROJECT_DIR"
tar -czf /tmp/bibliotheca-deploy.tar.gz \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='storage' \
    --exclude='*.db' \
    --exclude='.env' \
    --exclude='deploy.sh' \
    .

echo "==> Uploading to server..."
scp -i "$KEY" /tmp/bibliotheca-deploy.tar.gz "$SERVER:/tmp/"

echo "==> Deploying and restarting..."
ssh -i "$KEY" "$SERVER" "cd $REMOTE_DIR && tar -xzf /tmp/bibliotheca-deploy.tar.gz && sed -i 's/bibliotheca-v[0-9a-zA-Z_-]*/bibliotheca-${GIT_HASH}/' app/static/sw.js && chown -R bib:bib $REMOTE_DIR && systemctl restart bibliotheca && echo 'Service restarted' && sleep 3 && systemctl is-active bibliotheca && curl -sf http://127.0.0.1:8080/ping >/dev/null && echo 'Health check passed' || echo 'WARNING: Health check failed'"

echo "==> Deploy complete!"
