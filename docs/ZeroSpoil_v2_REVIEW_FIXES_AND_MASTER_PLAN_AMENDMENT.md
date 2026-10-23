# ZeroSpoil v2 — Verified Loopholes, Corrective Architecture and Master-Plan Amendment

**Status:** Implementation directive / patch specification, **not** a replacement for the master plan  
**Review date:** 23 October 2026  
**Authoritative base:** `ZeroSpoil_UNIFIED_MASTER_IMPLEMENTATION_PLAN.md` (v2.0, 2,229 lines)  
**Inputs:** Original Vibe-A-Thon problem statement, ZeroSpoil ideation playbook, first review, and subsequent “ZeroSpoil v2 Plan Review: Loopholes and Fixes”  
**Output required from a coding/planning LLM:** an internally consistent revised master Markdown plan, plus corresponding GitHub issue revisions and acceptance tests. **Do not silently modify the original file.**

> **Short verdict:** The new review identifies several genuine P0 gaps. In particular, simulated riders need to *act* during time warp; healthy riders need lease renewal; custody must survive risk-status changes; pre-pickup shelter closure needs a defined transition; demo reset must isolate idempotency state; and SSE must work when API and worker are separate processes. Preserve the modular monolith, PostgreSQL concurrency controls, deterministic safety engine, and four-team/subagent structure.

---

## 0. What to do with this document

1. Read the **entire** authoritative master file before editing it. The findings here are an amendment, not a license to redesign its stack.
2. Verify every alleged gap against the master file. A feature mentioned in prose is **not** complete if its schema, endpoint, issue, state transition, and test disagree.
3. Apply all changes marked **P0 / must fix**, then repair every associated section: tables, DTOs, APIs, algorithms, tests, GitHub issues, agent prompts, dependency graph, demo and README.
4. Treat P1/P2 enhancements as gated scope. If event time is tight, deliver the functional P0 workflow and judge scenarios first.
5. Produce a new versioned master file, e.g. `ZeroSpoil_UNIFIED_MASTER_IMPLEMENTATION_PLAN_v2_1.md`; keep the original v2.0 intact. The only time to edit v2.0 in place is if the user explicitly requests that operation.
6. Include a concise **change log** and an **old issue → new issue** map. Do not create duplicate issues for already specified capabilities.
7. Never label planned behavior, estimated P90 routing, food safety, rescued food, or stress-test throughput as real-world validated unless evidence exists.

### Non-negotiable architectural baseline

- Frontend: React + TypeScript, Vite.
- Backend: Python + FastAPI, modular monolith; domain decision functions remain testable without I/O.
- Database: PostgreSQL, SQLAlchemy/Alembic; the source of truth for physical quantity, demand, leases, custody and audit history.
- Routing: deterministic simulation-safe provider with clearly labeled estimated travel times.
- Realtime: replayable event stream (SSE for P0).
- Clock: server-owned real vs isolated deterministic scenario clock.
- Concurrency: transactions, row locks, version guards, uniqueness, idempotency, retry policy; no global serialization of unrelated deliveries.
- Multiagent: Group A resources/data, B pure decisions, C lifecycle/operations, D frontend/simulation; integration owner coordinates contracts and final build.
- Not mandatory: Kafka, Kubernetes, microservices, Redis, CP-SAT, ML model, PostGIS, multiple API replicas.

---

## 1. Validation matrix: what the latest reviewer got right, and what needs nuance

Here `Confirmed` means the stated gap is present in master v2.0; `Partial` means the plan mentions something but does not fully specify or consistently implement it; `Qualified` means the proposed fix should not be copied literally.

| Review | Finding in unified v2.0 | Assessment | Required action |
|---|---|---|---|
| 1: time-warp makes healthy riders silent | §8.4 says “stop scenario heartbeat/progress generation” but no generator or simulated movement contract exists. §8.3 advances due jobs without creating a healthy rider’s due actions. | **Confirmed P0** | Create simulated rider actor and pure `position_at` model, processed in the simulated event queue. |
| 2: 10-minute TTL kills distant healthy riders | §5.2 uses `min(10 min, .25 × slack)` and §8.2 revokes on expiry; no healthy-progress extension protocol. | **Confirmed P0** | Specify progress-aware lease renewal, separate physical cutoff from opportunity to rescue using standby. |
| 3: rider freed with cargo | §6.3 partial unique indexes cover `RESERVED`, `IN_TRANSIT`, `REROUTING`, not `AT_RISK`, while §4.5 uses `AT_RISK` as lot state. Separate lot/assignment/lease representations can diverge. | **Confirmed P0** | One authoritative mapping; keep assignment occupancy while cargo carried; risk as flag. |
| 4: shelter closes before pickup | §6.4 TX-4 marks commitments for revalidation; §4.5 has reroute from `IN_TRANSIT` only. No complete pre-pickup re-target transition or deterministic overcommit policy. | **Confirmed P0** | Define `LEASED` retarget/revoke and coordinated demand reconciliation ordering. |
| 5: partial alternative demand | §5.5 explicitly does **not** allow a 40-unit lot to deliver 30 to a 30-capacity fallback in P0; C4 asserts no false 40-unit success. | **Real capability gap; P0-light / P1 fallback** | Add *bounded sequential partial recovery* if it can be built safely; never violate custody accounting or overpromise. |
| 6: reset/idempotency conflict | §7.4 uses a fixed demo idempotency example; §6.3 key uniqueness lacks scenario scope and §8.4 reset does not explicitly clean keys/jobs. | **Confirmed P0** | Namespace idempotency/dedupe by scenario; purge/invalidate old scenario state atomically. |
| 7: API/worker realtime mismatch | §2.2 separate worker, §6.6 publisher sends to an **in-process subscriber**; different processes cannot share that subscriber. | **Confirmed P0** | MVP SSE reads committed events from Postgres by cursor; poll or LISTEN/NOTIFY; outbox optional for external side effects. |
| 8: inconsistent transaction lock order | TX-2 locks batch/lot/rider/demand, TX-5 lot/demands; global lock order is only promised, never fixed. | **Confirmed P0** | One lock order across commands and bounded whole-transaction retry on `40P01`, `40001`. |
| 9: DTO/DDL/event drift | §7.4 accepts `ready_at`, §6.3 batches does not have it. No persisted per-lot picked/rejected quantities or full heartbeat/handling metadata. Appendix A omits codes used elsewhere. | **Confirmed P0** | F2/F3 contract reconciliation and one canonical enum/specification. |
| 10: manual rider/shelter buttons in demo | No simulated auto-accept/pickup/deliver/confirm actors in scheduler. | **Confirmed P0** | Use same simulated actors; demo-only auto-confirm with deterministic policy and explicit UI label. |
| 11: hosted deployment | §19 targets Docker Compose; no guaranteed public URL. | **Conditional** | Aim for hosted demo if accessible/reliable; maintain clean local demo as required fallback. Single-process mode must be safe. |
| 12: reactive repeat trips | §5.4 marks multi-trip optimization P2, but ordinary redispatch when rider becomes free is not fully specified. | **Useful P1, possibly cheap P0** | Re-run planner on release/delivery/availability, no overlapping active trips. No multi-trip solver required. |
| 13: near-expiry donor salvage | Plan suggests partial/infeasible responses, but lacks fully specified urgent last-chance escalation path. | **Partial P1** | Urgent broadcast → feasible self-delivery suggestion → truthful disposal logging. All alternatives must remain safety constrained. |
| 14: innovation baseline | §5.3 defines one greedy strategy and §3 claims recovery-aware value; no reproducible nearest-valid comparator. | **Confirmed evidence gap, P1** | Add deterministic baseline and matched-condition comparison; only state measured simulated results. |
| Execution: cut F19/F29 | In master, **F19/F29 are judge failure test IDs, not GitHub implementation issues**. B5 CP-SAT already P1. | **Qualified** | Downgrade costly *test cases* if needed, don't delete mandatory correctness gates or nonexistent “F19/F29 issues”. |
| Execution: walking skeleton | I1 formally depends on a long foundation chain, so first real slice may arrive late. | **Confirmed process risk P0** | Add earlier `I0` thin vertical slice and adjust dependencies/timeline. |

