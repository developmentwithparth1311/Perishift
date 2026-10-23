# PeriShift

### The last mile for good food.

**PeriShift** is a coordination platform concept for moving time-sensitive food donations from donors to receiving organizations. It brings donation readiness, safe pickup windows, recipient demand, rider capacity, handoff records, and route estimates into one shared workflow.

The project is being built for the challenge **“The Last-Mile Spoilage Trap: Coordinating Perishable Surplus & Fragile Aid Under Uncertainty.”** Its central idea is simple: make the constraints visible, propose feasible handoffs, and keep a clear record as a donation moves from offer to receipt.

| | |
| --- | --- |
| **Project** | PeriShift |
| **Team** | Runtime Terror |
| **Team leader** | Parth Kumbhar |
| **Team member** | Aaryan Patil |

> **Project status:** The repository currently contains the landing page, API and database foundations, shared data contracts, and health checks. The operational logistics workflow described below is the target defined by the project plans; several domain features are still in progress. See [implementation status](#implementation-status) before treating a capability as live.

---

## The problem

Food donations often have a short window in which they can be collected and used. A donor may know what is available and when it will be ready, while a receiving organization knows what it needs and can accept. A rider has limited time and carrying capacity. If these details are spread across calls, messages, and spreadsheets, teams can lose time coordinating the handoff. A missed pickup window, a quantity mismatch, a closed destination, or a rider dropping out can leave usable food without a workable next step.

PeriShift is designed to help coordinators answer four practical questions:

1. **What food is ready, in what quantity, and until when can it travel?**
2. **Which recipient can accept it, and does the offer fit that recipient's demand and handling needs?**
3. **Which rider and route can complete the handoff within the relevant time and capacity constraints?**
4. **What happened at each offer, pickup, and receipt—and what should happen if the plan changes?**

## How PeriShift is intended to work

The planned workflow follows a donation from intake through verified handoff:

1. **Record a donation.** A donor enters the food description, quantity, readiness time, pickup window, safe-until deadline, and handling details.
2. **Check recipients.** The system compares the donation with recipient demand, receiving windows, capacity, and handling requirements.
3. **Propose a feasible handoff.** A deterministic, feasibility-first planner ranks workable recipient and rider options using time, capacity, and route estimates. It should explain why an option is feasible or rejected.
4. **Offer and accept.** A recipient can accept or decline the proposed handoff. Acceptance creates a commitment that coordinators can track.
5. **Assign and progress a rider.** A rider accepts a pickup task and reports progress. A renewable lease is planned to make assignment ownership recoverable if a rider or worker becomes unavailable.
6. **Record custody and receipt.** Pickup and receiving confirmations update the handoff timeline and quantity ledger, so the final outcome is auditable.
7. **Re-plan when conditions change.** If a rider drops, a destination closes, demand changes, or a deadline becomes at risk, the system can surface the risk and suggest another feasible next step.

The system is designed to support human coordination. Its planner is not intended to make food-safety decisions or guarantee that a delivery will succeed.

## Planned system architecture

The implementation plan uses a **modular monolith**: one API application with clear domain, service, and repository boundaries. PostgreSQL is the durable source of truth for donations, quantities, offers, assignments, custody events, and scenario state. Commands that change a handoff are intended to apply their related updates transactionally, protecting quantity conservation and the custody history.

```mermaid
flowchart LR
    People[Donors, coordinators, riders, recipients] --> Web[React + TypeScript web app]
    Web -->|REST reads and commands| API[FastAPI modular monolith]
    API --> Contracts[Pydantic contracts and validation]
    API --> Services[Domain and command services]
    Services --> Planner[Deterministic feasibility-first planner]
    Planner --> Routes[Routing adapter]
    Services --> Repositories[Repositories]
    Repositories --> DB[(PostgreSQL)]
    Worker[Background worker] <--> DB
    DB --> Events[Persisted event stream / SSE]
    Events --> Web
```

The diagram describes the **planned architecture**. The current API is an early foundation and does not yet expose the full donation, planning, assignment, custody, simulation, or event-stream workflow.

### Architecture principles

- **Feasibility before optimization:** reject options that violate deadlines, capacity, demand, receiving windows, or handling constraints before ranking the remaining choices.
- **Deterministic decisions:** use explicit rules and route estimates that can be explained and reproduced. AI is not the authority for safety or feasibility.
- **One quantity ledger:** model reservations, splits, pickup, receipt, and recovery so quantities remain traceable instead of being silently duplicated or lost.
- **Custody as an event history:** record who accepted, collected, transferred, or received a donation and when.
- **Recoverable assignments:** planned progress leases and recovery flows help the system handle stale or interrupted rider assignments.
- **Risk and custody are separate:** a delay or temperature concern can raise a risk flag without silently rewriting the custody record.
- **Scenario-based demos:** seeded scenarios, a controllable simulation clock, and repeatable reset behavior are planned so a demo can be replayed consistently.

## Technology

| Area | Technology / approach | Status in this repository |
| --- | --- | --- |
| Web interface | React, TypeScript, Vite | Landing page is present; logistics application flows are planned |
| API | Python 3.12, FastAPI, Pydantic v2 | Application shell, health endpoints, and shared contracts are present |
| Persistence | PostgreSQL 16, SQLAlchemy 2, Psycopg 3, Alembic | Runtime foundation is present; domain schema and migrations are pending |
| Local orchestration | Docker Compose | Services are configured; local runtime depends on Docker being available |
| Routing | Deterministic routing adapter; Leaflet is the planned P0 map library | Planned; interactive logistics map is not yet connected to the backend |
| Background work | Database-backed worker | Worker shell is present; operational jobs are pending |
| Live updates | Server-Sent Events (SSE) backed by persisted events | Planned; event stream is pending |
| Tests | Pytest and HTTPX | Initial health and contract tests are present |

## Run locally

### Requirements

- Docker Desktop or Docker Engine with the Compose plugin for the full stack
- Node.js 22 for running the web app outside Compose
- Python 3.12 or newer in the supported project range for running the API locally

### Start the configured stack

```bash
cp .env.example .env
docker compose up --build
```

Then open:

- Web landing page: <http://localhost:5173>
- API documentation: <http://localhost:8000/docs>
- Liveness check: <http://localhost:8000/health/live>
- Readiness check: <http://localhost:8000/health/ready>

The credentials in `.env.example` are for local development only. Replace them before using the project in any shared or deployed environment. A successful startup depends on Docker being installed and running.

### Run the API directly

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

The API's database readiness check needs PostgreSQL. For local API development, set `DATABASE_URL` to a PostgreSQL instance reachable from your machine; for example, if Compose PostgreSQL is running, use `postgresql+psycopg://perishift:local-dev-only@localhost:5432/perishift`.

### Run the web app directly

```bash
cd web
npm ci
npm run dev
```

The web app is currently a project landing page and UI preview. It does not yet provide the complete operational workflow described in the plans.

### Run the current backend checks

```bash
cd backend
python -m pytest
```

## Repository map

```text
.
├── backend/
│   ├── app/
│   │   ├── api/          # API route package
│   │   ├── contracts/    # Shared enums, schemas, and OpenAPI helpers
│   │   ├── core/         # Settings and database setup
│   │   ├── domain/       # Domain model boundary
│   │   ├── repositories/# Persistence boundary
│   │   ├── services/     # Use-case and command-service boundary
│   │   └── workers/      # Background worker entry point
│   └── tests/            # API and contract tests
├── web/
│   └── src/ui/           # React landing page and styles
├── docs/
│   ├── ARCHITECTURE_DECISIONS.md
│   ├── API_CONTRACT.md
│   ├── BACKEND_IMPLEMENTATION_PROGRESS.md
│   ├── MASTER_IMPLEMENTATION_PLAN.md
│   └── PeriShift_Project_Dossier.docx
├── docker-compose.yml
└── Makefile
```

## Implementation status

The current checkout is an early implementation slice, not a completed logistics platform. The latest detailed progress record is [BACKEND_IMPLEMENTATION_PROGRESS.md](docs/BACKEND_IMPLEMENTATION_PROGRESS.md).

**Present now:** the PeriShift landing page; FastAPI application shell; liveness and database-readiness endpoints; initial shared enums and request/response validation; PostgreSQL and worker service configuration; initial health and contract tests.

**Still planned or in progress:** database schema and migrations for operational entities; donation and recipient APIs; inventory and quantity ledger; feasibility planner; offers and acceptance; rider assignment leases; custody and receipt events; deterministic route display; scenario simulation and reset; rerouting and recovery; persisted SSE updates; end-to-end operational demo.

## Project documents

- [Unified implementation plan](ZeroSpoil_UNIFIED_MASTER_IMPLEMENTATION_PLAN.md) — consolidated product and engineering plan.
- [Master implementation plan](docs/MASTER_IMPLEMENTATION_PLAN.md) — phased delivery plan and system behavior.
- [Architecture decisions](docs/ARCHITECTURE_DECISIONS.md) — proposed technical decisions and boundaries.
- [API contract](docs/API_CONTRACT.md) — shared API and data-model contract.
- [Backend implementation progress](docs/BACKEND_IMPLEMENTATION_PROGRESS.md) — current implementation state and remaining work.
- [Plan amendment review](docs/ZeroSpoil_v2_REVIEW_FIXES_AND_MASTER_PLAN_AMENDMENT.md) — review notes and plan corrections.
- [Project dossier](docs/PeriShift_Project_Dossier.docx) — presentation-ready project overview, architecture, workflow, and jury preparation material.

## Team

**Runtime Terror**

Team leader: **Parth Kumbhar**

Team member: **Aaryan Patil**

---

PeriShift is a project prototype for coordinating time-sensitive perishable aid. Route and workflow examples should be treated as illustrative until their corresponding live system features are implemented and validated.
