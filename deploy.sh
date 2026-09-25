#!/usr/bin/env bash
set -euo pipefail

# deploy.sh — manual production deploy.
#
# Runs the exact steps that used to run automatically on every push to
# main via GitHub Actions (.github/workflows/ci.yml's old `deploy` job).
# That auto-deploy is gone — pushing to main no longer touches the server.
# Run this by hand on the server instead, whenever you actually want to
# deploy:
#
#   ./deploy.sh
#
# Must be run from the repo root on the server (the directory this
# script lives in), with the same user/permissions docker compose
# normally runs under there.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Deploying from $SCRIPT_DIR"

echo "==> [1/6] git pull origin main"
git pull origin main

echo "==> [2/6] docker compose build"
docker compose build

echo "==> [3/6] docker compose down --remove-orphans"
docker compose down --remove-orphans || true

echo "==> [4/6] docker compose up -d --force-recreate"
docker compose up -d --force-recreate

echo "==> [5/6] waiting for services to come up"
sleep 10

echo "==> [6/6] running migrations + admin bootstrap"
docker compose exec -T orchestrator alembic upgrade head
docker compose exec -T orchestrator python scripts/create_admin.py || true

echo "==> Deployed at $(date)"
