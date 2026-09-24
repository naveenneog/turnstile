"""Gateway governance: the tiers a Claude gateway enforces, and applying saves to it.

A Claude gateway that takes its governance from Turnstile reads the organization catalog,
the budgets and these tiers through the API. Saving any of them here starts the gateway's
apply job, so the change reaches the gateway without anyone running a script.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from turnstile_core.config import get_settings
from turnstile_core.domain.gateway import tier_rows, tier_write_problems, tiers_response
from turnstile_core.domain.models import (
    GatewayApplyRequest,
    GatewayApplyStatus,
    GatewayTiersResponse,
    GatewayTiersWrite,
)
from turnstile_core.integrations.gateway_apply import GatewayApplyTrigger

from .dependencies import Repository
from .session import (
    CurrentSession,
    OwnerSession,
    require_allowed_write_origin,
    require_authenticated_session,
)


@lru_cache
def get_gateway_apply_trigger() -> GatewayApplyTrigger:
    return GatewayApplyTrigger(get_settings().gateway_apply_job_id)


GatewayApply = Annotated[GatewayApplyTrigger, Depends(get_gateway_apply_trigger)]


def request_gateway_apply(
    background: BackgroundTasks, apply: GatewayApplyTrigger, reason: str
) -> None:
    """Start the apply job after the response is sent, so a save never waits on Azure."""
    if apply.configured:
        background.add_task(apply.request, reason)


router = APIRouter(
    dependencies=[
        Depends(require_authenticated_session),
        Depends(require_allowed_write_origin),
    ]
)


@router.get("/api/v1/gateway-tiers", response_model=GatewayTiersResponse)
def get_gateway_tiers(repository: Repository, identity: CurrentSession) -> GatewayTiersResponse:
    return tiers_response(repository.gateway_tiers())


@router.put("/api/v1/gateway-tiers", response_model=GatewayTiersResponse)
def put_gateway_tiers(
    write: GatewayTiersWrite,
    repository: Repository,
    identity: OwnerSession,
    background: BackgroundTasks,
    apply: GatewayApply,
) -> GatewayTiersResponse:
    problems = tier_write_problems(write)
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    repository.replace_gateway_tiers(tier_rows(write), identity.email)
    request_gateway_apply(background, apply, f"tiers saved by {identity.email}")
    return tiers_response(repository.gateway_tiers())


@router.get("/api/v1/gateway-apply", response_model=GatewayApplyStatus)
def get_gateway_apply(identity: CurrentSession, apply: GatewayApply) -> GatewayApplyStatus:
    return apply.status()


@router.post("/api/v1/gateway-apply", response_model=GatewayApplyRequest)
def post_gateway_apply(identity: OwnerSession, apply: GatewayApply) -> GatewayApplyRequest:
    """Apply now, without a change: after a failed run, or to re-assert the gateway's state."""
    if not apply.configured:
        raise HTTPException(status_code=409, detail="No gateway apply job is configured")
    return apply.request(f"applied by {identity.email}")
