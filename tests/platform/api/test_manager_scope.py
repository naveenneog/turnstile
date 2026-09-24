from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.api import app, get_entra_verifier, protected
from backend.data_sources.github_copilot.router import protected as copilot_protected
from backend.http.assistant import title_router
from backend.http.dependencies import get_repository
from backend.http.session import MANAGER_READ_ROUTES
from backend.services.budget_service import TokenBudgetService
from tests.platform.api.test_entra_admin_only import RoleVerifier
from tests.platform.api.test_entra_admin_only import _token as id_token
from tests.platform.api.test_entra_admin_only import _verifier as id_verifier
from tests.platform.api.test_entra_bearer_admin import TENANT, _configure, _token, _verifier
from turnstile_core.domain.enterprise import catalog_rows, catalog_write_problems
from turnstile_core.domain.models import EnterpriseCatalogWrite, TokenBudgetWrite, TokenUsageRecord
from turnstile_core.persistence.in_memory import InMemoryRepository
from turnstile_core.persistence.repository import PostgreSqlOpsDbProxy, UsageFilters
from turnstile_core.persistence.repository_support import cache_scope_for_filters

UNIT_GROUP = "aaaaaaaa-1111-2222-3333-444444444444"
TEAM_GROUP = "bbbbbbbb-1111-2222-3333-444444444444"
MANAGER = "Turnstile.Manager"
VIEWER = "Turnstile.Viewer"
ADMIN = "Turnstile.Admin"
NOW = datetime.now(UTC)
PERIOD = NOW.strftime("%Y-%m")
WINDOW = {
    "from": (NOW - timedelta(days=1)).isoformat(),
    "to": (NOW + timedelta(days=1)).isoformat(),
}
CATALOG = {
    "organizations": [
        {"id": "unit-a", "name": "Unit A", "attributes": {"manager_group_id": UNIT_GROUP.upper()}},
        {"id": "unit-b", "name": "Secret Unit B"},
    ],
    "departments": [
        {"id": "unit-a", "name": "A direct", "parent_id": "unit-a"},
        {
            "id": "team-a",
            "name": "Team A",
            "parent_id": "unit-a",
            "attributes": {"manager_group_id": TEAM_GROUP},
        },
        {"id": "team-b", "name": "Secret Team B", "parent_id": "unit-b"},
    ],
    "default_department_id": "team-b",
}


def headers(*groups: str, roles: list[str] | None = None, **claims: Any) -> dict[str, str]:
    token = _token(roles=roles or [MANAGER], groups=list(groups), **claims)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def environment() -> Iterator[tuple[TestClient, InMemoryRepository, Any]]:
    old = dict(app.dependency_overrides)
    store = _configure(entra_manager_role=MANAGER, entra_viewer_role=VIEWER)
    repository = InMemoryRepository()
    repository.replace_enterprise_entities(
        catalog_rows(EnterpriseCatalogWrite.model_validate(CATALOG)), "owner"
    )
    repository.application_owner_rows = []
    repository.usage_records = [
        TokenUsageRecord(
            id=team,
            request_id=team,
            correlation_id=f"correlation-{team}",
            ts=NOW,
            team=team,
            organization=unit,
            organization_id=unit,
            department=team,
            department_id=team,
            user=f"{team}@contoso.com",
            user_id=f"{team}@contoso.com",
            agent="agent",
            workflow="flow",
            run_id=team,
            turn_index=1,
            provider="anthropic",
            model="claude",
            model_id="claude",
            input_tokens=20,
            cached_tokens=0,
            output_tokens=10,
            et=1,
            et_coeff_m=1,
            latency_ms=10000,
            status="error",
            status_code=500,
            estimated_cost=1,
            estimated=False,
            ingest_source="test",
        )
        for unit, team in (("unit-a", "unit-a"), ("unit-a", "team-a"), ("unit-b", "team-b"))
    ]
    service = TokenBudgetService(repository)
    for kind, ids, limit in (
        ("organization", ["unit-a", "unit-b"], 1000),
        ("department", ["unit-a", "team-a", "team-b"], 200),
        ("user", ["unit-a@contoso.com", "team-a@contoso.com", "team-b@contoso.com"], 50),
    ):
        for scope_id in ids:
            service.save(PERIOD, kind, scope_id, TokenBudgetWrite(token_limit=limit), "owner")
    app.dependency_overrides[get_repository] = lambda: repository
    client = TestClient(app)
    try:
        yield client, repository, store
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)