### Important corrections to the review's proposed solutions

- **Do not make `AT_RISK` a terminal assignment state or release rider occupancy.** A risk label is not a physical custody transfer.
- **Do not treat the best backup ETA as a magical fixed subtraction that forces a progressing rider to be canceled.** Preserve one hard physical feasibility deadline and a separate rescue-opportunity deadline. A healthy primary can be safe even after the backup opportunity passes.
- **Do not simply “fold partial reroute into F13.”** `F13` in the *ideation* playbook is a *failure scenario*, not a GitHub issue in unified v2.0. Actual affected issues are **A1, B4, C2, C4, C5, F2, F3 and I3**.
- **Do not use one global PostgreSQL advisory lock for every command.** Lock only affected aggregates in a consistent order; scoped advisory locks can be added to genuinely complicated cross-row flows.
- **Do not make `expected_version` optional in production.** A demo-only convenience layer may acquire the current version under lock, while still requiring transactional conflict checks.
- **Do not remove the append-only audit events.** The P0 simplification removes the *external delivery outbox*, not durable event recording.
- **Do not claim hosted deployment is demanded by the official handout.** It is beneficial if stable and permitted; test judge access and keep one-command local backup.

---

## 2. P0 FIX A — Simulated riders must remain alive during time warp

### Root cause

`SimClock.advance(900)` currently executes scheduled deadlines, but an ordinary rider is only conceptually sending heartbeats from a client. Without generated healthy progress events, the watchdog falsely treats all seeded riders as ghosts.

### Required simulation design

Introduce a **deterministic `SimulatedRiderAgent`** per seeded rider. It is not an LLM subagent; it is a lightweight in-application simulator actor.

Suggested state:

```text
SimRider(id, scenario_id, mode=AUTO|MANUAL|SILENT|DELAYED,
         route_segments, scheduled_generation,
         heartbeat_period_seconds, accept_delay_seconds,
         pickup_delay_seconds, confirmation_policy)
```

Commands/actions it can schedule:

```text
SIM_ACCEPT_OFFER
SIM_HEARTBEAT
SIM_PICKUP
SIM_RIDER_MOVE_OR_POSITION_TICK      # optional: derive position lazily
SIM_DELIVER
SIM_CONFIRM_SHELTER_RECEIPT
SIM_RIDER_RESUME
```

The actor must use **the exact same domain service calls** as real actors; no direct mutation of inventory or assignment counters.

### Position as pure function

```python
position_at(
    assignment_id: UUID,
    scenario_time: datetime,
    route_segments: Sequence[RouteSegment],
    traffic_state: TrafficSnapshot,
) -> Coordinates
```

- Anchor segments at dispatch/pickup/reroute time and known origin/destination.
- Interpolate by scheduled segment duration; pause when rider is deliberately stopped, reroute from last authoritative simulated position.
- A traffic multiplier changes remaining ETA/segment progression only according to a documented policy; don't teleport a rider by recomputing the entire path from the original start.
- Server stores sufficient movement anchors; UI can interpolate smoothly from them between authoritative updates.
- `SILENT` stops heartbeats/progress events **only for targeted actors**; other riders continue.
- Missing progress from an API-only accepted lease remains a genuine no-heartbeat case: the watchdog's **server-side first-progress deadline** still detects it.

### Event advancement semantics

- Every action has `(scenario_id, due_at, deterministic_sequence, id)` and runs once per logical generation.
- Advancing 900 seconds must run simulated rider actions **and** expirations in a single ordered timeline.
- At the same timestamp, specify priority: e.g. authenticated pickup/heartbeat accepted at deadline only if business policy permits it; otherwise timeout wins. The ordering must be deterministic and tested.
- A 900-second jump can generate hundreds of heartbeat events. Bound work per request, allow safe continuation, and don't silently jump past unprocessed due events.
- Ignore stale scheduled actions after route version/lease generation changes.
- Do not allow a real-time worker to sweep simulation jobs based on wall time.

### Test gates

`SIM-01` Five healthy auto riders, 15-minute jump: **zero false ghost revocations**.  
`SIM-02` Target only R3 with rider-silent chaos: R3 suspect/revoked; others remain alive.  
`SIM-03` Route position at T+0, midpoint, completion is deterministic.  
`SIM-04` Reroute at midpoint anchors new path there, no teleport.  
`SIM-05` Same seed + same command list → same event sequence and final balances.  
`SIM-06` Large advance with processing cap resumes without skipping intermediate due events.

**Issue owners:** C5 + new C7; B1 routing position fixture; D3 map; D4 chaos controls; F4 seed.

---

## 3. P0 FIX B — Correct adaptive lease and healthy renewal behavior

### Separate three different times

1. **Primary physical feasibility cutoff:** Latest possible pickup/custody transfer that can still safely meet the destination deadline, accounting for handover and ETA. Calculated from current data.
2. **Heartbeat/progress lease expiry:** Short renewable checkpoint. Healthy progress extends it; absence of progress causes suspect/revocation.
3. **Backup rescue opportunity cutoff:** Latest time a standby rider can start (including offer/accept delay, its ETA to donor, handover and delivery). This is a planning/escalation threshold, **not always** the primary rider's hard deadline.

For each active primary rider, reevaluate:

```text
can_primary_finish_now =
    now + eta(primary_position, donor) + pickup_handling
      + eta(donor, destination) + delivery_handling <= deliver_by

can_backup_rescue_at(t) =
    t + offer_accept_delay + eta(backup_position_at_t, donor)
      + pickup_handling + eta(donor, destination)
      + delivery_handling <= deliver_by
```

