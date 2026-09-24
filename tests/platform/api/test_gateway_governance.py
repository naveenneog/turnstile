"""Gateway governance: tiers, and a save starting the gateway's apply job.

A Claude gateway that takes its governance from Turnstile reads the catalog, budgets and tiers
back through the API. Saving any of them starts the gateway's apply job, so the change takes
effect without anyone running a script. These tests pin who may write, what is refused, which
saves start the job, and that the job is started the way Azure expects -- and never makes a
save fail when it cannot be.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api import app
from backend.http.dependencies import get_repository
from backend.http.gateway_governance import get_gateway_apply_trigger
from backend.http.service_dependencies import token_budget_service
from backend.http.session import SessionIdentity, require_authenticated_session
from turnstile_core.domain.gateway import tier_rows, tier_write_problems, tiers_response
from turnstile_core.domain.models import GatewayTiersWrite, TokenBudgetResponse
from turnstile_core.integrations.gateway_apply import GatewayApplyTrigger
from turnstile_core.persistence.in_memory import InMemoryRepository

client = TestClient(app)
ORIGIN = {"Origin": "http://localhost:5173"}
PERIOD = datetime.now(UTC).strftime("%Y-%m")
JOB = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.App/jobs/job-apply"

TIERS = {
    "tiers": [
        {
            "id": "standard",
            "name": "Standard",
            "entra_group": "claude-code-standard",
            "tokens_per_minute": 20000,
            "tokens_per_day": 500000,
            "models": [],
        },
        {
            "id": "premium",
            "name": "Premium",
            "entra_group": "claude-code-premium",
            "tokens_per_minute": 100000,
            "tokens_per_day": 5000000,
            "models": ["claude-opus-5", "claude-sonnet-5"],
        },
    ]
}
CATALOG = {
    "organizations": [
        {"id": "sales", "name": "Sales", "external_ref": "entra-group:claude-bu-sales"}
    ],
    "departments": [{"id": "sales", "name": "Sales (direct)", "parent_id": "sales"}],
}


def _write(document: dict[str, Any]) -> GatewayTiersWrite:
    return GatewayTiersWrite.model_validate(document)


# --- the domain ------------------------------------------------------------------------


def test_tiers_read_back_in_document_order() -> None:
    rows = [
        {**row, "updated_at": datetime.now(UTC), "updated_by": "owner@contoso.com"}
        for row in tier_rows(_write(TIERS))
    ]
    response = tiers_response(rows)
    assert [tier.id for tier in response.items] == ["standard", "premium"]
    assert response.items[1].models == ["claude-opus-5", "claude-sonnet-5"]
    assert response.updated_by == "owner@contoso.com"


def test_no_tiers_is_an_empty_response() -> None:
    response = tiers_response([])
    assert response.items == [] and response.updated_at is None


def test_two_tiers_cannot_name_one_group() -> None:
    document = {
        "tiers": [
            dict(TIERS["tiers"][0]),
            {**TIERS["tiers"][1], "entra_group": "Claude-Code-Standard"},
        ]
    }
    problems = tier_write_problems(_write(document))
    assert any("name the same Entra group" in p for p in problems)


def test_duplicate_ids_and_models_are_named() -> None:
    document = {"tiers": [dict(TIERS["tiers"][0]), {**TIERS["tiers"][0], "entra_group": "other"}]}
    assert any("Duplicate tier id: standard" in p for p in tier_write_problems(_write(document)))
    twice = {"tiers": [{**TIERS["tiers"][1], "models": ["claude-opus-5", "claude-opus-5"]}]}
    assert any("a model is listed twice" in p for p in tier_write_problems(_write(twice)))


@pytest.mark.parametrize(
    "change",
    [
        {"id": "Premium"},
        {"id": "has space"},
        {"tokens_per_minute": 0},
        {"tokens_per_day": 0},
        {"models": ["claude,opus"]},
        {"entra_group": ""},
    ],
)
def test_values_a_gateway_could_not_apply_are_refused(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _write({"tiers": [{**TIERS["tiers"][0], **change}]})


def test_an_empty_set_is_refused() -> None:
    with pytest.raises(ValueError):
        _write({"tiers": []})


# --- the API ---------------------------------------------------------------------------


class FakeTrigger:
    def __init__(self, configured: bool = True) -> None:
        self.configured = configured
        self.reasons: list[str] = []

    def request(self, reason: str) -> Any:
        self.reasons.append(reason)

    def status(self) -> Any:
        from turnstile_core.domain.models import GatewayApplyStatus

        return GatewayApplyStatus(configured=self.configured)


def _identity(role: str) -> SessionIdentity:
    return SessionIdentity(
        id="00000000-0000-4000-8000-000000000011",
        email=f"{role}@contoso.com",
        name=role,
        role=role,  # type: ignore[arg-type]
        method="entra",
        session_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


@pytest.fixture
def repository() -> Iterator[InMemoryRepository]:
    repo = InMemoryRepository()
    app.dependency_overrides[get_repository] = lambda: repo
    yield repo
    for dependency in (
        get_repository,
        require_authenticated_session,
        get_gateway_apply_trigger,
        token_budget_service,
    ):
        app.dependency_overrides.pop(dependency, None)


@pytest.fixture
def trigger() -> FakeTrigger:
    fake = FakeTrigger()
    app.dependency_overrides[get_gateway_apply_trigger] = lambda: fake
    return fake


def _as(role: str) -> None:
    app.dependency_overrides[require_authenticated_session] = lambda: _identity(role)


def test_anyone_signed_in_reads_the_tiers(
    repository: InMemoryRepository, trigger: FakeTrigger
) -> None:
    _as("member")
    response = client.get("/api/v1/gateway-tiers")
    assert response.status_code == 200 and response.json()["items"] == []


def test_only_an_owner_writes_them(repository: InMemoryRepository, trigger: FakeTrigger) -> None:
    _as("member")
    assert client.put("/api/v1/gateway-tiers", headers=ORIGIN, json=TIERS).status_code == 403
    assert repository.gateway_tiers() == [] and trigger.reasons == []


def test_an_owner_saves_them_and_the_gateway_is_asked_to_apply(
    repository: InMemoryRepository, trigger: FakeTrigger
) -> None:
    _as("owner")
    response = client.put("/api/v1/gateway-tiers", headers=ORIGIN, json=TIERS)
    assert response.status_code == 200
    body = client.get("/api/v1/gateway-tiers").json()
    assert [tier["id"] for tier in body["items"]] == ["standard", "premium"]
    assert body["updated_by"] == "owner@contoso.com"
    assert trigger.reasons == ["tiers saved by owner@contoso.com"]


def test_a_refused_save_changes_nothing_and_applies_nothing(
    repository: InMemoryRepository, trigger: FakeTrigger
) -> None:
    _as("owner")
    bad = {"tiers": [dict(TIERS["tiers"][0]), {**TIERS["tiers"][0], "entra_group": "x"}]}
    assert client.put("/api/v1/gateway-tiers", headers=ORIGIN, json=bad).status_code == 422
    assert repository.gateway_tiers() == [] and trigger.reasons == []


def test_saving_the_catalog_asks_the_gateway_to_apply(
    repository: InMemoryRepository, trigger: FakeTrigger
) -> None:
    _as("owner")
    assert client.put("/api/v1/enterprise-catalog", headers=ORIGIN, json=CATALOG).status_code == 200
    assert client.delete("/api/v1/enterprise-catalog", headers=ORIGIN).status_code == 200
    assert trigger.reasons == [
        "catalog saved by owner@contoso.com",
        "catalog reset by owner@contoso.com",
    ]


class FakeBudgetService:
    def _response(self) -> TokenBudgetResponse:
        today = date.today()
        return TokenBudgetResponse(
            period=PERIOD,
            period_start=today,
            period_end=today,
            generated_at=datetime.now(UTC),
            items=[],
            risk_count=0,
            risk_items=[],
            history=[],
            enforcement=[],
        )

    def save(self, *args: Any) -> TokenBudgetResponse:
        return self._response()

    def remove(self, *args: Any) -> TokenBudgetResponse:
        return self._response()


def test_unit_and_team_budgets_apply_but_person_budgets_do_not(
    repository: InMemoryRepository, trigger: FakeTrigger
) -> None:
    app.dependency_overrides[token_budget_service] = lambda: FakeBudgetService()
    _as("owner")
    body = {"token_limit": 1000, "warning_threshold_percent": 80}
    for path in ("organization/sales", "department/sales", "user/dev@contoso.com"):
        assert (
            client.put(
                f"/api/v1/budgets/{path}?period={PERIOD}", headers=ORIGIN, json=body
            ).status_code
            == 200
        )
    assert (
        client.delete(
            f"/api/v1/budgets/department/sales?period={PERIOD}", headers=ORIGIN
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/v1/budgets/user/dev@contoso.com?period={PERIOD}", headers=ORIGIN
        ).status_code
        == 200
    )
    assert trigger.reasons == [
        "organization budget sales saved by owner@contoso.com",
        "department budget sales saved by owner@contoso.com",
        "department budget sales removed by owner@contoso.com",
    ]


def test_without_an_apply_job_nothing_is_requested(repository: InMemoryRepository) -> None:
    fake = FakeTrigger(configured=False)
    app.dependency_overrides[get_gateway_apply_trigger] = lambda: fake
    _as("owner")
    assert client.put("/api/v1/gateway-tiers", headers=ORIGIN, json=TIERS).status_code == 200
    assert fake.reasons == []
    assert client.post("/api/v1/gateway-apply", headers=ORIGIN).status_code == 409


def test_only_an_owner_applies_by_hand(
    repository: InMemoryRepository, trigger: FakeTrigger
) -> None:
    _as("member")
    assert client.post("/api/v1/gateway-apply", headers=ORIGIN).status_code == 403
    assert client.get("/api/v1/gateway-apply").status_code == 200


# --- the month a gateway reads ---------------------------------------------------------


def _month_before(period_start: date) -> date:
    return (period_start - timedelta(days=1)).replace(day=1)


def test_prepare_gives_the_month_its_budgets_before_the_timer(
    repository: InMemoryRepository,
) -> None:
    # A new month has no budgets until the roll-forward; a gateway reading it then would
    # remove every budget. Prepare runs that roll-forward first, exactly once.
    current = datetime.now(UTC).date().replace(day=1)
    repository.upsert_token_budget(
        _month_before(current), "organization", "sales", None, 900, 80, "owner@contoso.com"
    )
    _as("owner")
    first = client.post("/api/v1/gateway-governance/prepare", headers=ORIGIN)
    assert first.status_code == 200
    assert first.json() == {"period": PERIOD, "inherited_now": True, "inherited_scopes": 1}
    assert repository.token_budgets[(current, "organization", "sales")]["token_limit"] == 900
    again = client.post("/api/v1/gateway-governance/prepare", headers=ORIGIN).json()
    assert again == {"period": PERIOD, "inherited_now": False, "inherited_scopes": None}


def test_prepare_never_brings_back_a_budget_removed_this_month(
    repository: InMemoryRepository,
) -> None:
    current = datetime.now(UTC).date().replace(day=1)
    repository.upsert_token_budget(
        _month_before(current), "organization", "sales", None, 900, 80, "owner@contoso.com"
    )
    _as("owner")
    client.post("/api/v1/gateway-governance/prepare", headers=ORIGIN)
    assert repository.delete_token_budget(current, "organization", "sales", "owner@contoso.com")
    client.post("/api/v1/gateway-governance/prepare", headers=ORIGIN)
    assert (current, "organization", "sales") not in repository.token_budgets


def test_only_an_owner_prepares(repository: InMemoryRepository) -> None:
    _as("member")
    assert client.post("/api/v1/gateway-governance/prepare", headers=ORIGIN).status_code == 403


# --- the trigger, against a fake Azure -------------------------------------------------


class StaticToken:
    def get(self) -> str:
        return "token-123"


class FailingToken:
    def get(self) -> str:
        raise RuntimeError("no managed identity")


def _trigger(
    handler: Callable[[httpx.Request], httpx.Response], token: Any = None
) -> tuple[GatewayApplyTrigger, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client_ = httpx.Client(transport=httpx.MockTransport(record))
    return GatewayApplyTrigger(JOB, client=client_, token=token or StaticToken()), seen


def test_the_job_is_started_through_resource_manager_with_the_identity() -> None:
    trigger_, seen = _trigger(lambda request: httpx.Response(202, json={"name": "job-apply-abc12"}))
    result = trigger_.request("tiers saved by owner@contoso.com")
    assert result.started and result.execution == "job-apply-abc12" and result.error is None
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url).startswith(
        f"https://management.azure.com{JOB}/start?api-version=2024-03-01"
    )
    assert request.headers["Authorization"] == "Bearer token-123"


def test_a_refused_start_is_reported_not_raised() -> None:
    trigger_, _ = _trigger(lambda request: httpx.Response(403, text="AuthorizationFailed"))
    result = trigger_.request("catalog saved")
    assert not result.started and "403" in (result.error or "")
    assert trigger_.status().last_request == result


def test_a_missing_identity_is_reported_not_raised() -> None:
    trigger_, seen = _trigger(lambda request: httpx.Response(202), token=FailingToken())
    result = trigger_.request("catalog saved")
    assert not result.started and "no managed identity" in (result.error or "") and seen == []


def test_no_job_means_nothing_is_called() -> None:
    trigger_ = GatewayApplyTrigger(
        None, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    assert not trigger_.configured
    assert not trigger_.request("x").started
    assert trigger_.status().configured is False


def test_status_lists_the_latest_runs_first() -> None:
    runs = {
        "value": [
            {
                "name": "older",
                "properties": {"status": "Succeeded", "startTime": "2026-09-24T01:00:00+00:00"},
            },
            {
                "name": "newer",
                "properties": {"status": "Running", "startTime": "2026-09-24T02:00:00+00:00"},
            },
        ]
    }
    trigger_, seen = _trigger(lambda request: httpx.Response(200, json=runs))
    status = trigger_.status()
    assert [run.name for run in status.executions] == ["newer", "older"]
    assert status.executions[0].status == "Running"
    assert str(seen[0].url).startswith(f"https://management.azure.com{JOB}/executions?")