@pytest.mark.parametrize(
    "verifier,token", [(_verifier, _token), (id_verifier, lambda **kw: id_token(TENANT, **kw))]
)
@pytest.mark.parametrize(
    "claims,expected",
    [
        ({}, ()),
        (
            {"groups": [UNIT_GROUP.upper(), UNIT_GROUP, "not-an-id", 7, TEAM_GROUP]},
            (UNIT_GROUP, TEAM_GROUP),
        ),
        ({"groups": UNIT_GROUP}, ()),
        ({"groups": [UNIT_GROUP], "hasgroups": True}, ()),
        ({"groups": [UNIT_GROUP], "_claim_names": {"groups": "src1"}}, ()),
    ],
)
def test_both_verifiers_parse_only_usable_group_ids(
    verifier: Any, token: Any, claims: Any, expected: Any
) -> None:
    assert verifier().verify(token(**claims)).groups == expected


@pytest.mark.parametrize("roles", [[ADMIN], [VIEWER], [ADMIN, MANAGER], [VIEWER, MANAGER]])
def test_admin_and_viewer_precedence_remains_unrestricted(
    environment: Any, roles: list[str]
) -> None:
    client, _, _ = environment
    auth = headers(UNIT_GROUP, roles=roles)
    assert client.get("/api/v1/auth/me", headers=auth).json()["manager_scope"] is None
    response = client.get("/api/v1/enterprise-catalog", headers=auth)
    assert len(response.json()["organizations"]) == 2
    assert client.get("/api/v1/observability/overview", headers=auth).status_code == 200


@pytest.mark.parametrize(
    "groups,units,teams,writable",
    [
        ((UNIT_GROUP,), ["unit-a"], ["unit-a", "team-a"], ["team-a", "unit-a"]),
        ((TEAM_GROUP,), [], ["team-a"], []),
        ((UNIT_GROUP, TEAM_GROUP), ["unit-a"], ["unit-a", "team-a"], ["team-a", "unit-a"]),
        ((), [], [], []),
    ],
)
def test_profile_reports_exact_managed_scope(
    environment: Any, groups: Any, units: Any, teams: Any, writable: Any
) -> None:
    client, _, _ = environment
    response = client.get("/api/v1/auth/me", headers=headers(*groups))
    assert response.status_code == 200
    scope = response.json()["manager_scope"]
    assert [unit["id"] for unit in scope["organizations"]] == units
    assert [team["id"] for team in scope["departments"]] == teams
    assert scope["writable_department_ids"] == writable


@pytest.mark.parametrize("channel", ["entra", "cli"])
@pytest.mark.parametrize("groups", [(UNIT_GROUP,), ()])
def test_groups_survive_both_browser_login_paths(
    environment: Any, channel: str, groups: Any
) -> None:
    client, repository, store = environment
    if channel == "entra":
        app.dependency_overrides[get_entra_verifier] = lambda: RoleVerifier((MANAGER,), groups)
        signed_in = client.post("/api/v1/auth/entra", json={"id_token": "t"})
    else:
        begun = client.post("/api/v1/auth/cli", headers=headers(*groups))
        assert store.login_codes[0]["manager_group_ids"] == groups
        signed_in = client.post("/api/v1/auth/code", json={"code": begun.json()["code"]})
    assert signed_in.status_code == 200
    assert store.sessions[-1]["manager_group_ids"] == groups
    assert (
        signed_in.json()["manager_scope"] == client.get("/api/v1/auth/me").json()["manager_scope"]
    )
    assert client.get("/api/v1/observability/overview").status_code == 403
    # Change only the catalog: a still-valid cookie loses its scope on the next request.
    for row in repository.enterprise_entity_rows:
        row["attributes"] = {}
    assert client.get("/api/v1/auth/me").json()["manager_scope"]["departments"] == []


def test_password_sessions_stay_unrestricted(environment: Any) -> None:
    client, _, store = environment
    response = client.post(
        "/api/v1/auth/login", json={"email": "owner@contoso.com", "password": "correct-password"}
    )
    assert response.status_code == 200
    assert store.sessions[-1]["manager_group_ids"] is None
    assert client.get("/api/v1/auth/me").json()["manager_scope"] is None


@pytest.mark.parametrize("path", ["/api/v1/enterprise/entities", "/api/v1/enterprise-catalog"])
@pytest.mark.parametrize(
    "group,teams", [(UNIT_GROUP, {"unit-a", "team-a"}), (TEAM_GROUP, {"team-a"})]
)
def test_catalog_limits_teams_and_retains_context_unit(
    environment: Any, path: str, group: str, teams: set[str]
) -> None:
    client, _, _ = environment
    body = client.get(path, headers=headers(group)).json()
    assert {unit["id"] for unit in body["organizations"]} == {"unit-a"}
    assert {team["id"] for team in body["departments"]} == teams
    assert body["default_department_id"] is None
    assert "team-b" not in str(body)
    if "users" in body:
        assert {user["parent_id"] for user in body["users"]} <= teams


