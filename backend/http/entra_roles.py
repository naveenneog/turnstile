"""Which console role an Entra app role signs in as.

One decision, used by the web sign-in and by bearer tokens alike, so the two cannot drift.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from turnstile_core.config import Settings

ConsoleRole = Literal["owner", "member"]


def console_role(settings: Settings, roles: Iterable[str]) -> ConsoleRole | None:
    """Owner for the admin role, Member for the viewer or manager role, otherwise None.

    Viewers read everything; manager-only Members are catalog-scoped. Anyone else -- a
    developer included -- does not sign in at all, and is refused before any account is
    written.
    """
    held = set(roles)
    if settings.entra_admin_role and settings.entra_admin_role in held:
        return "owner"
    readers = {role for role in (settings.entra_viewer_role, settings.entra_manager_role) if role}
    if held & readers:
        return "member"
    return None


def manager_groups(
    settings: Settings, roles: Iterable[str], groups: tuple[str, ...]
) -> tuple[str, ...] | None:
    """NULL is unrestricted; an empty tuple is a manager of nothing."""
    held = set(roles)
    if held & {settings.entra_admin_role, settings.entra_viewer_role}:
        return None
    if settings.entra_manager_role and settings.entra_manager_role in held:
        return groups
    return None
