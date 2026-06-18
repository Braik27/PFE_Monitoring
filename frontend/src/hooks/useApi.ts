import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../lib/api'
import { normalizeApiUser } from '../lib/user'

/** Ligne renvoyée par GET /api/history → format attendu par le dashboard. */
export function mapHistoryRow(row: Record<string, unknown>) {
  const s = (row.summary as Record<string, unknown>) || {}
  const pairs = (s.pairs as Record<string, unknown>[]) || []
  const pair0 = pairs[0] || {}
  const dirRaw = String(pair0.direction ?? s.direction ?? 'EXPORT').toUpperCase()
  return {
    id: String(row.id ?? ''),
    flux_id: String(row.flux_id ?? s.flux_id ?? ''),
    flux_name: String(s.flux_name ?? row.flux_id ?? ''),
    division: String(s.division ?? (Array.isArray(s.divisions_found) ? (s.divisions_found as unknown[])[0] : '') ?? ''),
    direction: dirRaw === 'IMPORT' ? 'IMPORT' : 'EXPORT',
    label: String(row.label ?? s.label ?? ''),
    concordance_rate: Number(s.concordance_moyenne ?? pair0.concordance ?? 0),
    n_critiques: Number(s.total_critiques ?? pair0.n_critiques ?? 0),
    n_warnings: Number(s.total_warnings ?? pair0.n_warnings ?? 0),
    n_cegid: Number(pair0.n_cegid ?? 0),
    n_oracle: Number(pair0.n_oracle ?? 0),
    n_matched: Number(pair0.n_matched ?? 0),
    created_at: String(row.created_at ?? ''),
    analyst: String(s.analyst ?? ''),
    status: '',
  }
}

async function fetchHistoryMapped(params?: Record<string, string | number>) {
  const res = await api.get('/api/history', { params })
  const raw = Array.isArray(res.data) ? res.data : []
  return raw.map((r) => mapHistoryRow(r as Record<string, unknown>))
}

function mapFluxListItem(f: Record<string, unknown>) {
  return {
    ...f,
    name: f.name ?? f.flux_name,
    flux_name: f.flux_name ?? f.name,
    divisions: f.divisions ?? [],
  }
}

export function buildReportingFromApi(rows: Record<string, unknown>[]) {
  const by_flux: Record<string, Record<string, unknown>[]> = {}
  let total = 0
  for (const item of rows) {
    const name = String(item.flux_name ?? item.flux_id ?? 'Flux')
    total += Number(item.n_analyses ?? 0)
    const ds = (item.div_stats as Record<string, { n?: number; critiques?: number; warnings?: number }>) || {}
    const conc = Number(item.concordance_moy ?? 0)
    const tc = Number(item.total_critiques ?? 0)
    const tw = Number(item.total_warnings ?? 0)
    const inner = Object.keys(ds).length
      ? Object.entries(ds).map(([div, st]) => ({
          division: div,
          concordance_rate: conc,
          n_critiques: st.critiques ?? 0,
          n_warnings: st.warnings ?? 0,
        }))
      : [{ division: 'GLOBAL', concordance_rate: conc, n_critiques: tc, n_warnings: tw }]
    by_flux[name] = inner
  }
  return { by_flux, total, _raw: rows }
}

// ── useAnalyses ──────────────────────────────────────────────────────────
export function useAnalyses(filters?: { flux_id?: string; division?: string; limit?: number | string }) {
  return useQuery({
    queryKey: ['analyses', filters],
    queryFn: async () => {
      const params: Record<string, string | number> = {}
      if (filters?.flux_id) params.flux_id = filters.flux_id
      if (filters?.division) params.division = filters.division
      if (filters?.limit != null) params.limit = filters.limit
      return fetchHistoryMapped(params)
    },
    staleTime: 30_000,
    retry: 1,
  })
}

// ── useAlerts ────────────────────────────────────────────────────────────
export function useAlerts(filters?: { flux_id?: string; status?: string }) {
  return useQuery({
    queryKey: ['alerts', filters],
    queryFn: async () => {
      const res = await api.get('/api/alerts', { params: filters })
      return res.data.alerts ?? res.data ?? []
    },
    staleTime: 60_000,
    retry: 1,
  })
}

// ── useAlertDetail ───────────────────────────────────────────────────────
export function useAlertDetail(token: string, enabled = true) {
  return useQuery({
    queryKey: ['alert', token],
    queryFn: async () => {
      const res = await api.get(`/api/alerts/${token}`)
      return res.data
    },
    enabled,
    staleTime: 60_000,
    retry: 1,
  })
}

// ── useFluxes ────────────────────────────────────────────────────────────
export function useFluxes() {
  return useQuery({
    queryKey: ['fluxes'],
    queryFn: async () => {
      const res = await api.get('/api/flux')
      const arr = Array.isArray(res.data) ? res.data : []
      return arr.map((f) => mapFluxListItem(f as Record<string, unknown>))
    },
    staleTime: 60_000,
    retry: 1,
  })
}

