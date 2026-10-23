.PHONY: up down test demo-reset

up:
	docker compose up --build -d

down:
	docker compose down

test:
	cd backend && python -m pytest

demo-reset:
	docker compose exec api python -m app.demo.reset
