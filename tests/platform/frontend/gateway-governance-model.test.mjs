// The editing rules behind the gateway governance page: business units, teams and tiers as
// an Owner changes them before saving. Run: node --experimental-strip-types --test <this file>
import { test } from "node:test"
import assert from "node:assert/strict"

import {
  editableCatalog,
  governanceOf,
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
  assert.ok(problemsFor(c, { kind: "unit", id: "sales_emea", name: "S", group: "g7" }).some((p) => p.includes("hyphens")))
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

const managerGroupId = "ABCDEFAB-1234-5678-90AB-ABCDEFABCDEF"
test("governance round trips exact attribute names and preserves unrelated attributes", () => {
  const existing = saveUnit(empty, { id: "sales", name: "Sales", group: "g1" })
  existing.organizations[0].attributes = { custom: "retained" }
  const change = { id: "sales", name: "Sales", group: "g1", managerGroupId, enforcement: "allowance", allowancePercent: 15 }
  let c = saveUnit(existing, change, "sales")
  assert.deepEqual(c.organizations[0].attributes, {
    custom: "retained", manager_group_id: managerGroupId.toLowerCase(),
    enforcement: "allowance", allowance_percent: 15,
  })
  assert.equal(existing.organizations[0].attributes.enforcement, undefined)
  assert.equal(governanceOf(c.organizations[0]).managerGroupId, managerGroupId.toLowerCase())
  c = saveUnit(c, { ...change, enforcement: "strict" }, "sales")
  assert.equal("allowance_percent" in c.organizations[0].attributes, false)
  c = saveUnit(c, { ...change, managerGroupId: "", enforcement: "" }, "sales")
  assert.deepEqual(c.organizations[0].attributes, { custom: "retained" })
})

test("teams save their own manager group and enforcement without changing membership", () => {
  let c = saveUnit(empty, { id: "sales", name: "Sales", group: "g1" })
  c = saveTeam(c, { id: "emea", name: "EMEA", group: "g2", parentId: "sales", managerGroupId, enforcement: "notify" })
  const team = c.departments.find((d) => d.id === "emea")
  assert.equal(team.external_ref, "entra-group:g2")
  assert.deepEqual(team.attributes, { manager_group_id: managerGroupId.toLowerCase(), enforcement: "notify" })
  assert.equal(c.organizations[0].attributes, undefined)
})

test("governance validation rejects names, malformed ids and fractional or missing allowance", () => {
  const base = { kind: "unit", id: "sales", name: "Sales", group: "g1" }
  for (const managerGroupId of ["group-name", "123", "{abcdefab-1234-5678-90ab-abcdefabcdef}"])
    assert.ok(problemsFor(empty, { ...base, managerGroupId }).some((p) => p.includes("object id")))
  for (const allowancePercent of [undefined, 0, 101, 1.5, NaN])
    assert.ok(problemsFor(empty, { ...base, enforcement: "allowance", allowancePercent }).some((p) => p.includes("whole percentage")))
  for (const allowancePercent of [1, 100])
    assert.deepEqual(problemsFor(empty, { ...base, managerGroupId, enforcement: "allowance", allowancePercent }), [])
})

test("governance does not silently exceed the catalog attribute limit", () => {
  const c = saveUnit(empty, { id: "sales", name: "Sales", group: "g1" })
  c.organizations[0].attributes = Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`key${i}`, i]))
  assert.ok(problemsFor(c, { kind: "unit", id: "sales", editing: "sales", name: "Sales", group: "g1", managerGroupId }).some((p) => p.includes("20 attributes")))
})
