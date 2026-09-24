"""Catalog-resolved authorization, never a scope supplied by the browser."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from turnstile_core.domain.enterprise import (
    catalog_response,
    merge_application_owners,
    merge_observed_users,
    resolve_enterprise_catalog,
)
from turnstile_core.domain.models import (
    BudgetScopeType,
    EnterpriseCatalogResponse,
    EnterpriseEntity,
    EnterpriseEntityCatalog,
)
from turnstile_core.persistence.repository import QueryRepository

from .dependencies import Repository
from .session import CurrentSession


class ManagedScopeProfile(BaseModel):
    organizations: list[EnterpriseEntity]
    departments: list[EnterpriseEntity]
    writable_department_ids: list[str]


@dataclass(frozen=True)
class ManagerScope:
    organization_ids: frozenset[str]
    department_ids: frozenset[str]
    context_organization_ids: frozenset[str]
    writable_department_ids: frozenset[str]
    entities: EnterpriseEntityCatalog
    catalog: EnterpriseCatalogResponse

    @property
    def budget_scopes(self) -> dict[BudgetScopeType, frozenset[str]]:
        return {
            "organization": self.organization_ids,
            "department": self.department_ids,
            "user": frozenset(user.id for user in self.entities.users),
        }

    def profile(self) -> ManagedScopeProfile:
        return ManagedScopeProfile(
            organizations=[
                unit for unit in self.entities.organizations if unit.id in self.organization_ids
            ],
            departments=self.entities.departments,
            writable_department_ids=sorted(self.writable_department_ids),
        )

    def contains_usage(self, row: dict[str, Any]) -> bool:
        return (
            row.get("organization_id") in self.organization_ids
            or row.get("department_id") in self.department_ids
        )

    def require_department(self, department_id: str) -> None:
        if department_id not in self.department_ids:
            raise HTTPException(status_code=403, detail="Department is outside your managed scope")

    def require_budget_write(self, scope_type: BudgetScopeType, scope_id: str) -> None:
        allowed = (scope_type == "department" and scope_id in self.writable_department_ids) or (
            scope_type == "user" and scope_id in self.budget_scopes["user"]
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Budget is outside your writable scope")


def resolve_manager_scope(
    groups: tuple[str, ...] | None, repository: QueryRepository
) -> ManagerScope | None:
    if groups is None:
        return None
    rows = repository.enterprise_entities()
    catalog = catalog_response(rows)
    held = set(groups)

    def managed(entity: Any) -> bool:
        group = entity.attributes.get("manager_group_id")
        return isinstance(group, str) and group.lower() in held

    organizations = frozenset(unit.id for unit in catalog.organizations if managed(unit))
    departments = frozenset(
        team.id for team in catalog.departments if managed(team) or team.parent_id in organizations
    )
    context = organizations | frozenset(
        team.parent_id
        for team in catalog.departments
        if team.id in departments and team.parent_id is not None
    )
    writable = frozenset(team.id for team in catalog.departments if team.parent_id in organizations)
    entities = merge_application_owners(
        merge_observed_users(resolve_enterprise_catalog(rows), repository.observed_users()),
        repository.application_owners(),
    )
    projects = [project for project in entities.projects if project.parent_id in departments]
    project_ids = {project.id for project in projects}
    default = catalog.default_department_id
    filtered_entities = entities.model_copy(
        update={
            "organizations": [unit for unit in entities.organizations if unit.id in context],
            "departments": [team for team in entities.departments if team.id in departments],
            "users": [user for user in entities.users if user.parent_id in departments],
            "projects": projects,
            "agents": [agent for agent in entities.agents if agent.parent_id in project_ids],
            "invocation_testers": [],
            "default_department_id": default if default in departments else None,
        }
    )
    filtered_catalog = catalog.model_copy(
        update={
            "organizations": [unit for unit in catalog.organizations if unit.id in context],
            "departments": [team for team in catalog.departments if team.id in departments],
            "default_department_id": default if default in departments else None,
            "updated_at": None,
            "updated_by": None,
        }
    )
    return ManagerScope(
        organizations, departments, context, writable, filtered_entities, filtered_catalog
    )


def current_manager_scope(identity: CurrentSession, repository: Repository) -> ManagerScope | None:
    return resolve_manager_scope(identity.manager_group_ids, repository)


ScopedManager = Annotated[ManagerScope | None, Depends(current_manager_scope)]