**Renewable lease policy:**

```text
on_accept:
    reject unless can_primary_finish_now
    lease.expires_at = min(primary_hard_cutoff,
                           now + initial_progress_window)
    lease.first_progress_by = min(primary_hard_cutoff,
                                  now + first_progress_window)

on_valid_progress:
    revalidate rider identity, custody state, real movement, clock and ETA
    if still feasible:
        expires_at = min(primary_hard_cutoff_recomputed,
                         max(current_expires_at, now + renewal_window))
    else:
        revoke pre-pickup lease or escalate as appropriate
```

- Windows are configurable and may be short; **never use an unconditional 10-minute cap on total lifetime**.
- `primary_hard_cutoff` is not a fixed immutable timestamp after traffic or destination changes; recompute from fresh operational state and enforce a safe upper bound.
- Heartbeat alone may establish *liveness* but should not fabricate physical progress. Require position or confirmed milestones when feasible; treat GPS uncertainty explicitly.
- If backup rescue cutoff is approaching, send standby offers early; do not create a second active lease. If primary is healthy but only the primary remains feasible, record elevated risk and alert, not an invented backup.
- Renewals must invalidate / generation-fence stale expiry jobs, or expiry handlers must re-read current `expires_at` + version and ignore outdated jobs.

### Boundary tests

`LEASE-01` Rider 12–20 minutes away, making valid progress, stays leased past minute 10 until pickup.  
`LEASE-02` Rider 12 minutes away, no progress, revoked at configured first-progress / feasibility deadline.  
`LEASE-03` Traffic causes infeasibility: lease revoked **before pickup** with reason.  
`LEASE-04` Healthy lease renewal never extends beyond actual physical safe pickup feasibility.  
`LEASE-05` A stale scheduled expiration from before renewal does not revoke a valid renewed lease.  
`LEASE-06` Primary safe but backup now impossible: show risk; do not automatically cancel healthy primary.  
`LEASE-07` No standby exists: system makes no claim about guaranteed reassignment.  
`LEASE-08` Pickup and renewal/expiry occur concurrently: exactly one legal custody outcome.

**Issue owners:** B1 feasibility, C1 lease/state model, C3 watchdog, C7 actor, F2 DTO, I2 acceptance.

---

## 4. P0 FIX C — One authoritative custody/assignment/risk model

### Canonical separation

Use one stable representation for **physical custody and active assignment**, and separate flags for operational alerts:

| Physical / business phase | `lots.state` | `assignments.status` | Active pickup lease | Rider occupied? | Risk flags |
|---|---|---|---|---|---|
| Unreserved stock | `OPEN` or `OFFERED` | none | no | no | none |
| Accepted, before pickup | `LEASED` | `RESERVED` | yes | yes | `SUSPECT` possible |
| Picked up and going to destination | `IN_TRANSIT` | `IN_TRANSIT` | no | **yes** | `REROUTE_PENDING`, `AT_RISK`, `LOCATION_STALE` possible |
| Rerouting in progress | `IN_TRANSIT` | `IN_TRANSIT` | no | **yes** | `REROUTE_PENDING` |
| No feasible receiving point | `IN_TRANSIT` | `IN_TRANSIT` | no | **yes** | `AT_RISK` and coordinator alert |
| Safely delivered all cargo | `DELIVERED` | `DELIVERED` | no | no | resolved |
| Pickup lease released/canceled | `OPEN` / `EXPIRED` | `CANCELLED` | no | no | resolved |
| Physically disposed after pickup | `DISPOSED` | `CANCELLED` or terminal disposition | no | no after verified disposition | reason logged |

A `DISPOSED` terminal assignment should preferably have its own `DISPOSED` status rather than overloading `CANCELLED`; if introduced, update indexes and enums accordingly. **Never mark occupancy free until the physical remainder has been delivered or explicitly disposed/transferred.**

### Database backstops

- Use a unique partial index on `assignments(lot_id)` and `assignments(rider_id)` where `status IN ('RESERVED','IN_TRANSIT')`, if those are the complete **active** statuses by contract.
- If retaining a distinct `REROUTING` status, include it in both unique indexes. Never add a new active status without changing indexes and tests.
- `AT_RISK`, `SUSPECT`, `LOCATION_STALE`, `REROUTE_PENDING` are **flags**, not assignment statuses in P0.
- `leases.active` is true only pre-pickup; it is never used as the sole source for physical custody.
- Stop using contradictory lot-state transitions (e.g. `IN_TRANSIT → AT_RISK`) in diagrams if AT_RISK is a flag. Replace them with flag-set/clear events.
- Choose one owner of all lifecycle writes: the Group C command service. Group A exposes transactional repositories but does not write lifecycle transitions.

### Tests

`CUSTODY-01` In-transit rider marked AT_RISK cannot accept another lot.  
`CUSTODY-02` Same in-transit lot cannot be assigned to another rider after silence.  
`CUSTODY-03` Transfer/disposal releases occupancy only after verified physical resolution.  
`CUSTODY-04` No legal row combination has carried cargo + inactive rider assignment.  
`CUSTODY-05` Every status transition matches canonical table and versions.  
`CUSTODY-06` Reroute keeps same physical holder until a separate custody event.

**Issue owners:** F2, F3, A1, C1, C2, C4, I1/I3.

---

## 5. P0 FIX D — Closure before pickup and coordinated demand recovery

### New trigger: destination invalidated while lot LEASED

When a shelter closes, reduces intake, or loses compatibility before pickup:

1. Commit the shelter/demand change and audit event immediately.
2. Find affected `RESERVED` commitments and mark them `DESTINATION_REVALIDATION_REQUIRED` (flag/action, not automatic custody change).
3. For each affected lot, evaluate a replacement eligible demand using current time, quantity and compatibility.
4. **If a safe destination exists:** atomically retarget assignment demand commitment `old reserved → new reserved`, preserve rider lease when rider remains feasible, increment version, send rider route-change notification; require rider acknowledgement if enabled.
5. **If none exists:** atomically revoke lease, release donor-held reserved quantity and destination commitment, invalidate old offers, reoffer safely or expire if no safe path remains.
6. Do not cancel an in-transit rider's physical cargo under the pre-pickup path.

### Demand-shrink prioritization

When a walk-in causes current need to fall below reserved + transit commitment:

- Never rewrite already received quantity or silently erase commitments.
- First identify **unpicked-up reservations**, since they can be released without stranding physically held goods.
- Choose a **deterministic order** with explicit justification (e.g. protect low-slack/urgent viable allocations; release the assignments with greatest slack / easiest safe alternatives first). “Latest deadline first” is a possible policy, not a universal optimum.
- If excess remains, coordinate affected **in-transit** recovery candidates in increasing slack order to protect the most urgent cargo.
- Evaluate competition for alternate capacity together where practical. In all cases, committing each reroute must lock/revalidate candidate demand so two workers cannot consume the same fallback stock/demand.
- Use bounded retries; if no alternative exists, show AT_RISK and quantity, not fabricated placement.
- Multiple demand changes may arrive during recovery; compare versions before committing.

