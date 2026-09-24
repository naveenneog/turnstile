"""Browser sign-in through the Azure CLI, for a tenant whose web sign-in has no consent yet.

The Azure CLI is pre-authorized on Turnstile's API, so its token needs no consent. That token
is exchanged for a single-use code, and the code for a session cookie. These tests pin who
may start that, that a code is stored hashed, works once and not after a minute, and that a
workload identity cannot open a browser session.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.api import app
from backend.http.dependencies import get_repository
from backend.http.session import get_auth_store, get_entra_access_verifier
from backend.services.auth_service import hash_session_token
from tests.platform.api.test_entra_bearer_admin import _configure, _token, client
from turnstile_core.config import get_settings

ORIGIN = {"Origin": "http://localhost:5173"}
VIEWER = "Turnstile.Viewer"
MANAGER = "Turnstile.Manager"


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    client.cookies.clear()
    for dependency in (get_auth_store, get_settings, get_entra_access_verifier, get_repository):
        app.dependency_overrides.pop(dependency, None)


def _begin(token: str | None) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/v1/auth/cli", headers=headers)


def _redeem(code: str) -> Any:
    return client.post("/api/v1/auth/code", headers=ORIGIN, json={"code": code})


def test_a_persons_token_gets_a_short_lived_code_stored_hashed() -> None:
    store = _configure(entra_viewer_role=VIEWER)
    response = _begin(_token(roles=[VIEWER]))

    assert response.status_code == 200
    body = response.json()
    [stored] = store.login_codes
    assert stored["code_sha256"] == hash_session_token(body["code"])
    assert body["code"] not in str(stored)
    lifetime = datetime.fromisoformat(body["expires_at"]) - datetime.now(UTC)
    assert timedelta(0) < lifetime <= timedelta(seconds=60)
    assert store.entra_upserts == [{"email": "admin@contoso.com", "role": "member"}]


def test_the_code_opens_one_session_and_only_once() -> None:
    store = _configure()
    code = _begin(_token()).json()["code"]

    first = _redeem(code)
    assert first.status_code == 200
    assert "set-cookie" in first.headers
    assert [session["method"] for session in store.sessions] == ["entra"]

    client.cookies.clear()
    second = _redeem(code)
    assert second.status_code == 401
    assert len(store.sessions) == 1


def test_an_expired_code_opens_nothing() -> None:
    store = _configure()
    code = _begin(_token()).json()["code"]
    store.login_codes[0]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)

    assert _redeem(code).status_code == 401
    assert store.sessions == []


def test_a_workload_identity_cannot_open_a_browser_session() -> None:
    store = _configure(entra_viewer_role=VIEWER)
    assert _begin(_token(delegated=False, roles=[VIEWER])).status_code == 403
    assert store.login_codes == []


def test_a_developer_without_a_console_role_gets_no_code() -> None:
    store = _configure(entra_viewer_role=VIEWER, entra_manager_role=MANAGER)
    assert _begin(_token(roles=[])).status_code == 403
    assert store.login_codes == []
    assert store.entra_upserts == []


def test_no_token_no_code() -> None:
    store = _configure()
    assert _begin(None).status_code == 401
    assert store.login_codes == []


def test_a_code_that_was_never_issued_opens_nothing() -> None:
    store = _configure()
    assert _redeem("x" * 43).status_code == 401
    assert store.sessions == []
