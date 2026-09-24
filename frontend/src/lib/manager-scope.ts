import type { AuthUser } from "../api/auth.ts"

const managerPages = new Set([
  "finops-overview", "finops-analytics", "finops-trends", "finops-governance",
  "finops-requests", "budgets", "gateway-governance",
])

export function managerPageAllowed(page: string): boolean {
  return managerPages.has(page)
}

export function canEditBudget(
  user: AuthUser | null,
  item: { scope_type: string; scope_id: string; parent_scope_id: string | null },
): boolean {
  if (user?.role === "owner") return true
  const scope = user?.manager_scope
  if (!scope) return false
  if (item.scope_type === "department") return scope.writable_department_ids.includes(item.scope_id)
  return item.scope_type === "user" && scope.departments.some((team) => team.id === item.parent_scope_id)
}