@pytest.mark.parametrize(
    "path",
    [
        "anomalies",
        "distribution",
        "executive-overview",
        "requests",
        "trends",
    ],
)
@pytest.mark.parametrize("groups,count", [((UNIT_GROUP,), 2), ((TEAM_GROUP,), 1), ((), 0)])
def test_all_usage_reads_filter_before_aggregation(
    environment: Any, path: str, groups: Any, count: int
) -> None:
    client, _, _ = environment
    response = client.get(
        f"/api/v1/observability/{path}",
        headers=headers(*groups),
        params={**WINDOW, "dimension": "department", "interval": "day", "group_by": "department"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "team-b" not in str(body)
    if path == "requests":
        assert len(body["items"]) == count
    if path == "distribution":
        assert sum(item["total_requests"] for item in body["items"]) == count
    if path == "executive-overview":
        assert body["totals"]["total_requests"] == count


@pytest.mark.parametrize(
    "key,value",
    [
        ("organization_id", "unit-b"),
        ("department_id", "team-b"),
        ("user_id", "team-b@contoso.com"),
        ("project_id", "outside"),
        ("agent_id", "outside"),
    ],
)
def test_explicit_outside_filters_are_forbidden(environment: Any, key: str, value: str) -> None:
    client, _, _ = environment
    assert (
        client.get(
            "/api/v1/observability/requests",
            headers=headers(UNIT_GROUP),
            params={**WINDOW, key: value},
        ).status_code
        == 403
    )


def test_team_manager_cannot_select_context_unit_as_managed(environment: Any) -> None:
    client, _, _ = environment
    assert (
        client.get(
            "/api/v1/observability/requests",
            headers=headers(TEAM_GROUP),
            params={**WINDOW, "organization_id": "unit-a"},
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "request_id,status",
    [
        ("team-a", 200),
        ("correlation-team-a", 200),
        ("team-b", 403),
        ("correlation-team-b", 403),
        ("missing", 404),
    ],
)
def test_request_detail_checks_attribution(environment: Any, request_id: str, status: int) -> None:
    client, _, _ = environment
    assert (
        client.get(
            f"/api/v1/observability/requests/{request_id}", headers=headers(TEAM_GROUP)
        ).status_code
        == status
    )


@pytest.mark.parametrize("group", [UNIT_GROUP, TEAM_GROUP])
def test_budget_lists_risks_history_and_enforcement_are_scoped(
    environment: Any, group: str
) -> None:
    client, _, _ = environment
    body = client.get(
        "/api/v1/budgets", headers=headers(group), params={"period": PERIOD, "include_users": True}
    ).json()
    assert "team-b" not in str(body) and "unit-b" not in str(body)
    assert body["risk_count"] == len(body["risk_items"])
    if group == TEAM_GROUP:
        assert all(item["scope_type"] != "organization" for item in body["items"])
    empty = client.get("/api/v1/budgets", headers=headers(), params={"period": PERIOD}).json()
    assert empty["items"] == empty["risk_items"] == empty["history"] == empty["enforcement"] == []
    assert empty["risk_count"] == 0


@pytest.mark.parametrize("department,status", [("team-a", 200), ("team-b", 403), ("missing", 403)])
def test_people_requires_an_in_scope_department(
    environment: Any, department: str, status: int
) -> None:
    client, _, _ = environment
    response = client.get(
        "/api/v1/budgets/users",
        headers=headers(TEAM_GROUP),
        params={"period": PERIOD, "department_id": department},
    )
    assert response.status_code == status


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize(
    "groups,scope_type,scope_id,status",
    [
        ((UNIT_GROUP,), "department", "team-a", 200),
        ((UNIT_GROUP,), "department", "unit-a", 200),
        ((TEAM_GROUP,), "department", "team-a", 403),
        ((UNIT_GROUP,), "department", "team-b", 403),
        ((UNIT_GROUP,), "organization", "unit-a", 403),
        ((UNIT_GROUP,), "user", "team-a@contoso.com", 200),
        ((TEAM_GROUP,), "user", "team-a@contoso.com", 200),
        ((TEAM_GROUP,), "user", "unit-a@contoso.com", 403),
        ((UNIT_GROUP,), "user", "team-b@contoso.com", 403),
        ((), "user", "team-a@contoso.com", 403),
    ],
)
def test_budget_write_boundaries(
    environment: Any, method: str, groups: Any, scope_type: str, scope_id: str, status: int
) -> None:
    client, repository, _ = environment
    if method == "DELETE" and scope_type == "department" and status == 200:
        TokenBudgetService(repository).remove(PERIOD, "user", f"{scope_id}@contoso.com", "owner")
    response = client.request(
        method,
        f"/api/v1/budgets/{scope_type}/{scope_id}",
        headers=headers(*groups),
        params={"period": PERIOD},
        json={"token_limit": 150},
    )
    assert response.status_code == status, response.text
    if status == 200:
        assert "team-b" not in response.text and "unit-b" not in response.text


@pytest.mark.parametrize(
    "group,kind,id_",
    [
        (UNIT_GROUP, "department", "team-a"),
        (TEAM_GROUP, "user", "team-a@contoso.com"),
    ],
)
def test_parent_allocation_constraint_still_applies(
    environment: Any, group: str, kind: str, id_: str
) -> None:
    client, _, _ = environment
    assert (
        client.put(
            f"/api/v1/budgets/{kind}/{id_}",
            headers=headers(group),
            params={"period": PERIOD},
            json={"token_limit": 10000},
        ).status_code
        == 409
    )


def test_viewer_cannot_write_person_budget(environment: Any) -> None:
    client, _, _ = environment
    assert (
        client.put(
            "/api/v1/budgets/user/team-a@contoso.com",
            headers=headers(TEAM_GROUP, roles=[VIEWER, MANAGER]),
            params={"period": PERIOD},
            json={"token_limit": 100},
        ).status_code
        == 403
    )


DENIED_ROUTES = [
    (method, route.path)
    for router in (protected, copilot_protected, title_router)
    for route in router.routes
    if isinstance(route, APIRoute)
    for method in sorted(route.methods)
    if not (method == "GET" and route.path in MANAGER_READ_ROUTES)
    and not (
        method in {"PUT", "DELETE"} and route.path == "/api/v1/budgets/{scope_type}/{scope_id}"
    )
]


@pytest.mark.parametrize("method,path", DENIED_ROUTES)
def test_every_other_protected_route_denies_managers_by_default(
    environment: Any, method: str, path: str
) -> None:
    import re

    client, _, _ = environment
    path = re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000001", path)
    response = client.request(method, path, headers=headers(UNIT_GROUP), json={})
    assert response.status_code == 403, response.text


@pytest.mark.parametrize(
    "path", ["/api/v1/gateway-tiers", "/api/v1/gateway-apply", "/api/v1/anomaly-rules"]
)
def test_configuration_reads_are_allowed(environment: Any, path: str) -> None:
    client, _, _ = environment
    assert client.get(path, headers=headers()).status_code == 200


def test_sql_scope_is_an_or_inside_normal_filters_and_empty_sets_deny_all() -> None:
    filters = UsageFilters(
        department_id="team-a",
        managed_organization_ids=frozenset({"unit-a"}),
        managed_department_ids=frozenset({"team-a", "unit-a"}),
    )
    sql, params = PostgreSqlOpsDbProxy._filter_sql(filters)
    assert "(usage.organization_id = ANY(%s) OR usage.department_id = ANY(%s))" in sql
    assert params[:2] == [["unit-a"], ["team-a", "unit-a"]]
    assert params[-1] == "team-a"
    empty = UsageFilters(managed_organization_ids=frozenset(), managed_department_ids=frozenset())
    assert PostgreSqlOpsDbProxy._filter_sql(empty)[1] == [[], []]
    assert cache_scope_for_filters(empty) is None
    assert cache_scope_for_filters(filters) is None
    assert cache_scope_for_filters(UsageFilters()) == ("global", ("all",))


@pytest.mark.parametrize(
    "attributes,valid",
    [
        ({"manager_group_id": UNIT_GROUP.upper()}, True),
        ({"manager_group_id": "group-name"}, False),
        ({"manager_group_id": 123}, False),
        ({"enforcement": "strict"}, True),
        ({"enforcement": "notify"}, True),
        ({"enforcement": "allowance", "allowance_percent": 1}, True),
        ({"enforcement": "allowance", "allowance_percent": 100}, True),
        ({"enforcement": "allowance"}, False),
        ({"enforcement": "allowance", "allowance_percent": True}, False),
        ({"enforcement": "allowance", "allowance_percent": 1.5}, False),
        ({"enforcement": "allowance", "allowance_percent": 101}, False),
        ({"enforcement": "strict", "allowance_percent": 10}, False),
        ({"enforcement": "audit"}, False),
    ],
)
def test_catalog_validates_governance_attributes(attributes: Any, valid: bool) -> None:
    write = EnterpriseCatalogWrite.model_validate(
        {"organizations": [{"id": "a", "name": "A", "attributes": attributes}]}
    )
    assert bool(catalog_write_problems(write)) is not valid
