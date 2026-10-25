.PHONY: up down test demo-reset reconcile

up:
	docker compose up --build -d

down:
	docker compose down

test:
	cd backend && python -m pytest

demo-reset:
	docker compose exec api python -m app.demo.reset

reconcile:
	cd backend && python -m app.domain.reconciliation $(if $(SCENARIO_ID),--scenario-id $(SCENARIO_ID),)
