.PHONY: test test-unit test-integration test-frontend test-e2e test-all setup-test-db

# === Setup ===
setup-test-db:
	docker compose -f docker-compose.test.yml up -d
	sleep 3
	@echo "Test databases ready"

teardown-test-db:
	docker compose -f docker-compose.test.yml down

# === Backend ===
test-unit:
	cd backend/orchestrator && python -m pytest tests/unit/ -v --tb=short

test-integration: setup-test-db
	cd backend/orchestrator && python -m pytest tests/integration/ -v --tb=short

test-backend: test-unit test-integration

# === Frontend ===
test-frontend:
	cd frontend/companion-app && npx vitest run

# === E2E ===
test-e2e-onboard:
	cd frontend/companion-app && npx playwright test tests/e2e/onboarding.spec.ts

test-e2e-cast:
	cd frontend/companion-app && npx playwright test tests/e2e/cast-builder.spec.ts

test-e2e-live:
	cd frontend/companion-app && npx playwright test tests/e2e/live-control.spec.ts

test-e2e-analytics:
	cd frontend/companion-app && npx playwright test tests/e2e/analytics.spec.ts

test-e2e: test-e2e-onboard test-e2e-cast test-e2e-live test-e2e-analytics

# === All ===
test-all: test-backend test-frontend test-e2e

# === Quick (unit only — for rapid iteration) ===
test: test-unit test-frontend

# === CI (run on server after deploy) ===
ci: setup-test-db test-backend test-frontend teardown-test-db
