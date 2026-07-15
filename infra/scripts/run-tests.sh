#!/bin/bash
# Run full test suite — use after each coding session
# Usage: bash infra/scripts/run-tests.sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo ""
echo "========================================="
echo "  LUMINACAST OMNI — TEST RUNNER"
echo "========================================="
echo ""

# Start test databases
echo "📦 Starting test databases..."
docker compose -f docker-compose.test.yml up -d 2>/dev/null
sleep 3

PASS=0
FAIL=0

run_test() {
    local name="$1"
    local cmd="$2"
    echo ""
    echo "--- $name ---"
    if eval "$cmd" 2>&1; then
        echo "✅ $name PASSED"
        PASS=$((PASS + 1))
    else
        echo "❌ $name FAILED"
        FAIL=$((FAIL + 1))
    fi
}

# Backend unit tests
run_test "Backend Unit Tests" \
    "cd backend/orchestrator && python -m pytest tests/unit/ -v --tb=short 2>&1"

# Backend integration tests
run_test "Backend Integration Tests" \
    "cd backend/orchestrator && python -m pytest tests/integration/ -v --tb=short 2>&1"

# Frontend unit tests
run_test "Frontend Unit Tests" \
    "cd frontend/companion-app && npx vitest run 2>&1"

# Cleanup
echo ""
echo "📦 Stopping test databases..."
docker compose -f docker-compose.test.yml down 2>/dev/null

# Summary
echo ""
echo "========================================="
echo "  RESULTS: $PASS passed, $FAIL failed"
echo "========================================="

if [ $FAIL -gt 0 ]; then
    echo "  ❌ Some tests failed — fix before committing"
    exit 1
else
    echo "  ✅ All tests passed — safe to commit"
    exit 0
fi
