# Backend implementation progress

**Updated:** 2026-10-23  
**Branch:** `codex/backend-foundation`  
**Scope:** Backend, persistence, decision engines, simulation, and backend integration. Group D frontend implementation is excluded by the user-provided backend directive.

## Commit and verification cadence

For code changes, commit and push a coherent increment after about **4–5 functions**, sooner when an atomic unit is ready. Do not accumulate a large issue into one commit or create artificial commits. Include targeted tests where practical. Documentation/configuration-only checkpoints may use smaller commits. Preserve unrelated files, including the untracked `.opencode/` directory.

## Current checkpoint

- **F1 #2 — PARTIAL.** Synchronous SQLAlchemy 2.0/Psycopg runtime, DB readiness route, separate idle worker process, backend packaging, Compose healthcheck, and Make targets are present. `docker compose config --quiet` succeeded. The unit-level health contract passed, but the Docker daemon is not running, so clean Compose startup and PostgreSQL readiness have not been verified. `make demo-reset` is wired for the later F4/C5 reset command and is not usable yet.
- **F2 #3 — PARTIAL.** Shared actor/item/state/risk/event/error enums and Pydantic request/snapshot DTOs exist. DTO validation covers UTC timestamps, canonical item units, batch five-bucket conservation, demand remaining quantity, and event names. Tests passed. Component registration/export for the frontend handoff OpenAPI artifact remains to be completed; no generated TypeScript files are maintained in this backend scope.
- **F0 #1 — PARTIAL.** F0 scope, architecture and contract baselines are committed. Acceptance still requires four coordinator acknowledgements of ownership and frozen contract names; none have been recorded in the repository.
- **Test checkpoint:** `backend/.venv-py313/Scripts/python.exe -m pytest -vv` — **2 passed** (health and contract DTO tests). `docker compose config --quiet` — **passed**. Docker image build/runtime — **blocked by unavailable Docker Desktop Linux engine**. An earlier test collection attempt with SQLAlchemy 2.1 was blocked by Windows Application Control on its native extension; the project now constrains SQLAlchemy to `>=2.0,<2.1` and the two tests pass on 2.0.55.

## Backend issue matrix

Status meanings: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `PARTIAL`, `VERIFIED`. `VERIFIED` requires the issue's relevant acceptance evidence, not merely code presence.

