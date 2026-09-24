// The editing rules behind the gateway governance page: business units, teams and tiers as
// an Owner changes them before saving. Run: node --experimental-strip-types --test <this file>
import { test } from "node:test"
import assert from "node:assert/strict"

import {
  editableCatalog,
  parseModels,
  problemsFor,
  removeTeam,
  removeUnit,
  saveTeam,
  saveUnit,
  teamsOf,
  toWrite,
  unitsOf,
} from "../../../frontend/src/data-sources/apim/gateway-governance-model.ts"

const empty = { organizations: [], departments: [], default_department_id: null }

test("a new unit brings its own direct-members department and becomes the default", () => {
  const next = saveUnit(empty, { id: "sales", name: "Sales", group: "claude-bu-sales" })
  assert.deepEqual(next.organizations, [{ id: "sales", name: "Sales", external_ref: "entra-group:claude-bu-sales" }])
  assert.deepEqual(next.departments, [{ id: "sales", name: "Sales (direct members)", parent_id: "sales" }])
  assert.equal(next.default_department_id, "sales")
  assert.deepEqual(unitsOf(next), [{ id: "sales", name: "Sales", group: "claude-bu-sales", teams: 0 }])
  assert.deepEqual(teamsOf(next), [])
})

test("a team hangs under its unit and is counted there", () => {
  let c = saveUnit(empty, { id: "sales", name: "Sales", group: "claude-bu-sales" })
  c = saveTeam(c, { id: "sales-emea", name: "EMEA", group: "claude-team-emea", parentId: "sales" })
  assert.equal(unitsOf(c)[0].teams, 1)
  assert.deepEqual(teamsOf(c), [{ id: "sales-emea", name: "EMEA", group: "claude-team-emea", parentId: "sales", parentName: "Sales" }])
})

test("removing a unit removes its teams and its direct-members department", () => {
  let c = saveUnit(empty, { id: "sales", name: "Sales", group: "g1" })
  c = saveUnit(c, { id: "eng", name: "Engineering", group: "g2" })
  c = saveTeam(c, { id: "sales-emea", name: "EMEA", group: "g3", parentId: "sales" })
  c = removeUnit(c, "sales")
  assert.deepEqual(c.organizations.map((o) => o.id), ["eng"])
  assert.deepEqual(c.departments.map((d) => d.id), ["eng"])
  assert.equal(c.default_department_id, "eng")
})

test("renaming a unit's id carries its teams and direct department with it", () => {
  let c = saveUnit(empty, { id: "sales", name: "Sales", group: "g1" })
  c = saveTeam(c, { id: "sales-emea", name: "EMEA", group: "g3", parentId: "sales" })
  c = saveUnit(c, { id: "revenue", name: "Revenue", group: "g1" }, "sales")
  assert.deepEqual(c.organizations.map((o) => o.id), ["revenue"])
  assert.deepEqual(c.departments.map((d) => [d.id, d.parent_id]), [["revenue", "revenue"], ["sales-emea", "revenue"]])
})

test("removing a team leaves its unit", () => {
  let c = saveUnit(empty, { id: "sales", name: "Sales", group: "g1" })
  c = saveTeam(c, { id: "sales-emea", name: "EMEA", group: "g3", parentId: "sales" })
  assert.deepEqual(removeTeam(c, "sales-emea").departments.map((d) => d.id), ["sales"])
})

test("the unassigned organization is kept last and never shown as a unit", () => {
  const withUnassigned = {
    organizations: [{ id: "unassigned", name: "Unassigned" }],
    departments: [{ id: "unassigned", name: "Unassigned", parent_id: "unassigned" }],
    default_department_id: "unassigned",
  }
  const c = saveUnit(withUnassigned, { id: "sales", name: "Sales", group: "g1" })
  assert.deepEqual(c.organizations.map((o) => o.id), ["sales", "unassigned"])
  assert.deepEqual(unitsOf(c).map((u) => u.id), ["sales"])
  assert.deepEqual(teamsOf(c), [])
})

test("problems are named before anything is saved", () => {
  const c = saveUnit(empty, { id: "sales", name: "Sales", group: "claude-bu-sales" })
  const bad = problemsFor(c, { kind: "unit", id: "Has Space", name: " ", group: "" })
  assert.ok(bad.some((p) => p.includes("lower-case")))
  assert.ok(bad.some((p) => p.includes("Give it a name")))
  assert.ok(bad.some((p) => p.includes("Entra group")))
  assert.ok(problemsFor(c, { kind: "unit", id: "sales", name: "Again", group: "other" }).some((p) => p.includes("already used")))
  assert.ok(problemsFor(c, { kind: "unit", id: "eng", name: "Eng", group: "Claude-BU-Sales" }).some((p) => p.includes("already used by another")))
  assert.ok(problemsFor(c, { kind: "team", id: "t", name: "T", group: "g9", parentId: "nowhere" }).some((p) => p.includes("business unit")))
  assert.deepEqual(problemsFor(c, { kind: "unit", id: "sales", name: "Sales", group: "claude-bu-sales", editing: "sales" }), [])
})

test("a seeded catalog is not a starting point, and a write carries no parent on units", () => {
  assert.deepEqual(editableCatalog({ source: "seeded", organizations: [{ id: "x", name: "X" }], departments: [], default_department_id: null }), empty)
  const c = saveUnit(empty, { id: "sales", name: "Sales", group: "g1" })
  assert.equal("parent_id" in toWrite(c).organizations[0], false)
})

test("models are a comma or space separated list; empty means every model", () => {
  assert.deepEqual(parseModels("claude-opus-5, claude-sonnet-5"), ["claude-opus-5", "claude-sonnet-5"])
  assert.deepEqual(parseModels("  "), [])
})
