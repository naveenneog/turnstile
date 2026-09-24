// Gateway governance: the business units, teams and tiers a Claude gateway takes from
// Turnstile. Saving here starts the gateway's apply job, so a change reaches the gateway
// without anyone running a script. Budgets stay on the budget page, which applies too.

import { request, writeJson } from "../../api/client"
import type { EnterpriseCatalog, EnterpriseCatalogWrite, GatewayTier } from "./gateway-governance-model"

export * from "./gateway-governance-model"

export type GatewayTiers = { items: GatewayTier[]; updated_at?: string | null; updated_by?: string | null }

export type GatewayApplyRequest = {
  requested_at: string
  reason: string
  started: boolean
  execution?: string | null
  error?: string | null
}

export type GatewayApplyStatus = {
  configured: boolean
  last_request?: GatewayApplyRequest | null
  executions: { name: string; status: string; started_at?: string | null; ended_at?: string | null }[]
  executions_error?: string | null
}

export const gatewayGovernanceApi = {
  catalog: () => request<EnterpriseCatalog>("/api/v1/enterprise-catalog"),
  saveCatalog: (value: EnterpriseCatalogWrite) =>
    writeJson<EnterpriseCatalog>("/api/v1/enterprise-catalog", value, "PUT"),
  tiers: () => request<GatewayTiers>("/api/v1/gateway-tiers"),
  saveTiers: (tiers: GatewayTier[]) => writeJson<GatewayTiers>("/api/v1/gateway-tiers", { tiers }, "PUT"),
  status: () => request<GatewayApplyStatus>("/api/v1/gateway-apply"),
  applyNow: () => writeJson<GatewayApplyRequest>("/api/v1/gateway-apply", {}, "POST"),
}