| Issue | GitHub | Status | Hard dependencies | Current files / implemented scope | Verification and commits | Remaining work |
|---|---:|---|---|---|---|---|
| F0 — Scope and architecture freeze | #1 | PARTIAL | Official problem statement and plans | `docs/MASTER_IMPLEMENTATION_PLAN.md`, `docs/ARCHITECTURE_DECISIONS.md`, `docs/API_CONTRACT.md`, amendment copy | Amendment hash-checked; docs commit `7cbb3f5` on `main` | Record the four coordinator acknowledgements; reconcile the full v2.1 master plan as implementation contracts are resolved. |
| F1 — Runnable monorepo skeleton | #2 | PARTIAL | F0 | `backend/pyproject.toml`, `backend/app/core/config.py`, `backend/app/core/db.py`, `backend/app/main.py`, `backend/app/workers/__main__.py`, Docker/Compose/Make configuration | Health test passed; Compose config passed; code commit `1cd21bb` pushed to `codex/backend-foundation` | Verify clean Compose startup and DB-aware readiness with Docker; integrate seeded scenario/reset once F4/C5 exists; complete documented run commands. |
| F2 — Shared DTOs and OpenAPI | #3 | PARTIAL | F0 | `backend/app/contracts/enums.py`, `backend/app/contracts/schemas.py`, `docs/API_CONTRACT.md` | DTO tests passed; code commit `63a0012` pushed to `codex/backend-foundation` | Register shared schemas in OpenAPI, export an integration schema artifact, reconcile schema with migrations, and hand it to frontend teammate. |
| F3 — Database bootstrap | #4 | NOT_STARTED | F1, F2 review | — | — | SQLAlchemy metadata, Alembic scaffold, initial migration, constraints/indexes, lock order, transaction fixture, PostgreSQL migration evidence. |
| F4 — Seed city and deterministic clock | #5 | NOT_STARTED | F2; persistence seeding after F3 | — | — | RealClock/SimClock, deterministic ETA fixture and reproducible seeded actors/demand/batch. |
| F5 — CI and shared validation | #16 | NOT_STARTED | F1–F3 | — | — | Backend CI, contract checks, PostgreSQL service, integration verification scripts. |
| A1 — Inventory and item-specific demand ledger | #6 | NOT_STARTED | F2, F3 | — | — | Five-bucket inventory conservation, per-item demand, repositories, constraints, ledger tests. |
| A2 — Donor, rider, and batch resource APIs | #7 | NOT_STARTED | F2, F3, A1 interface | — | — | Authenticated resource operations, batch intake, actors, snapshots and pagination. |
| A3 — Shelter capabilities and demand APIs | #8 | NOT_STARTED | F2, F3, A1 | — | — | Demand/capability APIs, walk-ins, closure/reopen and capability evidence. |
| A4 — Inventory audit and reconciliation | #17 | NOT_STARTED | A1, C2 interface | — | — | Reconcile quantity/custody/commitments without misclassifying external overcommit. |
| B1 — Clock math and conservative routing | #9 | NOT_STARTED | F2 | — | — | Pure deadline feasibility, deterministic route estimates, position fixture and reason codes. |
| B2 — Compatibility hard constraints | #10 | NOT_STARTED | F2 | — | — | Deterministic fail-closed compatibility rules and tests. |
| B3 — Greedy capacity-aware split planner | #11 | NOT_STARTED | B1, B2 | — | — | Pure, stable capacity/demand-aware proposals and truthful remainder. |
| B4 — Recovery proposal and fallback ranking | #12 | NOT_STARTED | B1, B2 | — | — | Pure recovery options; no false partial delivery or capacity overcommit. |
| B5 — CP-SAT optional comparison | #13 | NOT_STARTED | B1–B3 | — | — | P2/optional; do not start before P0 gates pass. |
| C1 — Versioned state machine/idempotency/leases | #14 | NOT_STARTED | F2 | — | — | Canonical transitions, scenario-scoped idempotency, exclusive renewable lease primitives. |
| C2 — Atomic assignment/pickup/delivery/custody | #15 | NOT_STARTED | F2, A2, A3 | — | — | Transactional command path, quantity accounting, rider occupancy and audit events. |
| C3 — Ghost watchdog and due-work scheduler | #22 | NOT_STARTED | C1, C2 | — | — | Durable due actions, progress-aware renewal, expiry-generation fencing and PostgreSQL worker execution. |
| C4 — Transactional mid-transit reroute | #23 | NOT_STARTED | A3, B4, C2 | — | — | Transactional fallback demand reconciliation, custody-preserving reroute/AT_RISK behavior. |
| C5 — Deterministic SimClock and chaos endpoints | #24 | NOT_STARTED | F4, C1 | — | — | Isolated deterministic timeline, bounded resume, demo reset and chaos controls. |
| C6 — Durable events and SSE | #25 | NOT_STARTED | F3, C1 | — | — | Committed event history, DB-cursor replay across processes, reconnect semantics. |
| C7 — Simulated rider/shelter actors | #21 | NOT_STARTED | F2, F4; command port contract | — | — | AUTO/MANUAL/SILENT/DELAYED simulated actors calling the real command services. |
| I0 — Early backend walking skeleton | #20 | NOT_STARTED | F0–F3, thin A1/C1/C2 | — | — | Persist donation → offer → accept → pickup → deliver; verify inventory/demand transaction and event recording. |
| I1 — First end-to-end vertical slice | #28 | NOT_STARTED | I0 plus foundation/resource/decision paths | — | — | Complete donor-to-delivery backend acceptance against PostgreSQL. |
| I2 — Ghost rider recovery acceptance | #29 | NOT_STARTED | I1, C3, C5, C7 | — | — | Healthy 15-minute simulation, targeted silent rider, renewable progressing lease evidence. |
| I3 — Shelter closure acceptance | #30 | NOT_STARTED | I1, A3, B4, C4, C5, C7 | — | — | Before/after pickup closure, competing fallback, preserved custody and quantity. |
| I4 — Backend live-demo wiring | #31 | NOT_STARTED | I2, I3, C6 and frontend interface | — | — | Backend seed/reset/time/chaos APIs, events and scenario evidence; frontend checks remain teammate-owned. |
| I5 — Final reliability/CI/freeze | Not present | NOT_STARTED | I4, F5 | — | — | GitHub issue was not created; implement backend acceptance from the amendment/plan, then create/attach issue when GitHub action is available. |

### Explicitly excluded frontend issues

Group D issues #18–#19 and #26–#27, plus D5, are assigned to the frontend teammate and are outside this backend implementation scope. Implement backend contracts/services needed by their integration, but do not edit React/TypeScript source or claim frontend acceptance.

## Next execution order

1. Finish F2 OpenAPI schema registration/export and contract handoff.
2. Build the minimal F3 schema/migration foundation required by I0, preserving the amendment's canonical custody and scenario/idempotency rules.
3. Implement a thin A1/C1/C2 database command path and deliver I0 before expanding advanced planner or simulation functionality.
4. Continue F4 and B1–B4/C3–C7 in their dependency order; leave B5 until mandatory P0 gates pass.
