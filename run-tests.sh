#!/usr/bin/env bash
set -euo pipefail

# run-tests.sh — convenience wrapper around Makefile test targets
# Usage: ./run-tests.sh [unit|integration|backend|frontend|e2e|all|ci]
# Default: runs unit + frontend (quick mode)

TARGET="${1:-test}"

case "$TARGET" in
  unit)
    make test-unit
    ;;
  integration)
    make test-integration
    ;;
  backend)
    make test-backend
    ;;
  frontend)
    make test-frontend
    ;;
  e2e)
    make test-e2e
    ;;
  all)
    make test-all
    ;;
  ci)
    make ci
    ;;
  *)
    make test
    ;;
esac