### Required tests

`DEMAND-01` Shelter closes while lot LEASED; new destination safe → retarget, no double reservations.  
`DEMAND-02` Same but no alternative → release donor-held reservation only.  
`DEMAND-03` Walk-in 30 into a 50-unit demand reduces future commitments by 30.  
`DEMAND-04` Multiple riders compete for one fallback → capacity never overcommitted.  
`DEMAND-05` Walk-in causes overcommitment; received count remains accurate; reconciliation eventually records valid disposition or alert.  
`DEMAND-06` Competing closure and pickup produce a valid final state.  
`DEMAND-07` Capability loss causes both unpicked and in-transit revalidation.

**Issue owners:** A3 trigger, B4 candidate proposals, C2 transactional release/retarget, C4 coordinated recovery, F2/F3 statuses, I3.

---

## 6. P0-light / P1 FIX E — Partial rescue without inventing full multi-stop optimization

### Distinguish three quantities

- `cargo_qty_on_rider`: physically carried and still unresolved.
- `next_drop_qty`: quantity **currently intended** for the next receiving destination.
- `at_risk_remainder_qty`: physically held quantity not yet committed to a destination.

Example: rider has **40** meals, shelter A closes, shelter B can receive **30**. The system may reserve **30** for B and mark **10** as uncommitted/AT_RISK while **all 40 remain carried by the same rider**. Only after confirmed handover of 30 does rider cargo drop to 10. The system then evaluates the remaining 10 at the **new actual position and time**. If no safe destination exists, log/alert rather than claiming 40 delivered.

This is **sequential partial recovery**, not simultaneous independent assignment of the same rider to two active lots. If completing it safely is too risky for the deadline, use the original P0 rule (accept only a full-40 alternate; otherwise show 40 AT_RISK). Never create a deceptive partial-success UI.

### Required schema/service adjustments

- Add an active cargo quantity / allocation slice model with explicit `delivered_qty`, `rejected_qty` and `uncommitted_carried_qty` movements.
- Ensure one rider occupancy remains throughout sequential legs.
- Confirm each destination's intake, class, deadline and exact accepted units at its own drop.
- Balance identity remains `available + reserved + carried + delivered + disposed = original`.
- Shelter transit commitment cannot exceed the **portion actually committed to that shelter** (30 in the example).
- If expired before second drop, do **not** mark those 10 as delivered; escalate/record disposition.

### Tests

`PARTIAL-01` Carry 40 → commit 30 to B → deliver 30 → continue holding 10 → second route or disposition.  
`PARTIAL-02` Another rider cannot claim the remaining 10 held in transit.  
`PARTIAL-03` Two destinations and both demand ledgers reconcile independently.  
`PARTIAL-04` Deadline passes after first drop → remaining 10 never count as safe delivery.  
`PARTIAL-05` Arrival accepts only 25 of committed 30 → 5 remains physically on rider, handled explicitly.

**Issue owners:** A1, B4, C2, C4, F2/F3, I3. **Do not refer to “F13 GitHub issue”; it does not exist in this backlog.**

---

## 7. P0 FIX F — Resettable idempotency and demo-friendly inputs

### Scenario scope and reset rules

Every demo mutation, idempotency entry, scheduled job, and event belongs to a unique scenario execution. Recommended unique scopes:

```text
idempotency: (execution_scope, actor_id, endpoint, idempotency_key)
scheduled work: (execution_scope, action_type, aggregate_id, generation)
audit timeline: (execution_scope, event_id)
```

`execution_scope` is a **non-null** namespace (e.g. a UUID for demo execution or a reserved real-mode namespace). Do not rely on PostgreSQL's ordinary `UNIQUE` behavior for a nullable `scenario_id`.

Reset may either:

- **Prefer:** create a new scenario execution ID and seed fresh data, retiring the previous namespace; or
- Atomically clear/recreate all scenario-scoped idempotency, scheduled actions, actor plans, simulation state, and relevant audit state *without touching production records*.

Never let a stale scheduled action from a prior run execute against a newly seeded record.

### UI and manual API actions

- Generate an idempotency UUID **once per intended click/action**; reuse that key only on genuine retry of the same action.
- Keep the UI free-play mode: judges can create arbitrary valid batches with quantity, safe-by, ready time and optional restrictions.
- Production mutation endpoints require actor, authorization, idempotency key and state version per contract.
- **Demo-only** Swagger/curl convenience may infer scenario ID and synthesize missing idempotency/version inside the validated command service. Explicitly document that omission weakens client retry deduplication and is not the production API policy.
- Provide helpful error messages for 3-minute deadline, 1000-unit batch, no rider, no demand, invalid input.
- SSE cursor must reset/switch namespace on demo reset rather than replay an old scenario's events.

### Tests

`RESET-01` Reset, run identical seeded scenario twice; no cached old batch/lot ID returns.  
`RESET-02` Duplicate request within same run returns same committed result.  
`RESET-03` Same idempotency key in distinct executions does not collide.  
`RESET-04` Stale queued actions from earlier run cannot alter new run.  
`RESET-05` Concurrent reset/advance rejected or serialized.  
`RESET-06` Manual judge API calls with documented demo defaults work; production still enforces strict contract.

**Issue owners:** F2, F3, F4, C1, C5, D4, I4.

---

## 8. P0 FIX G — SSE that works across processes, without mandatory outbox

For MVP, **PostgreSQL `events` is the authoritative append-only source**, and each SSE connection retrieves committed events by `events.id > cursor` within its scenario namespace.

Suggested behavior:

```text
GET /api/v1/events/stream?scenario_id=...
Last-Event-ID: 1042

loop while connection alive:
   SELECT ... FROM events
    WHERE execution_scope=:scope AND id>:cursor
    ORDER BY id ASC LIMIT :page_size
   emit each as SSE id/data/event; advance cursor
   if empty: wait ~1 s (or await LISTEN/NOTIFY wakeup)
   periodically emit SSE comment keepalive
```

- An API process does not need to receive a worker's in-process callbacks; the worker writes `events` in PostgreSQL.
- A reconnecting client replays missed committed events by cursor or refreshes a full state snapshot.
- Each subscriber can poll simply for hackathon scale; don't claim infinite fan-out performance.
- Optional `LISTEN/NOTIFY` reduces latency but must be treated as a **wake-up hint**, never the durable event store. PostgreSQL's notifications are emitted after commit; their delivery does not replace querying the events table.
- **MVP:** drop `outbox` publication machinery if the only consumer is the UI. Keep transactional events. **P1/production:** reintroduce a transactional outbox for durable integration with third-party notifications, a real queue or external side effects.
- Adjust `C6`, §2.2, §6.2–§6.6, §9.4 and §19 to state the same choice.

