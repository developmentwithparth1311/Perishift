# PeriShift / ZeroSpoil

A deadline-aware dispatch prototype for perishable aid. This repository is being bootstrapped from the project implementation plan; the current scaffold is not a completed logistics system.

## Stack

- API: Python 3.12, FastAPI, Pydantic
- Web: React, TypeScript, Vite
- Local data service: PostgreSQL 16 via Docker Compose

## Run locally

1. Copy `.env.example` to `.env` and adjust local credentials if needed.
2. Start the stack with `docker compose up --build`.
3. Open the web app at <http://localhost:5173> and API docs at <http://localhost:8000/docs>.

For API-only development, create a virtual environment, install `backend/requirements.txt`, then run `uvicorn app.main:app --reload` from `backend/`.

## Current implementation

The first slice establishes runnable API and UI shells plus a health endpoint. Inventory, demand, lease, custody, simulation, and recovery behavior remain to be implemented against the architecture and contracts in [the master plan](ZeroSpoil_UNIFIED_MASTER_IMPLEMENTATION_PLAN.md).
