from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic.json_schema import models_json_schema

from app.contracts.schemas import (
    AcceptLotRequest,
    AcceptLotResponse,
    AssignmentSnapshot,
    BatchBalance,
    CreateBatchRequest,
    DeliveryConfirmationRequest,
    DemandSnapshot,
    ErrorDetail,
    ErrorEnvelope,
    EventEnvelope,
    OfferSnapshot,
    PickupRequest,
    ScenarioSnapshot,
    SimulatedRiderSnapshot,
)


CONTRACT_MODELS = (
    AcceptLotRequest,
    AcceptLotResponse,
    AssignmentSnapshot,
    BatchBalance,
    CreateBatchRequest,
    DeliveryConfirmationRequest,
    DemandSnapshot,
    ErrorDetail,
    ErrorEnvelope,
    EventEnvelope,
    OfferSnapshot,
    PickupRequest,
    ScenarioSnapshot,
    SimulatedRiderSnapshot,
)


def build_openapi_schema(app: FastAPI) -> dict[str, object]:
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    _, contract_definitions = models_json_schema(
        [(model, "serialization") for model in CONTRACT_MODELS],
        ref_template="#/components/schemas/{model}",
    )
    schemas = contract_definitions.get("$defs", {})
    schema.setdefault("components", {}).setdefault("schemas", {}).update(schemas)
    return schema
