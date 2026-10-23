# PeriShift implementation baseline

**Baseline:** F0 scope and architecture freeze, 2026-10-23  
**Status:** Proposed freeze recorded; coordinator acknowledgements are still required before F0 is fully accepted.

## Authority and precedence

This repository preserves the original [ZeroSpoil unified plan v2.0](../ZeroSpoil_UNIFIED_MASTER_IMPLEMENTATION_PLAN.md). The v2 review amendment is preserved verbatim at [docs/ZeroSpoil_v2_REVIEW_FIXES_AND_MASTER_PLAN_AMENDMENT.md](ZeroSpoil_v2_REVIEW_FIXES_AND_MASTER_PLAN_AMENDMENT.md). This file records the project-level implementation baseline without silently rewriting either source.

Apply requirements in this order:

1. Official problem statement and its safety constraints.
2. The unified v2.0 plan and the amendment together; the amendment patches the plan and does not replace it.
3. Frozen shared contracts in [API_CONTRACT.md](API_CONTRACT.md) and decisions in [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md).
4. Individual GitHub issue acceptance criteria and ownership boundaries.

If these sources conflict, stop the affected implementation, record the conflict, and get the integration coordinator to approve a contract/plan amendment before proceeding. Do not silently choose a new payload, state transition, safety rule, or owner.

## Scope freeze

### P0 release scope

- Donor batch intake and auditable quantity conservation.
- Deadline and compatibility checks; deterministic feasibility-first offers and capacity-aware lot splitting.
- Exclusive, progress-aware renewable pre-pickup leases; pickup and delivery custody transitions.
- Shelter demand changes, walk-ins, pre-pickup destination invalidation, and truthful recovery/escalation.
- Server-owned real and isolated scenario clocks, deterministic simulated rider actions, due-work processing, and replayable committed events.
- Role-checked donor, rider, shelter, coordinator, and judge flows; the primary donor-to-delivery vertical slice and judge failure scenarios.
- Backend-authoritative state, concurrency guards, idempotency, audit, and visible UI state/error handling.

### Cut lines

- P0 is single pickup to a single destination per lot. A carried lot remains assigned to its physical rider until delivered, transferred, or explicitly disposed; risk is a flag, not release of custody.
- Recovery must never mark a partial quantity as full delivery. Bounded sequential partial recovery remains P1 unless the coordinator explicitly revises scope with matching contracts and acceptance criteria.
- P1 includes richer routing/maps, ranking enhancements, PostGIS, CP-SAT comparison, signed pickup proof, and advanced recovery. P2 includes multi-stop/relay logistics, multi-trip optimization, live routing, Redis/job-queue scale-out, ML, and advanced notifications. These do not block the P0 workflow.
- ETA and safety outputs are estimates or rule outcomes under documented assumptions; they are not claims of real-world validation.

## Ownership and interface gates

- Integration coordinator: F0–F5, shared contracts, root Compose/CI, `backend/app/main.py`, and I1–I5 integration.
- Group A: schema/migrations and resource/demand repositories. It provides transactional persistence interfaces; it does not own Group C lifecycle transitions.
- Group B: pure, deterministic feasibility, compatibility, planning, and recovery proposals; no database writes or network calls.
- Group C: authoritative lifecycle commands, transaction boundaries, locks, idempotency, custody, leases, audit/event persistence, worker scheduling, and recovery.
- Group D: React/TypeScript UI and simulation controls; it consumes shared API contracts and never treats local mock state as persisted success.

F2 owns shared DTOs, enums, OpenAPI, and generated TypeScript types. F3/Group A owns schema and migration compatibility. Group C owns lifecycle writes. Contract changes require coordinator review and updates to affected issues before parallel implementation continues. No parallel worker may edit shared contracts, migrations, `main.py`, or Compose without its named owner.

## Change log and issue mapping

- **F0:** Recorded the P0/P1/P2 cut line, architecture choices, API conventions, amendment source, and file ownership. Existing F0–I5 issue identifiers remain stable; this baseline creates no duplicate issue. Amendment work is attached to the existing affected issues and the added C7/I0 work as listed in GitHub.
- **Remaining coordination gate:** Each of the four group coordinators must acknowledge this baseline and their file/contract ownership. Do not mark F0 accepted until those acknowledgements are recorded.
