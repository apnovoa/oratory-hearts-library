#!/bin/bash
set -e

SERVER="root@104.131.189.253"
KEY="$HOME/.ssh/oratory-lib"
REMOTE_DIR="/opt/bibliotheca"
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
ssh -i "$KEY" "$SERVER" "cd $REMOTE_DIR && tar -xzf /tmp/bibliotheca-deploy.tar.gz && chown -R bib:bib $REMOTE_DIR && systemctl restart bibliotheca && echo 'Service restarted' && systemctl is-active bibliotheca"

echo "==> Deploy complete!"
