"""Gateway governance: the tiers a Claude gateway enforces, and applying saves to it.

Turnstile holds the organization catalog, the budgets and, here, the tiers. A Claude gateway
that takes its governance from Turnstile reads all three through the API and applies them to
its own configuration. This module validates tiers and describes the apply request that a
save starts; it never enforces anything itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .models import GatewayTier, GatewayTiersResponse, GatewayTiersWrite


def tier_write_problems(write: GatewayTiersWrite) -> list[str]:
    """Errors a schema cannot express. Empty means the tiers can be stored."""
    problems: list[str] = []
    seen: set[str] = set()
    groups: dict[str, str] = {}
    for tier in write.tiers:
        if tier.id in seen:
            problems.append(f"Duplicate tier id: {tier.id}")
        seen.add(tier.id)
        key = tier.entra_group.strip().lower()
        if key in groups:
            problems.append(
                f"Tiers {groups[key]} and {tier.id} name the same Entra group, "
                f"{tier.entra_group}; a developer would hold both"
            )
        groups.setdefault(key, tier.id)
        if len(set(tier.models)) != len(tier.models):
            problems.append(f"{tier.id}: a model is listed twice")
    return problems


def tier_rows(write: GatewayTiersWrite) -> list[dict[str, Any]]:
    """The rows a write stores, in document order so the tiers read back as written."""
    return [
        {
            "tier_id": tier.id,
            "name": tier.name,
            "entra_group": tier.entra_group,
            "tokens_per_minute": tier.tokens_per_minute,
            "tokens_per_day": tier.tokens_per_day,
            "models": list(tier.models),
            "position": position,
        }
        for position, tier in enumerate(write.tiers)
    ]


def tiers_response(rows: Sequence[Mapping[str, Any]]) -> GatewayTiersResponse:
    ordered = sorted(rows, key=lambda row: int(row["position"]))
    items = [
        GatewayTier(
            id=str(row["tier_id"]),
            name=str(row["name"]),
            entra_group=str(row["entra_group"]),
            tokens_per_minute=int(row["tokens_per_minute"]),
            tokens_per_day=int(row["tokens_per_day"]),
            models=[str(m) for m in (row.get("models") or [])],
        )
        for row in ordered
    ]
    latest = max(ordered, key=lambda row: row["updated_at"]) if ordered else None
    return GatewayTiersResponse(
        items=items,
        updated_at=latest["updated_at"] if latest else None,
        updated_by=str(latest["updated_by"]) if latest else None,
    )
