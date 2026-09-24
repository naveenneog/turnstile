"""Starts a Claude gateway's apply job when governance is saved in Turnstile.

Turnstile does not write the gateway's configuration. It asks the gateway's own apply job --
an Azure Container Apps job that the gateway deployment owns -- to run now, and the job reads
the catalog, budgets and tiers back through this API and applies them. Turnstile's managed
identity holds one right on the gateway side: to start that job (Container Apps Jobs
Operator, scoped to the job). A save therefore takes effect without anyone running a script,
and Turnstile never holds write access to the gateway.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from ..domain.models import GatewayApplyExecution, GatewayApplyRequest, GatewayApplyStatus

ARM = "https://management.azure.com"
JOBS_API_VERSION = "2024-03-01"
MANAGED_IDENTITY_API_VERSION = "2019-08-01"


class TokenSource(Protocol):
    def get(self) -> str: ...


class ManagedIdentityToken:
    """An Azure Resource Manager token from App Service's managed identity endpoint."""

    def __init__(
        self,
        client: httpx.Client,
        endpoint: str | None = None,
        header: str | None = None,
    ) -> None:
        self._client = client
        self._endpoint = endpoint if endpoint is not None else os.environ.get("IDENTITY_ENDPOINT")
        self._header = header if header is not None else os.environ.get("IDENTITY_HEADER")
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - 300:
                return self._token
            if not self._endpoint or not self._header:
                raise RuntimeError(
                    "No managed identity: IDENTITY_ENDPOINT and IDENTITY_HEADER are not set"
                )
            response = self._client.get(
                self._endpoint,
                params={"resource": f"{ARM}/", "api-version": MANAGED_IDENTITY_API_VERSION},
                headers={"X-IDENTITY-HEADER": self._header},
                timeout=10,
            )
            response.raise_for_status()
            body = response.json()
            self._token = str(body["access_token"])
            self._expires_at = float(body.get("expires_on") or time.time() + 3000)
            return self._token


class GatewayApplyTrigger:
    def __init__(
        self,
        job_id: str | None,
        client: httpx.Client | None = None,
        token: TokenSource | None = None,
    ) -> None:
        self.job_id = job_id.strip() if job_id and job_id.strip() else None
        self._client = client or httpx.Client()
        self._token: TokenSource = token or ManagedIdentityToken(self._client)
        self._last: GatewayApplyRequest | None = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.job_id is not None

    def request(self, reason: str) -> GatewayApplyRequest:
        """Start the apply job now. Reported, never raised: a save must not fail because
        the gateway could not be reached; the status shows what happened."""
        now = datetime.now(UTC)
        if self.job_id is None:
            result = GatewayApplyRequest(
                requested_at=now,
                reason=reason,
                started=False,
                error="No gateway apply job is configured (GATEWAY_APPLY_JOB_ID)",
            )
        else:
            try:
                response = self._client.post(
                    f"{ARM}{self.job_id}/start",
                    params={"api-version": JOBS_API_VERSION},
                    headers={"Authorization": f"Bearer {self._token.get()}"},
                    json={},
                    timeout=30,
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"{response.status_code} {response.text[:300]}")
                result = GatewayApplyRequest(
                    requested_at=now,
                    reason=reason,
                    started=True,
                    execution=_execution_name(response),
                )
            except Exception as error:  # noqa: BLE001 - reported to the caller, see docstring
                result = GatewayApplyRequest(
                    requested_at=now, reason=reason, started=False, error=str(error)[:500]
                )
        with self._lock:
            self._last = result
        return result

    def status(self, limit: int = 5) -> GatewayApplyStatus:
        with self._lock:
            last = self._last
        if self.job_id is None:
            return GatewayApplyStatus(configured=False, last_request=last)
        try:
            response = self._client.get(
                f"{ARM}{self.job_id}/executions",
                params={"api-version": JOBS_API_VERSION},
                headers={"Authorization": f"Bearer {self._token.get()}"},
                timeout=20,
            )
            response.raise_for_status()
            items: list[dict[str, Any]] = list(response.json().get("value") or [])
        except Exception as error:  # noqa: BLE001 - reported to the caller
            return GatewayApplyStatus(
                configured=True, last_request=last, executions_error=str(error)[:300]
            )
        items.sort(key=lambda item: str(_properties(item).get("startTime") or ""), reverse=True)
        return GatewayApplyStatus(
            configured=True,
            last_request=last,
            executions=[
                GatewayApplyExecution(
                    name=str(item.get("name") or ""),
                    status=str(_properties(item).get("status") or "Unknown"),
                    started_at=_properties(item).get("startTime"),
                    ended_at=_properties(item).get("endTime"),
                )
                for item in items[:limit]
            ],
        )


def _properties(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("properties")
    return value if isinstance(value, dict) else {}


def _execution_name(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    name = body.get("name") if isinstance(body, dict) else None
    return str(name) if name else None
