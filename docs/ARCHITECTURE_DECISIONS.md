# Architecture decisions

**Status:** F0 freeze proposal, 2026-10-23. Coordinator acknowledgements are pending.  
**Scope:** P0 architecture for the PeriShift/ZeroSpoil prototype. See [the implementation baseline](MASTER_IMPLEMENTATION_PLAN.md) and [the API contract](API_CONTRACT.md).

## ADR-001: Modular monolith

Use one Python/FastAPI backend codebase with separated API, domain, service, repository, and worker modules. Keep pure decision functions independent of I/O. Only transactional command services mutate authoritative stock, demand, leases, custody, and lifecycle state. Do not split into microservices for P0.

## ADR-002: Frozen stack

- **Backend:** Python 3.12+, FastAPI, Pydantic v2, Uvicorn.
- **Persistence:** PostgreSQL 16+, SQLAlchemy 2, Alembic, and **synchronous SQLAlchemy with Psycopg 3**. Use synchronous database sessions and synchronous FastAPI handlers consistently; do not mix async and sync database access patterns.
- **Frontend:** React, TypeScript, and Vite, with Tailwind CSS and shadcn/ui as the shared component approach. Keep the frontend a client of the versioned API.
- **Map:** Leaflet as the sole P0 map library, using a configured tile source and a table/list fallback. Do not add MapLibre or a live routing dependency in parallel.
- **Routing:** deterministic, injectable simulation-safe routing provider. Label travel times as estimates; no paid/live routing service is required.
- **Realtime:** Server-Sent Events for P0. Persist events in PostgreSQL in the same transaction as the state change. The API stream reads/replays committed events by cursor from PostgreSQL so a separate worker process is supported. External side-effect outbox delivery is optional and is not the event-history authority.
- **Background work:** PostgreSQL-backed due-work records and one worker process for the prototype. Workers revalidate current database state and scenario time before acting. Redis or another broker is out of P0.
- **Deployment:** Docker Compose with PostgreSQL, API, worker, and Vite web service. A reliable local demo is the baseline; hosting is optional.

## ADR-003: Identity and authorization

Use seeded demo identities for the prototype. Authenticate API calls with signed bearer JWTs; the token identifies the actor, while the backend loads the actor's role from the authoritative user record for every authorization decision. Demo-token issuance and judge/chaos operations are available only when `DEMO_MODE=true`; signing secrets come from environment configuration, never source control. Enforce role checks in backend dependencies/services, not only in hidden or disabled UI controls. No external identity provider is required for P0.

## ADR-004: P0 scope and authority

- PostgreSQL is authoritative for physical quantities, item-specific demand, assignments, leases, custody, audit events, and scenario-owned records.
- Use transactions, row locks on affected aggregates, versions, unique constraints/indexes, idempotency, and bounded whole-transaction retry for the specified serialization/deadlock cases. Use a consistent documented lock order; never serialize unrelated deliveries behind one global lock.
- The server-owned `Clock` provides UTC-aware time. Scenario time is isolated and deterministic; wall-clock workers do not execute simulated jobs.
- A lot's physical custody/assignment is distinct from alert flags such as `AT_RISK`, `SUSPECT`, or `LOCATION_STALE`. Carried goods keep rider occupancy until physical resolution.
- P0 is single pickup to one destination, with truthful full-quantity delivery accounting. Do not claim a fallback accepted a partial lot unless an explicit multi-stop/partial accounting feature is approved and implemented.
- Pure planners return proposals and reason codes. They never write data, authorize unsafe aid, or make an LLM the safety authority.

## ADR-005: Shared contract ownership

F2 is the sole owner of shared DTOs, status/reason enums, event envelope, OpenAPI, and generated frontend types. F3/Group A owns schema/migrations and persisted constraints. Group C owns lifecycle transitions and transaction-writing services. Group B owns pure decisions. Group D consumes the frozen client types. The integration coordinator owns `main.py`, Compose, CI, and cross-group contract decisions. A contract change requires coordinator review and synchronized updates to its dependent issues before implementation proceeds.

## Alternatives explicitly deferred

Microservices, Kafka, Kubernetes, Redis, mandatory PostGIS, CP-SAT, ML/LLM safety decisions, live routing APIs, multiple API replicas, multi-stop/relay workflows, and multi-trip optimization are not P0 requirements. Add them only after the P0 behavior works and a measured requirement justifies the complexity.
