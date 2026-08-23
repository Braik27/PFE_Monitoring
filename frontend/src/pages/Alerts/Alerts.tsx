import { useEffect, useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'
import styles from './Alerts.module.css'

const STATUS_LABELS: Record<string, string> = {
  NEW:          '🟡 Nouveau',
  PENDING:      '🟡 Nouveau',
  ACKNOWLEDGED: '🔵 Pris en charge',
  IN_PROGRESS:  '🟣 En cours',
  ESCALATED:    '🟠 Escaladée',
  RESOLVED:     '🟢 Résolu',
  CLOSED:       '⚫ Clôturée',
  IGNORED:      '🚫 Ignoré',
}
const STATUS_BORDER: Record<string, string> = {
  NEW:          'var(--orange)',
  PENDING:      'var(--orange)',
  ACKNOWLEDGED: 'var(--blue)',
  IN_PROGRESS:  'var(--purple)',
  ESCALATED:    'var(--orange)',
  RESOLVED:     'var(--green)',
  CLOSED:       'var(--mut)',
  IGNORED:      'var(--mut)',
}

// ✅ Vrai structure retournée par le backend (generic_comparator.py)
interface Anomaly {
  error_type:  string        // ex: "MANQUANT_ORACLE", "ECART_montant"
  severity:    string        // "CRITIQUE" | "WARNING"
  key_str?:    string        // "article=X | site=Y"
  key_values?: Record<string, string>
  val_cegid?:  string | null
  val_oracle?: string | null
  explication?: string | null
}

/** Entrée d'historique — ⚠️ inféré du rendu (tracking list), pas d'un contrat backend. */
interface TrackingEntry {
  action?: string
  status?: string
  username?: string
  user?: string
  comment?: string
  at?: string
  created_at?: string
}

/**
 * Réponse de GET /api/alerts/<token>/suggest — ⚠️ inféré du rendu du panneau
 * « Suggestion IA », pas d'un contrat backend typé.
 */
interface AiSuggestion {
  diagnostic?: string
  ia_analyse?: string
  ia_actions?: string[]
  actions?: string[]
  ia_prevention?: string
  prevention?: string
  confidence?: number
  urgence?: string
  ia_enrichi?: boolean
}

interface FluxOptionRow { flux_id?: string }

interface Alert {
  id: string; token: string; flux_name: string; flux_id: string; division: string
  status: string; n_critiques: number; concordance: number; n_warnings: number
  created_at: string; label?: string; analyst?: string; tracking?: TrackingEntry[]
  ai_suggestion?: unknown; anomalies?: Anomaly[]
  sla_deadline?: string | null
  sla_hours?: number | null
  remaining_pct?: number | null
  sla_breached?: number | boolean
  sla_warning_sent?: number | boolean
  concordance_state?: string | null
  escalated_to?: string | null
  escalated_by?: string | null
  resolved_by?: string | null
}


const DEFAULT_SLA_MS = 4 * 3600 * 1000 // fallback 4h si champs API absents

function fmtCountdown(ms: number) {
  if (ms <= 0) return 'DÉPASSÉ'
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  const s = Math.floor((ms % 60000) / 1000)
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
}

function SlaPanel({ alert }: { alert: Alert }) {
  const [remaining, setRemaining] = useState(0)
  const [totalMs, setTotalMs] = useState(DEFAULT_SLA_MS)

  // RESOLVED/CLOSED : valeurs figées en base au moment de la résolution (storage.set_resolved)
  const frozen = alert.status === 'RESOLVED' || alert.status === 'CLOSED'

  useEffect(() => {
    if (frozen) return
    const createdAt = new Date(alert.created_at).getTime()
    const deadlineMs = alert.sla_deadline
      ? new Date(alert.sla_deadline).getTime()
      : createdAt + DEFAULT_SLA_MS
    const duration = alert.sla_hours
      ? alert.sla_hours * 3600000
      : (deadlineMs - createdAt) || DEFAULT_SLA_MS

    setTotalMs(duration > 0 ? duration : DEFAULT_SLA_MS)

    const tick = () => {
      const now = Date.now()
      if (alert.sla_deadline) {
        setRemaining(new Date(alert.sla_deadline).getTime() - now)
      } else {
        setRemaining(DEFAULT_SLA_MS - (now - createdAt))
      }
    }
    tick()
    const iv = setInterval(tick, 1000)
    return () => clearInterval(iv)
  }, [frozen, alert.created_at, alert.sla_deadline, alert.sla_hours, alert.remaining_pct])

  const slaHoursLabel = alert.sla_hours ? `${alert.sla_hours}h` : '4h'

  // Alerte résolue/clôturée : affichage statique du fait accompli, pas de compte à rebours
  if (frozen) {
    const pctVal = typeof alert.remaining_pct === 'number' ? Math.max(0, Math.min(100, alert.remaining_pct)) : null
    const pctLabel = pctVal != null ? `${pctVal}%` : null
    const msg = alert.sla_breached
      ? `Alerte ${alert.status === 'CLOSED' ? 'clôturée' : 'résolue'} après dépassement du SLA de ${slaHoursLabel}.`
      : pctLabel != null
        ? `Alerte ${alert.status === 'CLOSED' ? 'clôturée' : 'résolue'} — il restait ${pctLabel} du SLA (${slaHoursLabel}) à la résolution.`
        : `Alerte ${alert.status === 'CLOSED' ? 'clôturée' : 'résolue'} — SLA de ${slaHoursLabel}.`
    return (
      <div style={{ background: 'var(--s2)', border: '1.5px solid var(--brd)', borderRadius: 10, padding: '14px 16px', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--txt2)' }}>
            ⏱ SLA {slaHoursLabel} — terminé
          </span>
          <span style={{
            fontWeight: 700, fontSize: 13, padding: '3px 10px', borderRadius: 20,
            background: alert.sla_breached ? 'var(--red-lt)' : 'var(--grn-lt)',
            color: alert.sla_breached ? 'var(--red)' : 'var(--green)',
          }}>
            {alert.sla_breached ? 'DÉPASSÉ' : pctLabel != null ? `${pctLabel} restant` : '—'}
          </span>
        </div>
        <div style={{ height: 6, background: 'var(--brd)', borderRadius: 99, marginBottom: 4 }}>
          <div style={{ height: '100%', borderRadius: 99, background: alert.sla_breached ? 'var(--red)' : 'var(--green)', width: (pctVal ?? 0)+'%' }} />
        </div>
        <div style={{ fontSize: 11, color: 'var(--mut)' }}>{msg}</div>
      </div>
    )
  }

  const breached = Boolean(alert.sla_breached) || remaining <= 0
  const pct  = totalMs > 0 ? Math.max(0, Math.min(100, (remaining / totalMs) * 100)) : 0
  const over = breached
  const near = !over && remaining < 3600000
  const barBg = over ? 'var(--red)' : near ? 'var(--orange)' : 'var(--green)'
  const cls   = over ? { background: 'var(--red)', color: '#fff' }
               : near ? { background: 'var(--red-lt)', color: 'var(--red)' }
               : { background: 'var(--grn-lt)', color: 'var(--green)' }
  const msg = over ? '⚠ SLA dépassé — action immédiate requise.'
             : near ? "Moins d'1h restante — correction urgente nécessaire."
             : `Temps restant pour respecter le SLA de ${slaHoursLabel}.`

  return (
    <div style={{ background: 'var(--s2)', border: '1.5px solid var(--brd)', borderRadius: 10, padding: '14px 16px', marginBottom: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--txt2)' }}>
          ⏱ Temps restant pour résoudre (SLA {slaHoursLabel})
          {alert.sla_deadline ? '' : ' — défaut'}
        </span>
        <span style={{ ...cls, fontWeight: 700, fontSize: 13, padding: '3px 10px', borderRadius: 20 }}>
          {fmtCountdown(remaining)}
          {over && ' '}
          {over && <span style={{ background: 'var(--red)', color: '#fff', borderRadius: 20, fontSize: 11, padding: '2px 8px', marginLeft: 4 }}>DÉPASSÉ</span>}
        </span>
      </div>
      <div style={{ height: 6, background: 'var(--brd)', borderRadius: 99, marginBottom: 4 }}>
        <div style={{ height: '100%', borderRadius: 99, background: barBg, width: pct+'%', transition: 'width 1s, background .5s' }} />
      </div>
      <div style={{ fontSize: 11, color: 'var(--mut)' }}>{msg}</div>
    </div>
  )
}

export default function Alerts() {
  const { showToast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [fluxFilter, setFluxFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [fluxOptions, setFluxOptions] = useState<string[]>([])
  const [selected, setSelected] = useState<Alert | null>(null)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiData, setAiData] = useState<AiSuggestion | null>(null)
  const [resolveComment, setResolveComment] = useState('')
  const [resolveModal, setResolveModal] = useState(false)
  const [escalateModal, setEscalateModal] = useState(false)
  const [escalateEmail, setEscalateEmail] = useState('')
  const [escalateReason, setEscalateReason] = useState('')
  const [escalateComment, setEscalateComment] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<Alert | null>(null)
  const [deleting, setDeleting] = useState(false)

  const loadFluxOptions = useCallback(async () => {
    try {
      const res = await api.get<FluxOptionRow[] | { fluxes?: FluxOptionRow[]; data?: FluxOptionRow[] }>('/api/flux')
      const data = res.data
      const pickIds = (rows: FluxOptionRow[]) =>
        rows.map(f => f.flux_id).filter((id): id is string => Boolean(id))
      const fluxes: string[] = Array.isArray(data)
        ? pickIds(data)
        : data?.fluxes
          ? pickIds(data.fluxes)
          : data?.data
            ? pickIds(data.data)
            : []
      setFluxOptions(fluxes)
    } catch {
      setFluxOptions([])
    }
  }, [])

  useEffect(() => { loadFluxOptions() }, [loadFluxOptions])

  const load = useCallback(async () => {
    try {
      const params: Record<string, string> = {}
      if (fluxFilter) params.flux_id = fluxFilter
      if (statusFilter === 'CLOSED') params.archived = '1'
      const res = await api.get('/api/alerts', { params })
      let list: Alert[] = res.data.alerts ?? res.data ?? []
      if (statusFilter && statusFilter !== 'CLOSED') {
        list = list.filter(a => a.status === statusFilter)
      }
      setAlerts(list)
    } catch { showToast('Erreur chargement alertes', 'error') }
  }, [fluxFilter, statusFilter, showToast])

  useEffect(() => { load() }, [load])

  // Auto-select alert from URL token param, then clear URL
  useEffect(() => {
    const token = searchParams.get('token')
    if (token) {
      setSearchParams({}, { replace: true })
      const findAndSelect = async () => {
        try {
          const res = await api.get(`/api/alerts/${token}`)
          if (res.data) {
            setAlerts(prev => {
              const existing = prev.find(a => a.token === token)
              return existing ? prev : [...prev, res.data]
            })
            setSelected(res.data)
          }
        } catch { /* token may not be valid */ }
      }
      findAndSelect()
    }
  }, [searchParams, setSearchParams])

  const selectAlert = async (a: Alert) => {
    setAiData(null)
    try {
      const res = await api.get(`/api/alerts/${a.token}`)
      setSelected({ ...a, ...res.data })
    } catch {
      setSelected(a)
    }
  }

  const trackAlert = async (status: string) => {
    if (!selected) return
    try {
      await api.patch(`/api/alerts/${selected.token}/status`, { status })
      showToast(`Statut mis à jour : ${STATUS_LABELS[status] ?? status}`, 'success')
      load()
      setSelected(s => s ? { ...s, status } : s)
    } catch (err) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Erreur mise à jour'
      showToast(msg, 'error')
    }
  }

  const resolveAlert = async () => {
    if (!selected) return
    try {
      await api.post(`/api/alerts/${selected.token}/resolve`, { comment: resolveComment.trim() })
      showToast('Alerte résolue', 'success')
      setResolveModal(false); setResolveComment('')
      load(); setSelected(null)
    } catch (err) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Erreur résolution'
      showToast(msg, 'error')
    }
  }

  const escalateAlert = async () => {
    if (!selected) return
    try {
      await api.post(`/api/alerts/${selected.token}/escalate`, {
        assign_to_email: escalateEmail,
        reason: escalateReason,
        comment: escalateComment,
      })
      showToast('Alerte escaladée', 'success')
      setEscalateModal(false); setEscalateEmail(''); setEscalateReason(''); setEscalateComment('')
    } catch (err) {
      showToast((err as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Erreur escalade', 'error')
    }
  }

  const deleteAlert = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await api.delete(`/api/alerts/${deleteTarget.token}`)
      showToast('Alerte supprimée', 'success')
      if (selected?.token === deleteTarget.token) setSelected(null)
      setDeleteTarget(null)
      load()
    } catch (err) {
      showToast((err as { response?: { data?: { error?: string } } })?.response?.data?.error || 'Erreur suppression', 'error')
    }
    finally { setDeleting(false) }
  }

  // ✅ CORRIGÉ : le backend expose GET (pas POST) /api/alerts/<token>/suggest
  const suggestIA = async () => {
    if (!selected) return
    setAiLoading(true); setAiData(null)
    try {
      const res = await api.get(`/api/alerts/${selected.token}/suggest`)
      setAiData(res.data.suggestion ?? res.data)
    } catch { showToast('IA non disponible', 'error') }
    finally { setAiLoading(false) }
  }

  // ✅ Normalise la sévérité pour l'affichage (backend retourne "CRITIQUE" ou "WARNING")
  const severityBadge = (sev: string) => {
    const s = (sev ?? '').toUpperCase()
    if (s === 'CRITIQUE' || s === 'CRITICAL') return 'b-r'
    return 'b-o'
  }
  const severityLabel = (sev: string) => {
    const s = (sev ?? '').toUpperCase()
    if (s === 'CRITIQUE' || s === 'CRITICAL') return 'CRITIQUE'
    return 'WARNING'
  }

  // Concordance state badge (0-70% = CRITIQUE, >70-<80% = ATTENTION, >=80% = NORMAL)
  const concordanceBadge = (state: string | null | undefined, concordance: number) => {
    const s = (state ?? '').toUpperCase()
    if (s === 'CRITIQUE') return { cls: 'b-r', label: '🔴 CRITIQUE' }
    if (s === 'ATTENTION') return { cls: 'b-o', label: '🟠 ATTENTION' }
    if (s === 'NORMAL') return { cls: 'b-g', label: '🟢 NORMAL' }
    // Fallback based on numeric value
    if (concordance < 70) return { cls: 'b-r', label: '🔴 CRITIQUE' }
    if (concordance < 80) return { cls: 'b-o', label: '🟠 ATTENTION' }
    return { cls: 'b-g', label: '🟢 NORMAL' }
  }

  return (
    <>
          {/* Filters */}
      <div className={styles.filters}>
        <select value={fluxFilter} onChange={e => setFluxFilter(e.target.value)} aria-label="Filtrer par flux">
          <option value="">Tous les flux</option>
          {fluxOptions.map(f => <option key={f} value={f}>{f}</option>)}
        </select>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} aria-label="Filtrer par statut">
          <option value="">Tous statuts</option>
          <option value="NEW">🟡 Nouveau</option>
          <option value="ACKNOWLEDGED">🔵 Pris en charge</option>
          <option value="IN_PROGRESS">🟣 En cours</option>
          <option value="ESCALATED">🟠 Escaladée</option>
          <option value="IGNORED">🚫 Ignoré</option>
          <option value="RESOLVED">🟢 Résolu</option>
          <option value="CLOSED">⚫ Clôturée</option>
        </select>
        <button className="btn bg-btn bsm" onClick={load}>🔄</button>
      </div>

      {/* Detail panel */}
      {selected && (
        <div className={styles.detailPanel}>
          <SlaPanel alert={selected} />
          <div className={styles.detailHeader}>
            <div>
              <div style={{ fontSize: 15, fontWeight: 800, marginBottom: 3 }}>
                {selected.flux_name} {selected.division ? `— ${selected.division}` : ''}
              </div>
              <div style={{ fontSize: 11, color: 'var(--mut)' }}>
                Token: {selected.token} · {selected.created_at ? new Date(selected.created_at).toLocaleString('fr-FR') : ''}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <button className="btn bg-btn bsm" onClick={() => setSelected(null)}>✕ Fermer</button>
              <button
                className="btn bsm"
                style={{ background: 'var(--blu-lt)', color: 'var(--blue)', border: '1px solid var(--blu-md)' }}
                onClick={() => trackAlert('ACKNOWLEDGED')}
              >✅ Prendre en charge</button>
              <button
                className="btn bsm"
                style={{ background: 'rgba(148,163,184,.2)', color: 'var(--mut)', border: '1px solid rgba(148,163,184,.35)' }}
                onClick={() => trackAlert('IGNORED')}
              >🚫 Ignorer</button>
              <button
                className="btn bsm"
                style={{ background: 'var(--orn-lt)', color: 'var(--orange)', border: '1px solid var(--orn-md)' }}
                onClick={() => setEscalateModal(true)}
              >⬆ Escalader</button>
              <button className="btn bgreen bsm" onClick={() => setResolveModal(true)}>✔ Résoudre</button>
            </div>
          </div>

          {/* KPIs */}
          <div className={styles.detailKpis}>
            {[
              { l: 'TAUX DE CONFORMITÉ', v: `${selected.concordance ?? '—'}%`, c: 'var(--green)' },
              { l: 'ÉTAT', v: concordanceBadge(selected.concordance_state, selected.concordance ?? 0).label, c: 'var(--txt)' },
              { l: 'CRITIQUES',   v: selected.n_critiques ?? '—', c: 'var(--red)' },
              { l: 'WARNINGS',    v: selected.n_warnings ?? '—', c: 'var(--orange)' },
            ].map(k => (
              <div key={k.l} className={styles.detailKpi}>
                <div style={{ fontSize: 10, color: 'var(--mut)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.5px', marginBottom: 4 }}>{k.l}</div>
                <div style={{ fontSize: 16, fontWeight: 800, color: k.c }}>{k.v}</div>
              </div>
            ))}
          </div>
          <div className={styles.detailKpis}>
            {[
              { l: 'STATUT', v: STATUS_LABELS[selected.status] ?? selected.status, c: STATUS_BORDER[selected.status] },
              { l: 'RESPONSABLE', v: selected.escalated_to || selected.resolved_by || '—', c: 'var(--blue)' },
              ...(selected.sla_warning_sent ? [{ l: 'SLA WARNING', v: '📧 Envoyé', c: 'var(--orange)' }] : []),
              ...(selected.sla_breached ? [{ l: 'SLA DÉPASSÉ', v: '🚨 Oui', c: 'var(--red)' }] : []),
            ].map(k => (
              <div key={k.l} className={styles.detailKpi}>
                <div style={{ fontSize: 10, color: 'var(--mut)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.5px', marginBottom: 4 }}>{k.l}</div>
                <div style={{ fontSize: 16, fontWeight: 800, color: k.c }}>{k.v}</div>
              </div>
            ))}
          </div>

          {/* Anomalies — ✅ champs corrects : error_type, severity, key_str, val_cegid, val_oracle */}
          <div className="sblk">
            <div className="sblk-h">
              <span style={{ fontSize: 12, fontWeight: 700 }}>🔍 Anomalies</span>
              <button className="btn bg-btn bxs" onClick={suggestIA} disabled={aiLoading}>
                {aiLoading ? '⏳ Analyse...' : '🤖 Suggestion IA'}
              </button>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table>
                <thead>
                  <tr>
                    <th>Sévérité</th>
                    <th>Type</th>
                    <th>Clé</th>
                    <th>Cegid</th>
                    <th>Oracle</th>
                    <th>Explication</th>
                  </tr>
                </thead>
                <tbody>
                  {(selected.anomalies ?? []).slice(0, 100).map((a: Anomaly, i: number) => (
                    <tr key={i}>
                      <td>
                        <span className={`bdg ${severityBadge(a.severity)}`}>
                          {severityLabel(a.severity)}
                        </span>
                      </td>
                      {/* ✅ error_type (pas "type") */}
                      <td style={{ fontSize: 11 }}>{a.error_type ?? '—'}</td>
                      {/* ✅ key_str (pas "key") */}
                      <td><span className="val-box" style={{ fontSize: 10 }}>{a.key_str ?? '—'}</span></td>
                      {/* ✅ val_cegid (pas "cegid_val") */}
                      <td><span className="val-box">{a.val_cegid ?? '—'}</span></td>
                      {/* ✅ val_oracle (pas "oracle_val") */}
                      <td><span className="val-box">{a.val_oracle ?? '—'}</span></td>
                      {/* ✅ explication (pas "message") */}
                      <td style={{ fontSize: 11 }}>{a.explication ?? '—'}</td>
                    </tr>
                  ))}
                  {!(selected.anomalies?.length) && (
                    <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--mut)', padding: '1rem' }}>Aucune anomalie</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* AI suggestion — ✅ champs corrects : suggestion.diagnostic, suggestion.actions, suggestion.ia_analyse */}
          {(aiLoading || aiData) && (
            <div className={styles.iaBox}>
              <h4>🤖 Suggestion IA</h4>
              {aiLoading && <div style={{ color: 'var(--mut)', fontSize: 12 }}>Analyse en cours...</div>}
              {aiData && (
                <div>
                  {/* Diagnostic principal */}
                  {aiData.diagnostic && (
                    <p style={{ fontSize: 12, marginBottom: 8 }}>
                      <strong>Diagnostic :</strong> {aiData.diagnostic}
                    </p>
                  )}
                  {/* Analyse IA enrichie (si Claude API disponible) */}
                  {aiData.ia_analyse && (
                    <p style={{ fontSize: 12, marginBottom: 8, color: 'var(--purple)' }}>
                      <strong>🧠 Analyse IA :</strong> {aiData.ia_analyse}
                    </p>
                  )}
                  {/* Actions prioritaires */}
                  {((aiData.ia_actions ?? aiData.actions)?.length ?? 0) > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      <strong style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--mut)' }}>Actions recommandées</strong>
                      <ul style={{ paddingLeft: 0, listStyle: 'none', marginTop: 4 }}>
                        {(aiData.ia_actions ?? aiData.actions ?? []).map((a: string, i: number) => (
                          <li key={i} style={{ fontSize: 12, padding: '3px 0', display: 'flex', gap: 6 }}>
                            <span style={{ color: 'var(--purple)', fontWeight: 700 }}>→</span> {a}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {/* Prévention */}
                  {(aiData.ia_prevention ?? aiData.prevention) && (
                    <p style={{ fontSize: 11, color: 'var(--mut)', borderTop: '1px solid var(--brd)', paddingTop: 6, marginTop: 6 }}>
                      <strong>Prévention :</strong> {aiData.ia_prevention ?? aiData.prevention}
                    </p>
                  )}
                  {/* Méta */}
                  <div style={{ display: 'flex', gap: 8, marginTop: 8, fontSize: 10, color: 'var(--mut)' }}>
                    {aiData.confidence != null && <span>Confiance : {aiData.confidence}%</span>}
                    {aiData.urgence && <span>Urgence : {aiData.urgence}</span>}
                    {aiData.ia_enrichi && <span style={{ color: 'var(--purple)' }}>✨ Enrichi par IA</span>}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Tracking */}
          {(selected.tracking ?? []).length > 0 && (
            <div className="sblk" style={{ marginTop: 12 }}>
              <div className="sblk-h"><span style={{ fontSize: 12, fontWeight: 700 }}>📜 Historique</span></div>
              <div style={{ padding: '4px 14px 8px' }}>
                {(selected.tracking ?? []).map((t: TrackingEntry, i: number) => {
                  const when = t.at ?? t.created_at
                  return (
                    <div key={i} className={styles.trkItem}>
                      <span className={styles.trkDot} />
                      <span style={{ fontWeight: 700 }}>{STATUS_LABELS[t.action ?? t.status ?? ''] ?? (t.action ?? t.status)}</span>
                      <span style={{ color: 'var(--mut)', marginLeft: 4 }}>— {t.username ?? t.user ?? '?'}</span>
                      {t.comment && <span style={{ color: 'var(--txt2)', marginLeft: 8, fontSize: 11 }}>« {t.comment} »</span>}
                      <span style={{ marginLeft: 'auto', color: 'var(--mut)', fontSize: 11 }}>
                        {when ? new Date(when).toLocaleString('fr-FR') : ''}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Alerts list */}
      <div>
        {alerts.length === 0 ? (
          <div className="sblk">
            <div className="empty">
              <div className="empty-ico">🔔</div>
              <div className="empty-txt">Aucune alerte</div>
              <div className="empty-sub">Les alertes apparaissent automatiquement lors des analyses</div>
            </div>
          </div>
        ) : alerts.map(a => (
          <div
            key={a.id ?? a.token}
            className={styles.alertCard}
            style={{ borderLeftColor: STATUS_BORDER[a.status] ?? 'var(--brd)' }}
            onClick={() => selectAlert(a)}
          >
            <div className={styles.alertIcon} style={{ background: a.n_critiques > 0 ? 'var(--red-lt)' : 'var(--orn-lt)', fontSize: 18 }}>
              {a.n_critiques > 0 ? '🚨' : '⚠️'}
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 3 }}>
                {a.flux_name ?? a.flux_id} {a.division ? `— ${a.division}` : ''}
              </div>
              <div style={{ fontSize: 11, color: 'var(--mut)', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <span>{STATUS_LABELS[a.status] ?? a.status}</span>
                <span>{a.n_critiques ?? 0} critique(s)</span>
                <span>{a.n_warnings ?? 0} warning(s)</span>
                <span>{a.concordance ?? 0}% conformité</span>
                <span>{a.created_at ? new Date(a.created_at).toLocaleString('fr-FR') : ''}</span>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <span className={`bdg ${concordanceBadge(a.concordance_state, a.concordance ?? 0).cls}`}>
                {concordanceBadge(a.concordance_state, a.concordance ?? 0).label.split(' ').slice(1).join(' ')}
              </span>
              {a.sla_breached ? <span className="bdg b-r" title="SLA dépassée">🚨 SLA</span> : null}
              {a.sla_warning_sent ? <span className="bdg b-o" title="Email SLA warning envoyé">📧</span> : null}
              {a.escalated_to ? <span className="bdg b-o" title={`Escaladé vers ${a.escalated_to}`}>↗</span> : null}
              <button
                className={styles.deleteBtn}
                title="Supprimer l'alerte"
                aria-label="Supprimer l'alerte"
                onClick={e => { e.stopPropagation(); setDeleteTarget(a) }}
              >🗑️</button>
            </div>
          </div>
        ))}
      </div>

      {/* Resolve modal */}
      {resolveModal && (
        <div className="ov" onClick={() => setResolveModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="mhead">
              <span style={{ fontWeight: 700 }}>✔ Résoudre l'alerte</span>
              <button className="mclose" onClick={() => setResolveModal(false)}>×</button>
            </div>
            <div className="mbody">
              <div className="fg">
                <label>Commentaire de résolution (optionnel)</label>
                <textarea
                  rows={4}
                  value={resolveComment}
                  onChange={e => setResolveComment(e.target.value)}
                  placeholder="Décrivez la résolution... (laisser vide si non applicable)"
                  style={{ resize: 'vertical', width: '100%', padding: '8px', borderRadius: 6, border: '1.5px solid var(--brd)', fontFamily: 'inherit', fontSize: 13 }}
                />
              </div>
            </div>
            <div className="mfoot">
              <button className="btn bg-btn bsm" onClick={() => setResolveModal(false)}>Annuler</button>
              <button className="btn bgreen bsm" onClick={resolveAlert}>✔ Confirmer la résolution</button>
            </div>
          </div>
        </div>
      )}

      {/* Escalate modal */}
      {escalateModal && (
        <div className="ov" onClick={() => setEscalateModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="mhead">
              <span style={{ fontWeight: 700 }}>⬆ Escalader l'alerte</span>
              <button className="mclose" onClick={() => setEscalateModal(false)}>×</button>
            </div>
            <div className="mbody">
              <div className="fg" style={{ marginBottom: 12 }}>
                <label>EMAIL DU CONSULTANT / TEAM LEADER</label>
                <input
                  type="email"
                  value={escalateEmail}
                  onChange={e => setEscalateEmail(e.target.value)}
                  placeholder="ex: consultant@timsoft.com"
                />
              </div>
              <div className="fg" style={{ marginBottom: 12 }}>
                <label>RAISON DE L'ESCALADE</label>
                <select
                  value={escalateReason}
                  onChange={e => setEscalateReason(e.target.value)}
                  style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1.5px solid var(--brd)', fontFamily: 'inherit', fontSize: 13, color: 'var(--txt)', background: '#fff' }}
                >
                  <option value="Vérification requise">Vérification requise</option>
                  <option value="CRITIQUE_NON_RESOLU">Critique non résolu</option>
                  <option value="IMPACT_FINANCIER">Impact financier important</option>
                  <option value="RECURRENCE">Problème récurrent</option>
                  <option value="AUTRE">Autre</option>
                </select>
              </div>
              <div className="fg">
                <label>COMMENTAIRE</label>
                <textarea
                  rows={3}
                  value={escalateComment}
                  onChange={e => setEscalateComment(e.target.value)}
                  placeholder="Ajouter un commentaire pour le consultant..."
                  style={{ resize: 'vertical', width: '100%', padding: '8px 10px', borderRadius: 8, border: '1.5px solid var(--brd)', fontFamily: 'inherit', fontSize: 13 }}
                />
              </div>
            </div>
            <div className="mfoot">
              <button className="btn bg-btn bsm" onClick={() => { setEscalateModal(false) }}>Annuler</button>
              <button
                className="btn bsm"
                style={{ background: 'var(--orn-lt)', color: 'var(--orange)', border: '1px solid var(--orn-md)' }}
                onClick={escalateAlert}
                disabled={!escalateEmail || !escalateEmail.includes('@')}
              >⬆ Confirmer l'escalade</button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirmation modal */}
      {deleteTarget && (
        <div className="ov" onClick={() => setDeleteTarget(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="mhead">
              <span style={{ fontWeight: 700 }}>🗑️ Supprimer l'alerte</span>
              <button className="mclose" onClick={() => setDeleteTarget(null)}>×</button>
            </div>
            <div className="mbody">
              <p style={{ fontSize: 13, margin: 0, color: 'var(--txt2)' }}>
                Voulez-vous vraiment supprimer l'alerte{' '}
                <strong>{deleteTarget.flux_name ?? deleteTarget.flux_id}</strong>
                {deleteTarget.division ? ` — ${deleteTarget.division}` : ''} ?
              </p>
              <p style={{ fontSize: 11, color: 'var(--mut)', margin: '8px 0 0' }}>
                Cette action est définitive : l'alerte et tout son historique de suivi seront supprimés.
              </p>
            </div>
            <div className="mfoot">
              <button className="btn bg-btn bsm" onClick={() => setDeleteTarget(null)}>Annuler</button>
              <button className="btn bsm" style={{ background: 'var(--red-lt)', color: 'var(--red)', border: '1px solid var(--red)' }} onClick={deleteAlert} disabled={deleting}>
                {deleting ? '⏳ Suppression...' : '🗑️ Confirmer la suppression'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}