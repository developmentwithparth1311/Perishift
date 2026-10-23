# API contract baseline

**Status:** F0 conventions frozen as a proposal; F2 owns the detailed DTO/OpenAPI freeze. Coordinator acknowledgements remain pending.  
**Authority:** This baseline follows the unified plan's API section and its v2 review amendment. Do not introduce incompatible hand-written frontend payloads.

## Wire conventions

- Versioned JSON API prefix: `/api/v1`.
- Pydantic v2 request/response models are the backend schema authority; export OpenAPI and generate or validate TypeScript client types from it.
- JSON keys use the `snake_case` names shown here. Entity IDs are UUID strings. Resource responses include an integer `version` for stale-write detection.
- Timestamps are ISO 8601 UTC with timezone (`Z`); naive timestamps are invalid. Server time is authoritative. Quantities are positive/nonnegative integers as appropriate, always in the declared item's unit. Durations are integer seconds, distances are meters, and coordinates are WGS84 latitude/longitude.
- Mutations require an authenticated actor, role authorization, and `Idempotency-Key`. Demo mutations also require a scenario ID and server-side scenario scope. Demo/chaos routes require `DEMO_MODE=true` and coordinator or judge authorization.
- Conflicts return HTTP 409 and a stable reason/error code; validation errors use the shared error shape. Never return success for an uncommitted or mock-only mutation.

## Error envelope

```json
{
  "error": {
    "code": "PLAN_STALE",
    "message": "The resource changed; refresh and retry.",
    "details": {},
    "retryable": true
  },
  "request_id": "opaque-request-id"
}
```

`details` and `retryable` may be omitted when not applicable. Codes are stable machine-readable values; clients must not infer business decisions from message text.

## Event envelope

```json
{
  "id": 1038,
  "scenario_id": "00000000-0000-0000-0000-000000000001",
  "type": "LEASE_REVOKED",
  "aggregate_type": "lot",
  "aggregate_id": "00000000-0000-0000-0000-000000000002",
  "occurred_at": "2026-10-23T13:17:25Z",
  "reason_code": "NO_PROGRESS",
  "payload": {"previous_state": "LEASED", "new_state": "OPEN", "qty": 40}
}
```

Event IDs are monotonically increasing cursors. Event rows commit atomically with their authoritative mutation. SSE consumers deduplicate by ID, reconnect with the last cursor, and refresh a snapshot on a detected gap or stale stream. Scenario-owned events and reads are always scoped to their scenario.

## Stable P0 endpoint inventory

| Method | Path | Authorized actor(s) | Contract boundary |
|---|---|---|---|
| GET | `/health/live` | Public/lightly gated | Process liveness only |
| GET | `/health/ready` | Internal | Database/migration readiness; worker health reported separately |
| POST / GET | `/batches`, `/batches/{id}` | Donor creates; donor/coordinator reads | Batch plus conserved quantity snapshot |
| POST | `/dispatch/preview` | Coordinator/donor | Pure proposal; no reservation or commitment |
| POST | `/dispatch/commit` | Coordinator | Publishes bounded nonbinding offers |
| GET | `/offers` | Rider | Eligible offers only |
| POST | `/lots/{id}/accept` | Rider | Exclusive lease; atomic revalidation |
| POST | `/lots/{id}/heartbeat` | Rider | Progress/liveness observation; does not change custody |
| POST | `/lots/{id}/release` | Rider | Safe release before pickup |
| POST | `/lots/{id}/pickup` | Rider/donor confirmation policy | Revalidate and move reserved quantity into rider custody |
| POST | `/lots/{id}/report-stuck` | Rider | Re-evaluate feasibility/recovery |
| POST | `/lots/{id}/deliver` | Rider/shelter confirmation policy | Confirm actual delivered quantity and custody change |
| POST / PATCH | `/demands`, `/demands/{id}` | Shelter | Item-specific demand change and commitment revalidation |
| POST | `/shelters/{id}/events` | Shelter | Walk-in, close/reopen, capability change |
| GET | `/control/state` | Coordinator/judge | Consistent authoritative snapshot |
| GET | `/control/events` | Coordinator/judge | Paginated persisted audit history |
| GET | `/events/stream` | Authenticated clients | Replayable SSE after cursor |
| POST | `/demo/scenarios/reset` | Judge/coordinator, demo mode | Isolated reset including scenario idempotency/dedupe state |
| POST | `/demo/clock/advance` | Judge/coordinator, demo mode | Deterministic scenario timeline advancement |
| POST | `/demo/chaos/{action}` | Judge/coordinator, demo mode | Named rider-silent, shelter-close, walk-in, traffic, or capability-loss action through normal domain services |

F2 may refine request/response fields and code catalogs while preserving these conventions, authority boundaries, and named routes. Any breaking route or envelope change requires coordinator review and updates to affected issues before parallel implementation continues.
