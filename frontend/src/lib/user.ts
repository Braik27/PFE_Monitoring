/** Alignement champs utilisateur API Flask (session) avec le contexte React. */
export interface AppUser {
  username: string
  role: string
  name: string
  email?: string
  id?: number
  avatar?: string   // base64 data URL ou URL
  n_analyses?: number
}

export function normalizeApiUser(raw: Record<string, unknown> | null | undefined): AppUser | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  const username = String(o.username ?? '').trim()
  if (!username) return null
  const fullName = (o.full_name ?? o.name ?? username) as string
  return {
    username,
    role: String(o.role ?? 'analyst'),
    name: String(fullName || username),
    email: o.email != null ? String(o.email) : undefined,
    id: typeof o.id === 'number' ? o.id : o.id != null ? Number(o.id) : undefined,
    avatar: o.avatar != null ? String(o.avatar) : undefined,
    n_analyses: typeof o.n_analyses === 'number' ? o.n_analyses : undefined,
  }
}