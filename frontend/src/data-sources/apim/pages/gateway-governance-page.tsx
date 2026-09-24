import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Network,
  PanelLeft,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  X,
} from "lucide-react"

import { Button } from "../../../components/ui/button"
import { Input } from "../../../components/ui/input"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../../components/ui/dialog"
import { useAuth } from "../../../providers/auth-provider"
import {
  editableCatalog,
  gatewayGovernanceApi,
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
  type EnterpriseCatalogWrite,
  type GatewayApplyStatus,
  type GatewayTier,
  type GovernanceChange,
} from "../gateway-governance"
import "../../../styles/gateway-governance.css"

const keys = {
  catalog: ["gateway", "catalog"] as const,
  tiers: ["gateway", "tiers"] as const,
  status: ["gateway", "apply"] as const,
}

type Editing =
  | { kind: "unit"; id?: string }
  | { kind: "team"; id?: string }
  | { kind: "tier"; id: string }

function message(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function when(value?: string | null) {
  return value ? new Date(value).toLocaleString() : "--"
}

function ApplyStatus({ status, onApply, canManage, busy }: {
  status?: GatewayApplyStatus
  onApply: () => void
  canManage: boolean
  busy: boolean
}) {
  if (!status) return null
  if (!status.configured) {
    return <div className="gg-apply" data-state="off"><AlertTriangle size={14} />Not connected to a gateway: saves are stored here only. Connect the gateway to apply them.</div>
  }
  const latest = status.executions[0]
  const state = latest?.status === "Succeeded" ? "ok" : latest?.status === "Failed" ? "failed" : latest ? "running" : "idle"
  return <div className="gg-apply" data-state={state}>
    {state === "ok" && <CheckCircle2 size={14} />}
    {state === "running" && <RefreshCw className="spin" size={14} />}
    {state === "failed" && <AlertTriangle size={14} />}
    {state === "idle" && <Clock3 size={14} />}
    <span>
      {latest ? <>Gateway apply <b>{latest.status}</b>, started {when(latest.started_at)}</> : "No apply has run yet"}
      {status.last_request?.error && <> · last request failed: {status.last_request.error}</>}
    </span>
    {canManage && <Button size="sm" variant="outline" disabled={busy} onClick={onApply}><RefreshCw size={13} />Apply now</Button>}
  </div>
}

export function GatewayGovernancePage({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const canManage = user?.role === "owner"
  const [editing, setEditing] = useState<Editing | null>(null)
  const catalog = useQuery({ queryKey: keys.catalog, queryFn: gatewayGovernanceApi.catalog })
  const tiers = useQuery({ queryKey: keys.tiers, queryFn: gatewayGovernanceApi.tiers })
  const status = useQuery({
    queryKey: keys.status,
    queryFn: gatewayGovernanceApi.status,
    refetchInterval: (query) => {
      const latest = query.state.data?.executions[0]
      return latest && latest.status !== "Succeeded" && latest.status !== "Failed" ? 4000 : 20000
    },
  })
  const afterSave = () => {
    setEditing(null)
    // The apply job starts after the response; poll its status straight away.
    window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: keys.status }), 1500)
  }
  const saveCatalog = useMutation({
    mutationFn: (value: EnterpriseCatalogWrite) => gatewayGovernanceApi.saveCatalog(toWrite(value)),
    onSuccess: (data) => { queryClient.setQueryData(keys.catalog, data); afterSave() },
  })
  const saveTiers = useMutation({
    mutationFn: (value: GatewayTier[]) => gatewayGovernanceApi.saveTiers(value),
    onSuccess: (data) => { queryClient.setQueryData(keys.tiers, data); afterSave() },
  })
  const applyNow = useMutation({
    mutationFn: gatewayGovernanceApi.applyNow,
    onSettled: () => void queryClient.invalidateQueries({ queryKey: keys.status }),
  })
  const current = useMemo(() => (catalog.data ? editableCatalog(catalog.data) : null), [catalog.data])
  const units = current ? unitsOf(current) : []
  const teams = current ? teamsOf(current) : []
  const busy = saveCatalog.isPending || saveTiers.isPending
  const error = saveCatalog.error ?? saveTiers.error

  return <div className="finops-workspace gg-workspace">
    <header className="finops-header">
      <div><Button variant="ghost" size="icon-sm" className="finops-sidebar-trigger" aria-label="Toggle navigation" onClick={onToggleSidebar}><PanelLeft size={16} /></Button><span className="finops-header-icon"><Network size={17} /></span><h1>Gateway governance</h1></div>
      <Button variant="ghost" size="icon-sm" className="finops-header-refresh" aria-label="Refresh" onClick={() => void queryClient.invalidateQueries({ queryKey: ["gateway"] })}><RefreshCw size={15} /></Button>
    </header>
    <div className="finops-filterbar gg-filterbar">
      <ApplyStatus status={status.data} canManage={canManage} busy={applyNow.isPending} onApply={() => applyNow.mutate()} />
    </div>
    <div className="finops-scroll-region">
      <div className="finops-content gg-content">
        <p className="gg-lead">Business units, teams and tiers the Claude gateway enforces. Saving applies the change to the gateway at once. Monthly budgets are set on the Budget page, which applies the same way. Membership comes from each Entra group.</p>
        {(catalog.isLoading || tiers.isLoading) && <div className="finops-state"><RefreshCw className="spin" size={18} />Loading</div>}
        {(catalog.error || tiers.error) && <div className="finops-state error"><AlertTriangle size={18} /><b>Governance is unavailable</b><span>{message(catalog.error ?? tiers.error)}</span></div>}

        {current && <section className="gg-section">
          <div className="gg-section-head"><h2>Business units</h2>{canManage && <Button size="sm" onClick={() => setEditing({ kind: "unit" })}><Plus size={13} />Add business unit</Button>}</div>
          <table className="gg-table"><thead><tr><th>Business unit</th><th>Id</th><th>Entra group</th><th>Teams</th><th>Manager group / enforcement</th><th /></tr></thead><tbody>
            {units.length === 0 && <tr><td colSpan={6} className="gg-empty">No business units yet.</td></tr>}
            {units.map((u) => <tr key={u.id}><td>{u.name}</td><td><code>{u.id}</code></td><td>{u.group || "--"}</td><td>{u.teams}</td><td><GovernanceSummary value={governanceOf(current.organizations.find((o) => o.id === u.id))} /></td>
              <td className="gg-actions">{canManage && <Button variant="ghost" size="icon-sm" aria-label={`Edit ${u.name}`} onClick={() => setEditing({ kind: "unit", id: u.id })}><Pencil size={14} /></Button>}</td></tr>)}
          </tbody></table>
        </section>}

        {current && <section className="gg-section">
          <div className="gg-section-head"><h2>Teams</h2>{canManage && units.length > 0 && <Button size="sm" onClick={() => setEditing({ kind: "team" })}><Plus size={13} />Add team</Button>}</div>
          <table className="gg-table"><thead><tr><th>Team</th><th>Id</th><th>Business unit</th><th>Entra group</th><th>Manager group / enforcement</th><th /></tr></thead><tbody>
            {teams.length === 0 && <tr><td colSpan={6} className="gg-empty">No teams yet.</td></tr>}
            {teams.map((t) => <tr key={t.id}><td>{t.name}</td><td><code>{t.id}</code></td><td>{t.parentName}</td><td>{t.group || "--"}</td><td><GovernanceSummary value={governanceOf(current.departments.find((d) => d.id === t.id))} /></td>
              <td className="gg-actions">{canManage && <Button variant="ghost" size="icon-sm" aria-label={`Edit ${t.name}`} onClick={() => setEditing({ kind: "team", id: t.id })}><Pencil size={14} /></Button>}</td></tr>)}
          </tbody></table>
        </section>}

        {tiers.data && <section className="gg-section">
          <div className="gg-section-head"><h2>Tiers</h2></div>
          <table className="gg-table"><thead><tr><th>Tier</th><th>Entra group</th><th>Tokens per minute</th><th>Tokens per day</th><th>Models</th><th /></tr></thead><tbody>
            {tiers.data.items.length === 0 && <tr><td colSpan={6} className="gg-empty">No tiers yet. Connect the gateway to bring in its standard and premium tiers.</td></tr>}
            {tiers.data.items.map((t) => <tr key={t.id}><td>{t.name} <code>{t.id}</code></td><td>{t.entra_group}</td><td>{t.tokens_per_minute.toLocaleString()}</td><td>{t.tokens_per_day.toLocaleString()}</td><td>{t.models.length ? t.models.join(", ") : "All models"}</td>
              <td className="gg-actions">{canManage && <Button variant="ghost" size="icon-sm" aria-label={`Edit ${t.name}`} onClick={() => setEditing({ kind: "tier", id: t.id })}><Pencil size={14} /></Button>}</td></tr>)}
          </tbody></table>
        </section>}
      </div>
    </div>

    {canManage && editing && current && editing.kind !== "tier" && <StructureEditor
      key={`${editing.kind}:${editing.id ?? "new"}`}
      editing={editing}
      catalog={current}
      busy={busy}
      error={error ? message(error) : null}
      onClose={() => { if (!busy) setEditing(null) }}
      onSave={(next) => saveCatalog.mutate(next)}
    />}
    {canManage && editing?.kind === "tier" && tiers.data && <TierEditor
      key={editing.id}
      tier={tiers.data.items.find((t) => t.id === editing.id) as GatewayTier}
      busy={busy}
      error={error ? message(error) : null}
      onClose={() => { if (!busy) setEditing(null) }}
      onSave={(tier) => saveTiers.mutate(tiers.data.items.map((t) => (t.id === tier.id ? tier : t)))}
    />}
  </div>
}