**Tests:** worker process creates event → another API process's SSE client sees it; reconnect resumes; event is never replayed across scenario reset; no duplicate visible state application.

---

## 9. P0 FIX H — One global database lock order and retriable command pipeline

### Ownership of writes

Group C owns the **single transactional command pipeline**. Group A owns repositories and migrations, not alternative command-level locking strategies. Every mutation to stock, commitments, lease, assignment, and custody passes through the one pipeline (or a reviewed thin wrapper that takes the same locks).

### Canonical lock order

Example lock order (apply consistently to every command, skipping absent resources):

```text
0. Claim/check idempotency key for the given execution scope (same transaction)
1. batch rows, sorted by UUID
2. batch_inventory rows, sorted by batch_id
3. lot rows, sorted by UUID
4. demand rows, sorted by UUID (old/new both in sorted order)
5. rider rows, sorted by UUID
6. assignment rows, sorted by UUID
7. lease rows, sorted by UUID
8. event and scheduler inserts; response/idempotency completion
```

A shelter update that needs to lock `shelter` must either do so **before** all its child `demand` rows by an explicit globally published order, or update shelter state in one short transaction and enqueue subsequent per-demand recovery. The canonical rule must cover all tables actually mutated, including `shelters`, `offers`, and `scheduled_actions`; don't pretend the partial example is already exhaustive. Avoid acquiring a previously skipped **earlier-rank** lock after holding later locks. When an operation discovers a new required entity late, end/retry with a freshly computed lock set.

### Retry contract

- PostgreSQL SQLSTATE **`40P01`** (`deadlock_detected`) and **`40001`** (`serialization_failure`) are candidates for bounded **whole-transaction** retries with jitter.
- Recompute the validation/read snapshot and current simulated/real clock on each retry. Never replay a stale committed plan or stale ETA blindly.
- After retry limit, return a typed retryable conflict and preserve state integrity.
- Handle expected unique-index conflicts as business conflicts (e.g. only one rider wins a lease) rather than automatically retrying them as successful allocations.
- Scoped transaction advisory locks are an option for narrowly identified cross-aggregate resources, **not** one global lock for every donation.

### Boundary tests

`DB-01` 50 concurrent accepts on a lot → exactly one winner, no inventory drift.  
`DB-02` Simultaneous closure/reroute/accept → no deadlock or bounded documented retry; valid result.  
`DB-03` Two riders compete for limited fallback shelter → no demand overcommit.  
`DB-04` Pickup races watchdog → exactly one physical outcome.  
`DB-05` Forced simulated `40P01` / `40001` in test fixture → complete retry or typed failure.  
`DB-06` Concurrent unrelated batches progress without holding a global lock.

---

## 10. P0 FIX I — Contract, SQL, event and reason-code reconciliation

F2/F3 must publish **one canonical contract matrix**, then regenerate OpenAPI/TypeScript types and fixtures. At minimum reconcile:

| Entity / operation | Required detail to formalize |
|---|---|
| `batches` | `ready_at`, `pickup_window_end`, optional food-preparation timestamp, safety-evidence fields, dietary/allergen requirements, class/unit, computed safe/deliver cutoffs |
| `riders` / simulator | authoritative last heartbeat/progress timestamps, position update authority, route segment anchors, availability/capacity, `scenario_id` |
| `lots` / physical cargo | allocated, picked, delivered, rejected/disposed and remaining carried quantities or linked movement records; version and current flags |
| `offers` | published, accepted, declined, expired; rider decline endpoint optional but useful to prevent pointless wait |
| `leases` | renewable expiry, first progress deadline, last valid progress, renewal generation / version |
| `assignments` | active statuses, risk flags, next destination, quantities committed for next drop, rider acknowledgement if enabled |
| `demands` | `required/received/reserved/transit`, walk-in quantity reason/source, closed/reopen/capability update, one per item class/time window |
| `deliver` DTO | `accepted_qty`, `rejected_qty`, destination confirmation proof, handling/expiry validation result, remaining custody |
| `pickup` DTO | `picked_qty` ≤ authorized lot qty, shortage/overflow reason, safety evidence when applicable |
| clock | `GET /api/v1/clock` returning current real or scenario time and mode; server-owned cutoff calculation |
| reason/event enums | All codes used in routes, tests, examples, diagrams and appendices must exist once in canonical enum |

### Additional mandatory reason/event codes to consider

```text
DELIVERY_BLOCKED_UNSAFE
EXPIRED_CARGO
PARTIAL_DELIVERY_CONFIRMED
DESTINATION_REVALIDATION_REQUIRED
REROUTE_ACK_PENDING
REROUTE_ACK_TIMEOUT
LEASE_RENEWED
SIM_RIDER_HEARTBEAT
SIM_RIDER_AUTO_CONFIRMED
SCENARIO_RESET
DEMO_ACTION_REJECTED
SAFETY_BLOCKED                # cross-reference existing example
INSUFFICIENT_CAPACITY         # cross-reference planner response
PICKUP_BLOCKED_UNSAFE
COMPATIBILITY_CHANGED
```

Choose aliases/removals explicitly; don't keep both `CAPACITY_RIDER` and `INSUFFICIENT_CAPACITY` unless they deliberately mean different things. Make event names separate from reason codes. Not all suggested strings need to become events; maintain one canonical naming policy.

### End-of-life safety gate

At delivery confirmation, recompute actual delivery/acceptance feasibility using authoritative clock, recorded handling conditions, current destination capabilities and accepted/rejected quantities. If unsafe, **do not increment safe-delivered count**. Preserve custody and record rejected/expired cargo for recovery (if still feasible) or safe disposal. A physically arrived but unsafe batch must never turn into a green “delivered safely” metric merely because the rider tapped a button.

### Test gates

- OpenAPI DTOs match Alembic/SQLAlchemy columns and generated TS types.
- All reason codes in failure matrix exist in enum.
- Complete lifecycle preserves quantity after 40 planned → 35 picked → 30 safely accepted → 5 still in custody / disposed according to explicit event.
- Delivery attempt after safe-by/deliver-by produces explicit unsafe result, never false success.
- Invalid user times or item categories are rejected with clear reason.

---

## 11. P0 FIX J — Fully automated judge demo and safe confirmation policy

### One-click full scenario

A seeded demo should allow clicking **Run scenario** once, then observing authentic domain changes without 5 separate human accept/pickup/confirm actions. Under the hood the simulator acts through the same real APIs/services used by human roles.

Example:

```text
seed 180 meals, riders R1–R6, shelters A–D
→ planner publishes offers
→ healthy simulated riders accept with staggered delays
→ actor schedules heartbeats and position anchors
→ auto pickup when physically arrived (uses real safety gate)
→ chaos event silences R3 only
→ R3's lease expires/revokes; backup promoted if valid
→ rider R1 picks up and starts delivery
→ shelter A receives walk-in / closes
→ recovery evaluates alternatives, commits or raises AT_RISK
→ auto delivery confirmation for simulated shelter at arrival
→ actual database balances and audit events verified
```

