// The editing rules for gateway governance: business units, teams and tiers as the page
// changes them. Free of React and of the API client, so they run under node's test runner.

export type CatalogEntity = {
  id: string
  name: string
  parent_id?: string | null
  external_ref?: string | null
  attributes?: Record<string, string | number | boolean>
}

export type EnterpriseCatalog = {
  source: "configured" | "seeded"
  organizations: CatalogEntity[]
  departments: CatalogEntity[]
  default_department_id: string | null
  updated_at?: string | null
  updated_by?: string | null
}

export type EnterpriseCatalogWrite = {
  organizations: CatalogEntity[]
  departments: CatalogEntity[]
  default_department_id: string | null
}

export type GatewayTier = {
  id: string
  name: string
  entra_group: string
  tokens_per_minute: number
  tokens_per_day: number
  models: string[]
}

// --- editing rules, kept free of React so they can be tested on their own ----------------

const GROUP_PREFIX = "entra-group:"
export const UNASSIGNED_ID = "unassigned"
// The gateway's own rule: an id becomes a counter key and a map key, so letters, digits
// and hyphens only.
const ID = /^[a-z0-9][a-z0-9-]{0,63}$/
const OBJECT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export type GovernanceChange = {
  managerGroupId?: string
  enforcement?: "" | "strict" | "allowance" | "notify"
  allowancePercent?: number
}

export function governanceOf(entity?: CatalogEntity): GovernanceChange {
  return {
    managerGroupId: String(entity?.attributes?.manager_group_id ?? ""),
    enforcement: (entity?.attributes?.enforcement ?? "") as GovernanceChange["enforcement"],
    allowancePercent: Number(entity?.attributes?.allowance_percent ?? 10),
  }
}

function withGovernance(entity: CatalogEntity, change: GovernanceChange): CatalogEntity {
  if (change.managerGroupId === undefined && change.enforcement === undefined) return entity
  const attributes = { ...entity.attributes }
  if (change.managerGroupId !== undefined) {
    if (change.managerGroupId.trim()) attributes.manager_group_id = change.managerGroupId.trim().toLowerCase()
    else delete attributes.manager_group_id
  }
  if (change.enforcement !== undefined) {
    if (change.enforcement) attributes.enforcement = change.enforcement
    else delete attributes.enforcement
    if (change.enforcement === "allowance") attributes.allowance_percent = change.allowancePercent as number
    else delete attributes.allowance_percent
  }
  return { ...entity, attributes }
}

export function groupOf(entity: CatalogEntity): string {
  const ref = entity.external_ref ?? ""
  return ref.startsWith(GROUP_PREFIX) ? ref.slice(GROUP_PREFIX.length) : ref
}

export type UnitRow = { id: string; name: string; group: string; teams: number }
export type TeamRow = { id: string; name: string; group: string; parentId: string; parentName: string }

// A unit is an organization; its teams are departments under it. Every unit also has a
// department of its own id for the people mapped to it directly, which is not a team.
export function unitsOf(catalog: EnterpriseCatalogWrite): UnitRow[] {
  return catalog.organizations
    .filter((org) => org.id !== UNASSIGNED_ID)
    .map((org) => ({
      id: org.id,
      name: org.name,
      group: groupOf(org),
      teams: catalog.departments.filter((d) => d.parent_id === org.id && d.id !== org.id).length,
    }))
}

export function teamsOf(catalog: EnterpriseCatalogWrite): TeamRow[] {
  const names = new Map(catalog.organizations.map((org) => [org.id, org.name]))
  return catalog.departments
    .filter((d) => d.parent_id && d.id !== d.parent_id && d.parent_id !== UNASSIGNED_ID)
    .map((d) => ({
      id: d.id,
      name: d.name,
      group: groupOf(d),
      parentId: d.parent_id as string,
      parentName: names.get(d.parent_id as string) ?? (d.parent_id as string),
    }))
}

export function editableCatalog(catalog: EnterpriseCatalog): EnterpriseCatalogWrite {
  // The seeded demonstration catalog is not a starting point for a real one.
  if (catalog.source === "seeded") return { organizations: [], departments: [], default_department_id: null }
  return {
    organizations: catalog.organizations.map((o) => ({ ...o, parent_id: undefined })),
    departments: catalog.departments.map((d) => ({ ...d })),
    default_department_id: catalog.default_department_id,
  }
}

function strip(entity: CatalogEntity): CatalogEntity {
  const { parent_id, ...rest } = entity
  return parent_id == null ? rest : entity
}

export function toWrite(catalog: EnterpriseCatalogWrite): EnterpriseCatalogWrite {
  return {
    organizations: catalog.organizations.map(strip),
    departments: catalog.departments,
    default_department_id: catalog.default_department_id,
  }
}