function GovernanceSummary({ value }: { value: GovernanceChange }) {
  return <><code>{value.managerGroupId || "No manager group"}</code><br /><span>{value.enforcement || "Gateway default"}{value.enforcement === "allowance" ? ` (+${value.allowancePercent}%)` : ""}</span></>
}

function StructureEditor({ editing, catalog, busy, error, onClose, onSave }: {
  editing: { kind: "unit" | "team"; id?: string }
  catalog: EnterpriseCatalogWrite
  busy: boolean
  error: string | null
  onClose: () => void
  onSave: (next: EnterpriseCatalogWrite) => void
}) {
  const isUnit = editing.kind === "unit"
  const existing = isUnit
    ? unitsOf(catalog).find((u) => u.id === editing.id)
    : teamsOf(catalog).find((t) => t.id === editing.id)
  const [id, setId] = useState(existing?.id ?? "")
  const [name, setName] = useState(existing?.name ?? "")
  const [group, setGroup] = useState(existing?.group ?? "")
  const governance = governanceOf((isUnit ? catalog.organizations : catalog.departments).find((e) => e.id === editing.id))
  const [managerGroupId, setManagerGroupId] = useState(governance.managerGroupId ?? "")
  const [enforcement, setEnforcement] = useState(governance.enforcement ?? "")
  const [allowancePercent, setAllowancePercent] = useState(String(governance.allowancePercent ?? 10))
  const [parentId, setParentId] = useState((existing && "parentId" in existing ? existing.parentId : "") || unitsOf(catalog)[0]?.id || "")
  const [problems, setProblems] = useState<string[]>([])
  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const change = { id, name, group, parentId, managerGroupId, enforcement, allowancePercent: Number(allowancePercent) }
    const found = problemsFor(catalog, { ...change, kind: editing.kind, editing: editing.id })
    setProblems(found)
    if (found.length) return
    onSave(isUnit ? saveUnit(catalog, change, editing.id) : saveTeam(catalog, change, editing.id))
  }
  const remove = () => onSave(isUnit ? removeUnit(catalog, editing.id as string) : removeTeam(catalog, editing.id as string))
  const noun = isUnit ? "business unit" : "team"
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
    <DialogContent className="registry-editor-dialog" finalFocus={false}>
      <form className="registry-editor" onSubmit={submit}>
        <DialogHeader className="registry-editor-header">
          <DialogTitle>{editing.id ? `Edit ${noun}` : `Add ${noun}`}</DialogTitle>
          <DialogDescription>{isUnit ? "A business unit is charged for its members' Claude use, against its monthly budget." : "A team is a part of a business unit with a budget of its own."}</DialogDescription>
        </DialogHeader>
        <div className="registry-editor-body">
          <label className="registry-field"><span className="registry-field-label">Name</span><Input value={name} onChange={(e) => setName(e.target.value)} required autoFocus /></label>
          <label className="registry-field"><span className="registry-field-label">Id</span><Input value={id} onChange={(e) => setId(e.target.value.toLowerCase())} required /><small>Used in reports and budgets. Lower case, no spaces.</small></label>
          <label className="registry-field"><span className="registry-field-label">Entra group</span><Input value={group} onChange={(e) => setGroup(e.target.value)} required placeholder="claude-bu-sales" /><small>Its members belong to this {noun}. The gateway checks that the group exists.</small></label>
          <label className="registry-field"><span className="registry-field-label">Manager group object id</span><Input value={managerGroupId} onChange={(e) => setManagerGroupId(e.target.value)} placeholder="00000000-0000-0000-0000-000000000000" /><small>Optional. Assign this security group to the Turnstile enterprise application with Turnstile.Manager. It is separate from the membership group above.</small></label>
          <label className="registry-field"><span className="registry-field-label">Budget enforcement</span><select className="gg-select" value={enforcement} onChange={(e) => setEnforcement(e.target.value as NonNullable<GovernanceChange["enforcement"]>)}><option value="">Gateway default</option><option value="strict">Strict — stop at the budget</option><option value="allowance">Allowance — permit limited overage</option><option value="notify">Notify — report without blocking</option></select></label>
          {enforcement === "allowance" && <label className="registry-field"><span className="registry-field-label">Allowance percent</span><Input type="number" min={1} max={100} step={1} value={allowancePercent} onChange={(e) => setAllowancePercent(e.target.value)} required /><small>Whole percentage above the budget, from 1 to 100. Removed when another mode is selected.</small></label>}
          {!isUnit && <label className="registry-field"><span className="registry-field-label">Business unit</span>
            <select className="gg-select" value={parentId} onChange={(e) => setParentId(e.target.value)}>{unitsOf(catalog).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>}
          {(problems.length > 0 || error) && <div className="registry-error">{[...problems, ...(error ? [error] : [])].join(". ")}</div>}
        </div>
        <DialogClose render={<Button type="button" variant="ghost" size="icon-sm" className="registry-editor-close" disabled={busy} />}><X size={16} /><span className="sr-only">Close</span></DialogClose>
        <DialogFooter className="registry-editor-footer">
          <div>{editing.id && <Button type="button" variant="destructive" disabled={busy} onClick={remove}><Trash2 size={14} />Remove {noun}</Button>}</div>
          <div><DialogClose render={<Button type="button" variant="outline" disabled={busy} />}>Cancel</DialogClose><Button type="submit" disabled={busy}><Save size={14} />Save and apply</Button></div>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}