**Important:** Demo-mode simulated shelter auto-confirmation is a *scenario assumption*, labeled as such. In real mode, a timeout never silently counts unconfirmed aid as received; leave `DELIVERY_PENDING_CONFIRMATION` / disputed custody according to the state contract.

### UI controls

- Play / pause / advance / reset simulated clock; display scenario time and current speed.
- Rider list: `AUTO / SILENT / DELAYED / MANUAL`, last heartbeat, current simulated location, cargo count.
- Judge requirements panel: expiry, split capacity, silent rider, mid-transit closure, incompatible supplies, with one-click triggers and **actual** pass/fail assertions.
- Event timeline displays why each outcome happened, rather than static “success” cards.
- Free-play form for judge-defined quantities/deadlines/rider counts; seeded runs only for known demo narrative.
- Emit truthful `NO_FEASIBLE_DESTINATION` and unsafe handover states.

**Issue owners:** C5/C7, D3/D4, F4, I2/I3/I4.

---

## 12. P1 improvements worth carrying forward (do not block working P0)

### 12.1 Reactive repeated trips

After completed delivery and a verified rider-free transition, optionally queue `REDISPATCH_REMAINDER` if feasible donor-held stock remains. Also trigger on new rider availability, canceled unpicked-up leases, and reopened demand. The rider may return and accept a **new** lot only after prior physical cargo is cleared. Re-run ETA and expiry checks; no overlapping rider occupancy. This is not a pre-booked multi-trip optimization solver.

### 12.2 Near-expiry salvage ladder

Urgently publish offers to riders currently able to complete safely. If impossible, show donor a **feasibility-checked** self-delivery/on-site distribution suggestion only when operationally valid; otherwise mark quantity not safely deliverable and record eventual disposal/expiry truthfully. Never imply a public distribution site is verified unless it is an actual eligible receiver.

### 12.3 Baseline-versus-recovery-aware experiment

Define two deterministic planner modes over **identical inputs and failure schedules**:

- `NEAREST_FEASIBLE`: nearest currently eligible rider/destination without advance fallback preference.
- `RECOVERY_AWARE`: feasibility + deadline slack + fallback diversity and recovery policy.

Compare actual simulated `safely_delivered_qty`, `at_risk_qty`, `recovered_after_failure_qty`, decision latency and scenario failure count. Show results only after running both. Use multiple seeds, state assumptions, and report when there is no meaningful difference. Label all findings **simulation-only**.

### 12.4 Counterfactual explanations

“Blocked: shelter lacks verified fuel; becomes eligible if fuel capability is verified” and “Unsafe ETA: to meet deadline, need pickup within X minutes” are useful. Never suggest restoring capabilities is guaranteed or that unsafe formula/medicine can be moved without professional verification.

### 12.5 Safe-reach visual

Show a transparent ETA-derived *approximate* reachable area that shrinks with time. A naive kilometer circle is not proof of safe road delivery; mark as approximate when using distance/speed model.

### 12.6 Optional pickup/delivery proofs

Signed QR/OTP handoff, human shelter confirmation and replay protections are good P1 features. Simple button-based confirmation remains P0 for the hackathon.

### 12.7 Hosted demonstration and worker mode

Provide a reliable public URL **if deployment conditions permit**; otherwise provide a verified Docker Compose/local demo. A one-process `WORKER_MODE=inprocess` mode may run **exactly one worker scheduler** in the API process for single-instance demo hosting; don't enable multiple replicas or hot-reload duplication with uncontrolled background loops. Simulated scenarios are advanced only by scenario commands, while ordinary real-mode due jobs use wall time. Document auth, HTTPS and environment handling; don't expose chaos operations to ordinary users.

---

## 13. Revised GitHub issue mapping — THIS master already has an issue backlog

**Confirmed:** `ZeroSpoil_UNIFIED_MASTER_IMPLEMENTATION_PLAN.md` §13–§16 already contains **31 identified GitHub implementation/integration issues**, not just a conceptual grouping:

- **F0–F5:** 6 foundation issues.
- **A1–A4:** 4 resource/persistence issues.
- **B1–B5:** 5 dispatch/routing/rules issues.
- **C1–C6:** 6 operations/concurrency issues.
- **D1–D5:** 5 UI/demo issues.
- **I1–I5:** 5 integration/finalization issues.

**Total = 6 + 4 + 5 + 6 + 5 + 5 = 31.** §14 gives ownership, files, dependencies, implementation work and acceptance checks; §15 includes agent prompts and Git worktree instructions; §13 gives a dependency graph. These are **proposed issue texts**, not evidence that GitHub issues have already been created.

### Mandatory revisions to existing issues

| Existing issue | Amendment |
|---|---|
| F0 | Freeze renewed lease semantics, real vs demo confirmation policy, custody flags, and scope cut line. |
| F1 | Start skeleton API/UI immediately; add single-process local/optional hosted run mode with safe scheduler lifecycle. |
| F2 | Reconcile DTO/DDL/enums, scenario-scoped idempotency, flags, partial-delivery payloads, clock endpoint; regenerate TS API types. |
| F3 | Migrations for missing fields, exact active assignment uniqueness, scoped key indexes; agreed global lock order; add database integration test fixture. |
| F4 | Add seed SimulatedRiderAgent definitions, deterministic route anchors, reproducible scenario reset and simulated confirmation defaults. |
| F5 | Add smoke checks for the early thin slice, healthy time warp, two process SSE, race and reset tests. |
| A1 | Canonical custody/demand conservation, partial accepted/rejected accounting, statement of required repository lock helpers. |
| A2 | `ready_at`, pickup window and handling evidence validation; rider location/capacity source. |
| A3 | Walk-in quantity adjustment, closure while reserved, server-side revalidation trigger. |
| A4 | Reconciliation report for partial drops, scenario reset and cargo still with rider. |
| B1 | Exact feasibility/lease cutoff math, simulated-position ETA fixture, renewal boundary tests. |
| B2 | Canonical reason enums and conservative evidence policy alignment. |
| B3 | Accept planner rerun on released stock/free rider; don't count nonbinding offers as reserved. |
| B4 | Coordinated replacement destinations, pre-pickup retarget proposal, partial-alternative proposal with actual quantities and safe stop checks. |
| B5 | Keep CP-SAT strictly optional P2; if innovation evidence desired, prioritize simple nearest-feasible baseline comparison. |
| C1 | Canonical lifecycle/flags/occupancy; one transaction command framework; scenario-scope key dedupe; lease renewal semantics. |
| C2 | Full lock order; partial pickup/delivery; delivery-expiry check; rider occupancy after AT_RISK; conditional full-transaction retry. |
| C3 | Renew progress leases, distinguish false silence vs genuine no progress, ignore stale expiry jobs. |
| C4 | Before-pickup retarget, overcommitment ordering, coordinated competing fallbacks, sequential partial reroute if feasible. |
| C5 | Deterministic clock advance, reset all run-specific keys/events/jobs, actor-aware due work, demo clock API. |
| C6 | P0 database-cursor SSE instead of in-process worker publisher; optional outbox P1 only for third-party side effects. |
| D1 | Visible demo/live mode tags and clock. |
| D2 | Clear batch “expires in X” UI; errors for impossible/no rider/no demand. |
| D3 | Move rider positions from authoritative simulated anchors; risk flag, remaining custody, acknowledgement state. |
| D4 | Auto-run scenario controls, per-rider SILENT/DELAYED toggles, reset, judge compliance panel, free-play. |
| D5 | Keep one essential browser smoke test and repeatable demo; full suite P1. |
| I1 | Integrate full vertical slice **after** first tiny `I0` walking skeleton; reconcile real DB and UI contracts. |
| I2 | Healthy riders survive time warp, target ghost detected, lease renew/release correct, duplicate lease prevented. |
| I3 | Closure before/after pickup, competing fallback, partial-demand tests, rider remains occupied with remaining cargo. |
| I4 | Simulated auto-accept, pickup, confirmation and scenario reset; clean stream reconnect. |
| I5 | Repeated demo three times, DB concurrency smoke, known limitations, optional hosted URL and offline/local fallback. |

