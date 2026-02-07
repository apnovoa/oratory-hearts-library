#!/bin/bash
set -e

SERVER="${DEPLOY_SERVER:?Set DEPLOY_SERVER env var (e.g. root@1.2.3.4)}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/oratory-lib}"
REMOTE_DIR="${DEPLOY_REMOTE_DIR:-/opt/bibliotheca}"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Adding SSH key to agent (enter passphrase once)..."
ssh-add "$KEY" 2>/dev/null || true

echo "==> Creating deploy tarball..."
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
ssh -i "$KEY" "$SERVER" "cd $REMOTE_DIR && tar -xzf /tmp/bibliotheca-deploy.tar.gz && chown -R bib:bib $REMOTE_DIR && systemctl restart bibliotheca && echo 'Service restarted' && sleep 3 && systemctl is-active bibliotheca && curl -sf http://127.0.0.1:8000/health >/dev/null && echo 'Health check passed' || echo 'WARNING: Health check failed'"

echo "==> Deploy complete!"