function TierEditor({ tier, busy, error, onClose, onSave }: {
  tier: GatewayTier
  busy: boolean
  error: string | null
  onClose: () => void
  onSave: (tier: GatewayTier) => void
}) {
  const [group, setGroup] = useState(tier.entra_group)
  const [perMinute, setPerMinute] = useState(String(tier.tokens_per_minute))
  const [perDay, setPerDay] = useState(String(tier.tokens_per_day))
  const [models, setModels] = useState(tier.models.join(", "))
  const [problem, setProblem] = useState<string | null>(null)
  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const minute = Number(perMinute)
    const day = Number(perDay)
    if (!group.trim()) return setProblem("Name the Entra group whose members hold this tier")
    if (!Number.isSafeInteger(minute) || minute < 1) return setProblem("Tokens per minute must be a whole number above zero")
    if (!Number.isSafeInteger(day) || day < 1) return setProblem("Tokens per day must be a whole number above zero")
    onSave({ ...tier, entra_group: group.trim(), tokens_per_minute: minute, tokens_per_day: day, models: parseModels(models) })
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
    <DialogContent className="registry-editor-dialog" finalFocus={false}>
      <form className="registry-editor" onSubmit={submit}>
        <DialogHeader className="registry-editor-header">
          <DialogTitle>Edit the {tier.name} tier</DialogTitle>
          <DialogDescription>Limits apply to each member of the tier's Entra group, on every request.</DialogDescription>
        </DialogHeader>
        <div className="registry-editor-body">
          <label className="registry-field"><span className="registry-field-label">Entra group</span><Input value={group} onChange={(e) => setGroup(e.target.value)} required autoFocus /></label>
          <label className="registry-field"><span className="registry-field-label">Tokens per minute</span><Input type="number" min="1" step="1" value={perMinute} onChange={(e) => setPerMinute(e.target.value)} required /></label>
          <label className="registry-field"><span className="registry-field-label">Tokens per day</span><Input type="number" min="1" step="1" value={perDay} onChange={(e) => setPerDay(e.target.value)} required /></label>
          <label className="registry-field"><span className="registry-field-label">Models</span><Input value={models} onChange={(e) => setModels(e.target.value)} placeholder="All models" /><small>Comma-separated. Empty allows every model the gateway serves.</small></label>
          {(problem || error) && <div className="registry-error">{problem ?? error}</div>}
        </div>
        <DialogClose render={<Button type="button" variant="ghost" size="icon-sm" className="registry-editor-close" disabled={busy} />}><X size={16} /><span className="sr-only">Close</span></DialogClose>
        <DialogFooter className="registry-editor-footer">
          <div />
          <div><DialogClose render={<Button type="button" variant="outline" disabled={busy} />}>Cancel</DialogClose><Button type="submit" disabled={busy}><Save size={14} />Save and apply</Button></div>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}