### Proposed new issues (keep count small)

#### `I0 — Walking skeleton in the first 15–20%` **[P0]**

**Owner:** Integrator + selected A/C/D leads.  
**Scope:** One seeded actor, one batch, one rider, one shelter, one short nonbinding offer, accepted lease, pickup, delivery, PostgreSQL quantity transitions, and visible UI result. It can start after *minimal* DTO + migration skeleton; do not wait for all resource screens or all B algorithms.  
**Dependencies:** F0, minimal F1/F2/F3, a thin A1 repo, a thin C1/C2 command path, one basic D page. Full A2/A3/B3 are **not** hard prerequisites for this skeleton.  
**Acceptance:** Clean reset then click/run → exactly one 40-unit donation safely delivered, five-bucket accounting valid, event timeline populated, no mock mutation.  
**Files:** Integrator owns cross-group wiring and one smoke fixture; do not take ownership of A/C/D internals.

#### `C7 — Deterministic simulated rider and shelter actors` **[P0]**

**Owner:** Group C subagent assigned a new isolated `backend/app/demo/actors.py`, `backend/app/demo/movement.py`, associated focused tests; coordinate with F4/C5.  
**Dependencies:** F2 event/DTO contract and F4 clock fixture; may implement against mock commands before C2 integration.  
**Work:** AUTO/SILENT/DELAYED/MANUAL modes, position_at, scheduled heartbeat, offer accept/pickup/deliver, shelter-confirm actions, generation fencing, no production side effects.  
**Acceptance:** Healthy auto actors survive a 900-second jump; silencing R3 only affects R3; no invented safe delivery; deterministic replay.

#### Optional `D6 — Judge proof / resilience comparison UI` **[P1]**

**Owner:** Group D agent, with a Group B baseline fixture.  
**Work:** One-click handout tests, pass/fail with event links, counterfactual reasons, matched-condition nearest-feasible vs recovery-aware chart.  
**Acceptance:** Every displayed metric comes from executed simulation/test results; no hardcoded green ticks.

### Updated dependency strategy

```text
F0 -> [F1 small skeleton, F2 minimal contracts, F4 actor fixtures, B1/B2 pure, D1 mocked]
     -> F3 minimal DB + A1 thin ledger + C1/C2 thin command path
     -> I0 WALKING SKELETON (target first 15–20% of sprint)
     -> expand A/B/C/D in parallel:
        C7 (sim actor) + C5 (clock) + C3 (leases) -> I2 ghost acceptance
        A3 (demand) + B4 (recovery) + C4 (transaction) -> I3 closure acceptance
        D3/D4 (views + chaos) + C6 (DB SSE) -> I4 live proof
     -> I5 stability freeze, demo rehearsal, hosted URL if available
```

Move C7 actor work into the **first** development wave as a pure simulation dependency; no need to wait until every backend service is integrated. Group C's main lifecycle service is one owner; a C7 subagent may use mock command ports until C2 completes.

### GitHub labels and recommended issue ordering

`P0`, `P1`, `P2`, `group:A`, `group:B`, `group:C`, `group:D`, `foundation`, `integration`, `blocked`, `contract-change`, `judge-scenario`, `demo`.  
Open/freeze **F0, F1, F2, F3, I0, C7** early; then execute all other P0 issues according to contract dependencies. Regular substantive commits should map naturally to issue IDs and PRs; never invent empty commit activity.

---

## 14. P0 acceptance and regression matrix after patch

Every case must have test setup, trigger, expected **state transitions and DB balances**, reason/event trail, and UI effect. Mark TESTED only if actually run.

| Case | Trigger | Required behavior |
|---|---|---|
| P0-01 | Create 180 meals with 75-minute scenario window | Feasibility, capacity-conserving proposed lots, truthful remainder |
| P0-02 | 40-unit rider / 50-unit shelter | No capacity overrun across many lots |
| P0-03 | Two concurrent accepts | One exclusive active lot lease, no double stock/demand commitment |
| P0-04 | Five healthy riders, advance 15 min | No false liveness failures; move/pickup/deliver according to schedule |
| P0-05 | Kill R3 heartbeat pre-pickup | Only R3 enters SUSPECT/revoked; donor stock reoffered if feasible |
| P0-06 | Long but progressing pickup ETA | Lease renewal works past old 10-minute cap |
| P0-07 | Rider stuck in traffic pre-pickup | New ETA triggers safe early revoke if infeasible |
| P0-08 | Shelter closes before pickup | Existing lease safely retargeted or revoked, never silently stranded |
| P0-09 | Shelter closes after pickup | Cargo remains with rider, alternate committed or AT_RISK alerted |
| P0-10 | Walk-in reduces 50-unit need by 30 | No false demand; commitments reconciled deterministically |
| P0-11 | Competing reroutes to one 30-unit fallback | Never overcommit fallback quantity |
| P0-12 | 40 carried, only 30 demand | Either explicit partial rescue 30+10 carried or truthful 40 AT_RISK under agreed cut line |
| P0-13 | Rider AT_RISK and another accept arrives | Rider cannot take new cargo |
| P0-14 | Partial pickup 40 proposed, 35 physically picked | 35 carried, five released/expired with exact audit |
| P0-15 | Partial destination acceptance | Confirm accepted quantity only, retain/dispose rejected balance |
| P0-16 | Delivery time after safe-by/deliver-by | Unsafe handover blocked, no increment in safely delivered quantity |
| P0-17 | Powder formula and no verified safe water | Hard reject; counterfactual only if explicitly valid |
| P0-18 | Fresh scenario reset | No stale idempotency/jobs/SSE event reuse |
| P0-19 | API/process restart with persisted event stream | SSE replay from `Last-Event-ID` works |
| P0-20 | Concurrency pickup-vs-watchdog | No duplicate/phantom physical custody |
| P0-21 | Unknown judge inputs: 3min, 1000 units, zero riders | Helpful valid infeasible responses, no crashes/phantom allocations |
| P0-22 | Clean clone and run documented commands | API, DB, web and scheduler start; core scenario reproducible |

