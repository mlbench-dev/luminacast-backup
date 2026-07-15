#!/bin/bash
# Pull + rebuild + restart
# Usage: bash infra/scripts/deploy.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo "========================================="
echo "  LUMINACAST OMNI — DEPLOY"
echo "========================================="
echo ""

# Pull latest
echo "📥 Pulling latest code..."
git pull origin main

# Build and restart
echo "🔨 Building containers..."
docker compose build --no-cache

echo "🚀 Starting services..."
docker compose up -d

# Run migrations
echo "📦 Running database migrations..."
docker compose exec -T orchestrator alembic upgrade head

# Health check
echo "🏥 Checking service health..."
sleep 10
docker compose ps

echo ""
echo "========================================="
echo "  Deployed at $(date)"
echo "========================================="