export function problemsFor(
  catalog: EnterpriseCatalogWrite,
  change: { kind: "unit" | "team"; id: string; name: string; group: string; parentId?: string; editing?: string } & GovernanceChange,
): string[] {
  const problems: string[] = []
  if (!ID.test(change.id)) problems.push("Use lower-case letters, digits and hyphens for the id")
  if (!change.name.trim()) problems.push("Give it a name")
  if (!change.group.trim()) problems.push("Name the Entra group whose members belong to it")
  const taken = [...catalog.organizations, ...catalog.departments].some(
    (e) => e.id === change.id && e.id !== change.editing,
  )
  if (taken && change.editing !== change.id) problems.push(`The id ${change.id} is already used`)
  if (change.kind === "team" && !catalog.organizations.some((o) => o.id === change.parentId && o.id !== UNASSIGNED_ID))
    problems.push("Choose the business unit the team belongs to")
  const groupTaken = [...catalog.organizations, ...catalog.departments].some(
    (e) => e.id !== change.editing && groupOf(e).toLowerCase() === change.group.trim().toLowerCase(),
  )
  if (groupTaken) problems.push(`The group ${change.group.trim()} is already used by another unit or team`)
  if (change.managerGroupId?.trim() && !OBJECT_ID.test(change.managerGroupId.trim()))
    problems.push("Manager group must be an Entra group object id")
  if (change.enforcement && !["strict", "allowance", "notify"].includes(change.enforcement))
    problems.push("Choose strict, allowance or notify enforcement")
  if (change.enforcement === "allowance" && (!Number.isInteger(change.allowancePercent) || (change.allowancePercent ?? 0) < 1 || (change.allowancePercent ?? 0) > 100))
    problems.push("Allowance must be a whole percentage from 1 to 100")
  const original = (change.kind === "unit" ? catalog.organizations : catalog.departments).find((e) => e.id === change.editing)
  const updated = withGovernance(original ?? { id: change.id, name: change.name }, change)
  if (Object.keys(updated.attributes ?? {}).length > 20) problems.push("At most 20 attributes are allowed")
  return problems
}

export function saveUnit(
  catalog: EnterpriseCatalogWrite,
  unit: { id: string; name: string; group: string } & GovernanceChange,
  editing?: string,
): EnterpriseCatalogWrite {
  const entity: CatalogEntity = { id: unit.id, name: unit.name.trim(), external_ref: GROUP_PREFIX + unit.group.trim() }
  const organizations = editing
    ? catalog.organizations.map((o) => (o.id === editing ? withGovernance({ ...o, ...entity }, unit) : o))
    : [...catalog.organizations.filter((o) => o.id !== UNASSIGNED_ID), withGovernance(entity, unit), ...catalog.organizations.filter((o) => o.id === UNASSIGNED_ID)]
  let departments = catalog.departments
  if (editing && editing !== unit.id) {
    departments = departments.map((d) => ({
      ...d,
      id: d.id === editing ? unit.id : d.id,
      parent_id: d.parent_id === editing ? unit.id : d.parent_id,
    }))
  }
  const direct = departments.find((d) => d.id === unit.id && d.parent_id === unit.id)
  if (!direct) departments = [...departments, { id: unit.id, name: `${unit.name.trim()} (direct members)`, parent_id: unit.id }]
  else departments = departments.map((d) => (d === direct ? { ...d, name: `${unit.name.trim()} (direct members)` } : d))
  return { ...catalog, organizations, departments, default_department_id: catalog.default_department_id ?? unit.id }
}

export function removeUnit(catalog: EnterpriseCatalogWrite, id: string): EnterpriseCatalogWrite {
  const departments = catalog.departments.filter((d) => d.parent_id !== id && d.id !== id)
  const defaultStillThere = departments.some((d) => d.id === catalog.default_department_id)
  return {
    organizations: catalog.organizations.filter((o) => o.id !== id),
    departments,
    default_department_id: defaultStillThere ? catalog.default_department_id : departments[0]?.id ?? null,
  }
}

export function saveTeam(
  catalog: EnterpriseCatalogWrite,
  team: { id: string; name: string; group: string; parentId: string } & GovernanceChange,
  editing?: string,
): EnterpriseCatalogWrite {
  const entity: CatalogEntity = {
    id: team.id,
    name: team.name.trim(),
    parent_id: team.parentId,
    external_ref: GROUP_PREFIX + team.group.trim(),
  }
  const departments = editing
    ? catalog.departments.map((d) => (d.id === editing ? withGovernance({ ...d, ...entity }, team) : d))
    : [...catalog.departments, withGovernance(entity, team)]
  return { ...catalog, departments }
}

export function removeTeam(catalog: EnterpriseCatalogWrite, id: string): EnterpriseCatalogWrite {
  return { ...catalog, departments: catalog.departments.filter((d) => d.id !== id) }
}

// The gateway's allow list is comma-separated; an empty list means every model.
export function parseModels(text: string): string[] {
  return text.split(/[\s,]+/).map((m) => m.trim()).filter(Boolean)
}
