import { test } from "node:test"
import assert from "node:assert/strict"
import { canEditBudget, managerPageAllowed } from "../../../frontend/src/lib/manager-scope.ts"

const teamManager = { role: "member", manager_scope: { organizations: [], departments: [{ id: "team-a", parent_id: "unit-a", name: "A" }], writable_department_ids: [] } }
const unitManager = { role: "member", manager_scope: { ...teamManager.manager_scope, organizations: [{ id: "unit-a", name: "A" }], writable_department_ids: ["team-a"] } }
const row = (scope_type, scope_id, parent_scope_id = null) => ({ scope_type, scope_id, parent_scope_id })

test("manager navigation is an explicit allow list", () => {
  for (const page of ["finops-overview", "finops-analytics", "finops-trends", "finops-governance", "finops-requests", "budgets", "gateway-governance"])
    assert.equal(managerPageAllowed(page), true)
  for (const page of ["models", "settings", "assistant", "applications", "pinned-report", "copilot-requests", "finops-invoke", "new-future-page"])
    assert.equal(managerPageAllowed(page), false)
})

test("unit managers edit only child departments and scoped people", () => {
  assert.equal(canEditBudget(unitManager, row("organization", "unit-a")), false)
  assert.equal(canEditBudget(unitManager, row("department", "team-a", "unit-a")), true)
  assert.equal(canEditBudget(unitManager, row("department", "team-b", "unit-b")), false)
  assert.equal(canEditBudget(unitManager, row("user", "person", "team-a")), true)
  assert.equal(canEditBudget(unitManager, row("user", "other", "team-b")), false)
})

test("team managers edit people, never team or unit budgets", () => {
  assert.equal(canEditBudget(teamManager, row("department", "team-a", "unit-a")), false)
  assert.equal(canEditBudget(teamManager, row("user", "person", "team-a")), true)
  assert.equal(canEditBudget(teamManager, row("user", "other", "team-b")), false)
})

test("owners are unchanged; viewers, signed-out and empty managers cannot edit", () => {
  const target = row("organization", "unit-a")
  assert.equal(canEditBudget({ role: "owner" }, target), true)
  assert.equal(canEditBudget({ role: "member" }, target), false)
  assert.equal(canEditBudget(null, target), false)
  assert.equal(canEditBudget({ role: "member", manager_scope: { organizations: [], departments: [], writable_department_ids: [] } }, row("user", "person", "team-a")), false)
})