// ── useLoginMutation ─────────────────────────────────────────────────────
export function useLoginMutation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({ username, password }: { username: string; password: string }) => {
      const res = await api.post('/api/login', { username, password })
      return res.data
    },
    onSuccess: (data) => {
      const u = normalizeApiUser((data as { user?: Record<string, unknown> }).user)
      if (u) sessionStorage.setItem('user', JSON.stringify(u))
      const token = (data as { token?: string }).token
      if (token) sessionStorage.setItem('token', token)
      queryClient.invalidateQueries({ queryKey: ['me'] })
    },
  })
}

// ── useLogoutMutation ────────────────────────────────────────────────────
export function useLogoutMutation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async () => {
      const res = await api.post('/api/logout')
      return res.data
    },
    onSuccess: () => {
      sessionStorage.clear()
      queryClient.clear()
    },
  })
}

// ── useMe ────────────────────────────────────────────────────────────────
export function useMe(enabled = true) {
  return useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      const res = await api.get('/api/me')
      return res.data
    },
    enabled,
    staleTime: 60_000,
    retry: 1,
  })
}

// ── useReporting ─────────────────────────────────────────────────────────
export function useReporting(period: string, divFilter?: string) {
  return useQuery({
    queryKey: ['reporting', period, divFilter],
    queryFn: async () => {
      const params: Record<string, string> = { period }
      if (divFilter) params.division = divFilter
      const res = await api.get('/api/reporting', { params })
      const rows = Array.isArray(res.data) ? (res.data as Record<string, unknown>[]) : []
      return buildReportingFromApi(rows)
    },
    staleTime: 30_000,
    retry: 1,
  })
}

// ── useHistory ───────────────────────────────────────────────────────────
export function useHistory(filters?: { flux_id?: string; division?: string }) {
  return useQuery({
    queryKey: ['history', filters],
    queryFn: async () => {
      const params: Record<string, string> = { limit: '100' }
      if (filters?.flux_id) params.flux_id = filters.flux_id
      if (filters?.division) params.division = filters.division
      const list = await fetchHistoryMapped(params)
      return { analyses: list, total: list.length }
    },
    staleTime: 60_000,
    retry: 1,
  })
}

// ── useAlertTracking ─────────────────────────────────────────────────────
export function useAlertTracking(token: string) {
  return useMutation({
    mutationFn: async ({ status, comment }: { status: string; comment?: string }) => {
      const res = await api.patch(`/api/alerts/${token}/status`, { status, comment })
      return res.data
    },
  })
}

// ── useResolveAlert ──────────────────────────────────────────────────────
export function useResolveAlert(token: string) {
  return useMutation({
    mutationFn: async ({ comment }: { comment?: string }) => {
      await api.patch(`/api/alerts/${token}/status`, { status: 'RESOLVED', comment })
    },
  })
}

// ── useSuggestIA ─────────────────────────────────────────────────────────
export function useSuggestIA(token: string) {
  return useMutation({
    mutationFn: async () => {
      const res = await api.post(`/api/alerts/${token}/suggest`)
      return res.data
    },
  })
}

// ── useFeedbackIA ────────────────────────────────────────────────────────
export function useFeedbackIA(token: string) {
  return useMutation({
    mutationFn: async ({ score, comment, action_taken }: {
      score: number; comment?: string; action_taken?: string
    }) => {
      const res = await api.post(`/api/alerts/${token}/feedback`, { score, comment, action_taken })
      return res.data
    },
  })
}

// ── useUserManagement ────────────────────────────────────────────────────
export function useUserManagement() {
  const queryClient = useQueryClient()

  const listUsers = useQuery({
    queryKey: ['users'],
    queryFn: async () => {
      const res = await api.get('/api/admin/users')
      return res.data.users ?? res.data ?? []
    },
    staleTime: 30_000,
  })

  const createUser = useMutation({
    mutationFn: async (form: Record<string, any>) => {
      const res = await api.post('/api/admin/users', form)
      return res.data
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  const deleteUser = useMutation({
    mutationFn: async (userId: number) => {
      await api.delete(`/api/admin/users/${userId}`)
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  return { listUsers, createUser, deleteUser }
}

// ── useFluxAdmin ─────────────────────────────────────────────────────────
export function useFluxAdmin() {
  const queryClient = useQueryClient()

  const listFluxes = useQuery({
    queryKey: ['fluxes-admin'],
    queryFn: async () => {
      const res = await api.get('/api/flux')
      const arr = Array.isArray(res.data) ? res.data : []
      return arr.map((f) => mapFluxListItem(f as Record<string, unknown>))
    },
    staleTime: 30_000,
  })

  const saveFlux = useMutation({
    mutationFn: async ({ form, editing }: { form: any; editing?: string }) => {
      const payload = { ...form, flux_name: form.flux_name ?? form.name }
      if (editing) {
        await api.put(`/api/flux/${editing}`, payload)
      } else {
        await api.post('/api/flux', payload)
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['fluxes-admin'] }),
  })

  const deleteFlux = useMutation({
    mutationFn: async (flux_id: string) => {
      await api.delete(`/api/flux/${flux_id}`)
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['fluxes-admin'] }),
  })

  return { listFluxes, saveFlux, deleteFlux }
}

// ── useChangePassword ────────────────────────────────────────────────────
export function useChangePassword() {
  return useMutation({
    mutationFn: async ({ old_password, new_password }: {
      old_password: string; new_password: string
    }) => {
      await api.put('/api/profile/password', {
        current_password: old_password,
        new_password,
      })
    },
  })
}