**Performance is separate from correctness:** Suggested targets (e.g. small candidate plan under one second, reroute started in two seconds) are aspirations until measured on target hardware. Do not use unmeasured claims in demo or README.

---

## 15. Exact sections to patch in the v2.0 master

| Section in master | Required edit |
|---|---|
| §0, §1.3 | State offers are **nonbinding** until acceptance; remove contradictory “reserve then rider accepts” sentence. |
| §1.4, §3 | Update P0/P1 scope and innovation evidence limits. |
| §2.2/§2.3 | Choose single-process demo vs separate-process operation explicitly; SSE uses DB events; worker mode safe. |
| §4.2–§4.5 | Custody amounts, partial accepted/rejected balance, risk **flags**, pre-pickup retarget state and exact mapping. |
| §5.1/§5.2 | Feasibility and **renewable lease** protocol; separate backup cutoff. |
| §5.3–§5.5 | Re-dispatch when freed, competing fallback policy, optional partial reroute; baseline comparison P1. |
| §6.2/§6.3 | Complete missing columns and indexes, scenario-safe namespace, remove/optionalize outbox table. |
| §6.4 TX-1…TX-6 | One lock order, retarget/partial delivery/unsafe delivery checks, actor simulation uses real service commands. |
| §6.5/§6.6 | Scoped idempotency and replay-safe DB-cursor SSE. |
| §7.1–§7.5 | One exact REST/DTO contract, optional demo-only convenience behavior, explicit quantity requests and reason codes. |
| §8.1–§8.4 | SimRider action generation, pure position, lease renewal generation, reset/advance semantics. |
| §9 | Moving rider UI, scenario indicators, judge panel, explicit auto-confirm label. |
| §10/§11 | Repo ownership for actor and core command service, one source of truth for flags/codes. |
| §12–§16 | Add `I0`, `C7`, revise existing issue acceptance and dependencies, handoff prompts, merge waves. |
| §17/§18 | Replace tests based on stale statuses, add exact time-warp/partial/race cases, demote expensive load sweeps. |
| §19 | Optional hosted deployment + safe `WORKER_MODE` and exact P0 Compose fallback. |
| §20/§21 | Demo risk, physical custody, unverified safety/time assumptions, live proof and backup demo. |
| Appendix A/B/C | Canonical event/reason status lists, custody invariants, revised freeze and success checklist. |

**Consistency grep after edit:** search for `AT_RISK`, `REROUTING`, `SUSPECT`, `raw_ttl`, `lease_expires_at`, `outbox`, `sim_now`, `Idempotency-Key`, `ready_at`, `picked_qty`, `accepted_qty`, `F13`, `F19`, `F29`, `REROUTE_PENDING`. Every occurrence should match the revised canonical contract and priority tier.

---

## 16. Copy-ready instruction for the teammate's planning/coding LLM

> You are the ZeroSpoil master-plan reconciliation lead. Read `ZeroSpoil_UNIFIED_MASTER_IMPLEMENTATION_PLAN.md` in full and then this companion amendment file. Apply all P0 corrections to a **new** master v2.1 Markdown file while preserving the original FastAPI + React + PostgreSQL modular-monolith architecture, the four team groups, GitHub branch/worktree strategy, continuous integration and demo-oriented scope. Do not silently redesign technologies or remove existing correct functionality.
>
> Your highest priorities are: (1) deterministic simulated rider action/position generation; (2) renewable progress-aware leases and separate rescue cutoff; (3) canonical assignment/custody states so AT_RISK never frees carried stock; (4) shelter closure before pickup and coordinated demand reconciliation; (5) truthful partial deliveries/unsafe delivery; (6) scenario-scoped idempotency and complete reset; (7) Postgres-cursor SSE across processes; (8) transaction lock order plus bounded deadlock retries; (9) one unified DDL/DTO/event/reason contract; (10) first working vertical slice early. Implement sequential partial reroute only if it meets P0 correctness gates; otherwise explicitly retain the honest AT_RISK fallback and mark feature P1.
>
> Reconcile every affected section, table, sample API request, code sketch, diagram, SQL migration proposal, test matrix, GitHub issue, dependency edge, multi-agent prompt and demo script. Update existing issue IDs rather than duplicating work; add only `I0` (early walking skeleton), `C7` (simulated actors), and optionally `D6` (judge proof UI) when useful. Include a concise changelog, updated issue count and P0/P1 scope line. Make `F2` contract consistency and `F3` persistence matching explicit preconditions for multiagent coding. Leave Kafka, CP-SAT, Redis-backed distributed workers and heavy full suites out of P0.
>
> Return (a) new versioned master Markdown, (b) updated issue checklist/dependency graph, (c) a table with each review finding and its resolution, and (d) a proof checklist of tests to run. Never claim tests have passed without running them.

---

## 17. Decisions the team must explicitly record

- Confirm actual hackathon time available and teammates/agent concurrency; this affects whether sequential partial reroute is P0 or P1.
- Confirm whether public hosting is permitted, required, and reliable; local Docker remains backup.
- Freeze whether simulated rider auto-confirm applies only to preset scenarios and how real unconfirmed receipts are handled.
- Decide whether all physical lifecycle quantities are stored in a normalized movement table or as authoritative inventory balances plus auditable movements. **Do not maintain two conflicting sources of truth.** The v2.0 baseline uses authoritative balances + append-only events.
- Choose the exact lease renewal/grace timers and document them as **simulator policy values**, not field-validated probabilities.
- Record one canonical status flag schema and one canonical reason/event enum before parallel code generation.
- Decide exact safety handling for expired physical cargo, especially how post-arrival rejected goods remain accounted for and ultimately disposed.

### Final release gate

The revised plan is ready for implementation when: contracts and SQL agree; every P0 issue has owner/files/dependencies/tests; I0 is runnable early; targeted 15-minute time-warp does not falsely revoke healthy riders; no AT_RISK path frees a loaded rider; reset is repeatable; DB/SSE survives separate processes; and the four official judge disruptions can be reproduced through real business logic rather than animation-only demo data.

**This companion document reviews and directs changes. It does not claim the master file has already been updated or that ZeroSpoil has been implemented.**
