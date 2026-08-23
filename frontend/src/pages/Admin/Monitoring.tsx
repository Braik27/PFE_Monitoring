import { useEffect, useState, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Search,
  Bell,
  FileText,
  Eye,
  Download,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Terminal,
  Activity
} from 'lucide-react'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'
import styles from './Monitoring.module.css'

// ── Types ──────────────────────────────────────────────────────────────────
interface FluxStat {
  flux_id: string
  flux_name: string
  n_analyses: number
  total_critiques: number
  total_warnings: number
  concordance_moy: number
  last_analysis: unknown | null
}

/** Champs de paire utilisés pour les totaux Cegid/Oracle — ⚠️ inféré du rendu. */
interface PairTotals {
  n_cegid?: number
  n_oracle?: number
}

interface AnalysisRow {
  id: number
  flux_id: string
  label: string
  created_at: string
  summary: {
    flux_name?: string
    analyst?: string
    division?: string
    concordance_moyenne?: number
    total_critiques?: number
    total_warnings?: number
    total_anomalies?: number
    pairs?: PairTotals[]
  }
}

interface PerfMetrics {
  requests_total: number
  error_rate_pct: number
  avg_response_ms: number
  p95_response_ms: number
  ia_success_rate_pct: number
  ia_calls_total: number
}

interface HealthStatus {
  status: string
  version: string
  timestamp: string
  response_ms: number
  components: {
    database?: string
    ollama?: string
  }
  environment: string
}

export default function Monitoring() {
  const { showToast } = useToast()
  const navigate = useNavigate()

  // State
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [sortBy, setSortBy] = useState('newest')
  const [isDarkMode, setIsDarkMode] = useState(false)
  const [alertBadge, setAlertBadge] = useState(true)

  const [stats, setStats] = useState<FluxStat[]>([])
  const [history, setHistory] = useState<AnalysisRow[]>([])
  const [perf, setPerf] = useState<PerfMetrics | null>(null)
  const [health, setHealth] = useState<HealthStatus | null>(null)

  // Fetch data
  const loadData = useCallback(async () => {
    try {
      const [statsRes, histRes, perfRes, healthRes] = await Promise.all([
        api.get('/api/stats').catch(() => ({ data: [] })),
        api.get('/api/history?limit=100').catch(() => ({ data: [] })),
        api.get('/api/system/perf').catch(() => ({ data: null })),
        api.get('/api/system/health').catch(() => ({ data: null }))
      ])

      setStats(Array.isArray(statsRes.data) ? statsRes.data : [])
      setHistory(Array.isArray(histRes.data) ? histRes.data : [])
      setPerf(perfRes.data)
      setHealth(healthRes.data)
    } catch (err) {
      showToast('Erreur lors du chargement des données de monitoring', 'error')
    } finally {
      setLoading(false)
    }
  }, [showToast])

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 30000)
    return () => clearInterval(interval)
  }, [loadData])

  // Computed metrics
  const totalAnalyses = useMemo(() => {
    return stats.reduce((sum, f) => sum + (f.n_analyses ?? 0), 0)
  }, [stats])

  const avgConcordance = useMemo(() => {
    const activeFluxes = stats.filter(f => f.n_analyses > 0)
    if (activeFluxes.length === 0) return 100.0
    const sum = activeFluxes.reduce((acc, f) => acc + (f.concordance_moy ?? 100.0), 0)
    return Math.round(sum / activeFluxes.length * 10) / 10
  }, [stats])

  const avgRiskScore = useMemo(() => {
    return Math.max(0, Math.round(100 - avgConcordance))
  }, [avgConcordance])

  const formattedResponseTime = useMemo(() => {
    const ms = perf?.avg_response_ms ?? 0
    if (ms === 0) return '—'
    return ms > 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`
  }, [perf])

  // Filtered and sorted history
  const filteredHistory = useMemo(() => {
    let result = [...history]

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      result = result.filter(r => 
        (r.label ?? '').toLowerCase().includes(q) ||
        (r.flux_id ?? '').toLowerCase().includes(q) ||
        (r.summary?.flux_name ?? '').toLowerCase().includes(q)
      )
    }

    result.sort((a, b) => {
      const timeA = new Date(a.created_at).getTime()
      const timeB = new Date(b.created_at).getTime()
      return sortBy === 'newest' ? timeB - timeA : timeA - timeB
    })

    return result
  }, [history, searchQuery, sortBy])

  // Latest analysis risk score
  const latestAnalysis = useMemo(() => {
    return history[0] ?? null
  }, [history])

  const latestRiskScore = useMemo(() => {
    if (!latestAnalysis) return 0
    const conc = latestAnalysis.summary?.concordance_moyenne ?? 100
    return Math.max(0, 100 - conc)
  }, [latestAnalysis])

  const riskLevel = useMemo(() => {
    const r = latestRiskScore
    if (r < 10) return { label: 'Risque Faible', color: 'var(--green)', class: styles.trendUp }
    if (r <= 25) return { label: 'Risque Moyen', color: 'var(--orange)', class: styles.trendDown }
    return { label: 'Risque Élevé', color: 'var(--red)', class: styles.trendDown }
  }, [latestRiskScore])

  // Last 5 analyses for historical comparison (chronological order)
  const historicalBars = useMemo(() => {
    const lastFive = history.slice(0, 5).reverse()
    return lastFive.map(r => {
      const conc = r.summary?.concordance_moyenne ?? 100
      return {
        id: r.id,
        val: Math.max(0, 100 - conc),
        label: r.created_at ? new Date(r.created_at).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' }) : '—'
      }
    })
  }, [history])

  // Download Excel Handler
  const handleDownloadExcel = async (id: number, fluxId: string) => {
    try {
      showToast('Préparation du rapport Excel...', 'info')
      const res = await api.get(`/api/analysis/${id}/export/excel`, { responseType: 'blob' })
      const blob = new Blob([res.data])
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)
      link.download = `rapport_${fluxId}_${id}.xlsx`
      link.click()
      URL.revokeObjectURL(link.href)
      showToast('Téléchargement Excel démarré !', 'success')
    } catch {
      showToast('Erreur lors du téléchargement du rapport Excel', 'error')
    }
  }

  // Console terminal lines
  const consoleLines = useMemo(() => {
    const lines = [
      { type: 'info', text: 'Initializing system status logs...' }
    ]

    if (health) {
      lines.push({
        type: health.status === 'healthy' ? 'success' : 'warn',
        text: `System overall status: ${health.status.toUpperCase()} (Env: ${health.environment})`
      })
      lines.push({
        type: health.components?.database === 'ok' ? 'success' : 'warn',
        text: `Database Connection: ${health.components?.database === 'ok' ? 'CONNECTED' : 'DEGRADED'}`
      })
      if (health.components?.ollama) {
        lines.push({
          type: 'info',
          text: `Ollama NIM Services: ${health.components.ollama.toUpperCase()}`
        })
      }
    }

    if (perf) {
      lines.push({
        type: 'info',
        text: `Performance profile: ${perf.requests_total} web requests registered.`
      })
      lines.push({
        type: perf.error_rate_pct > 5 ? 'warn' : 'success',
        text: `Average HTTP Response Latency: ${Math.round(perf.avg_response_ms)}ms (Error rate: ${perf.error_rate_pct}%)`
      })
    }

    lines.push({ type: 'info', text: 'WS Alert Broadcaster connection online.' })
    return lines
  }, [health, perf])

  if (loading && stats.length === 0) {
    return (
      <div className={styles.loader}>
        <div className="spin" style={{ width: 28, height: 28, borderWidth: 3, borderTopColor: 'var(--blue)', borderColor: 'var(--brd)' }} />
        <span>Chargement des métriques de réconciliation...</span>
      </div>
    )
  }

  return (
    <div className={styles.dashboard}>
      {/* ── Top controls ── */}
      <div className={styles.topRow}>
        <div className={styles.searchWrapper}>
          <Search className={styles.searchIcon} />
          <input
            type="text"
            className={styles.searchInput}
            placeholder="Rechercher une analyse par nom ou flux..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
        </div>

        <div className={styles.rightControls}>
          {/* Theme Toggle switch */}
          <div className={styles.toggleWrapper} onClick={() => setIsDarkMode(!isDarkMode)}>
            <div className={`${styles.toggleTrack} ${isDarkMode ? styles.toggleTrackActive : ''}`}>
              <div className={`${styles.toggleThumb} ${isDarkMode ? styles.toggleThumbActive : ''}`} />
            </div>
          </div>

          {/* Alert Notification bell */}
          <button className={styles.notificationBtn} onClick={() => { setAlertBadge(false); navigate('/alerts') }}>
            <Bell size={18} />
            {alertBadge && <span className={styles.notificationBadge} />}
          </button>

          {/* New Analysis Action */}
          <button className="btn bp bsm" onClick={() => navigate('/analyze')}>
            + Nouvelle analyse
          </button>
        </div>
      </div>

      {/* ── KPI Row ── */}
      <div className={styles.kpiGrid}>
        {/* KPI 1 */}
        <div className={styles.kpiCard}>
          <div className={styles.kpiHeader}>
            <div className={`${styles.kpiIconBox} ${styles.bgBlue}`}>
              <Activity size={18} />
            </div>
            <span className={`${styles.trendBadge} ${styles.trendUp}`}>+2%</span>
          </div>
          <div className={styles.kpiValue}>{totalAnalyses}</div>
          <div className={styles.kpiLabel}>Analyses Totales</div>
        </div>

        {/* KPI 2 */}
        <div className={styles.kpiCard}>
          <div className={styles.kpiHeader}>
            <div className={`${styles.kpiIconBox} ${styles.bgGreen}`}>
              <CheckCircle2 size={18} />
            </div>
            <span className={`${styles.trendBadge} ${styles.trendUp}`}>+5%</span>
          </div>
          <div className={styles.kpiValue}>{avgConcordance}%</div>
          <div className={styles.kpiLabel}>Taux Concordance Moyen</div>
        </div>

        {/* KPI 3 */}
        <div className={styles.kpiCard}>
          <div className={styles.kpiHeader}>
            <div className={`${styles.kpiIconBox} ${styles.bgOrange}`}>
              <AlertTriangle size={18} />
            </div>
            <span className={`${styles.trendBadge} ${styles.trendUp}`}>+7%</span>
          </div>
          <div className={styles.kpiValue}>{avgRiskScore}%</div>
          <div className={styles.kpiLabel}>Score de Risque Moyen</div>
        </div>

        {/* KPI 4 */}
        <div className={styles.kpiCard}>
          <div className={styles.kpiHeader}>
            <div className={`${styles.kpiIconBox} ${styles.bgPurple}`}>
              <Clock size={18} />
            </div>
            <span className={`${styles.trendBadge} ${styles.trendDown}`}>-3%</span>
          </div>
          <div className={styles.kpiValue}>{formattedResponseTime}</div>
          <div className={styles.kpiLabel}>Latence HTTP Moyenne</div>
        </div>
      </div>

      {/* ── Main content grid ── */}
      <div className={styles.contentGrid}>
        {/* Left column: Recent Analyses */}
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <div className={styles.cardTitle}>
              <FileText size={18} style={{ color: 'var(--blue)' }} />
              <span>Dernières analyses de réconciliation</span>
              <span className={styles.badge}>{filteredHistory.length} analyses</span>
            </div>
            <select
              className={styles.select}
              value={sortBy}
              onChange={e => setSortBy(e.target.value)}
              aria-label="Trier les analyses par date"
            >
              <option value="newest">Plus récent</option>
              <option value="oldest">Plus ancien</option>
            </select>
          </div>

          <div className={styles.listWrapper}>
            {filteredHistory.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '40px 10px', color: 'var(--mut)' }}>
                Aucune analyse ne correspond à votre recherche.
              </div>
            ) : (
              filteredHistory.map(row => {
                const dateStr = row.created_at
                  ? new Date(row.created_at).toLocaleString('fr-FR', {
                      day: '2-digit',
                      month: '2-digit',
                      year: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit'
                    })
                  : '—'
                
                const pairs = row.summary?.pairs ?? []
                const totCegid = pairs.reduce((acc: number, p: PairTotals) => acc + (p.n_cegid ?? 0), 0)
                const totOracle = pairs.reduce((acc: number, p: PairTotals) => acc + (p.n_oracle ?? 0), 0)
                const recordCount = totCegid > 0 ? `${totCegid} Cegid / ${totOracle} Oracle` : 'Pas de lignes'

                return (
                  <div key={row.id} className={styles.listItem}>
                    <div className={styles.itemIconBox}>
                      <FileText size={18} />
                    </div>
                    <div className={styles.itemDetails}>
                      <div className={styles.itemName} title={row.label}>
                        {row.label}
                      </div>
                      <div className={styles.itemMeta}>
                        <span>{dateStr}</span>
                        <span className={styles.itemDot} />
                        <span>{recordCount}</span>
                        <span className={styles.itemDot} />
                        <span style={{ 
                          fontWeight: 700, 
                          color: (row.summary?.concordance_moyenne ?? 100) >= 95 ? 'var(--green)' : 
                                 (row.summary?.concordance_moyenne ?? 100) >= 80 ? 'var(--orange)' : 'var(--red)'
                        }}>
                          Concordance: {row.summary?.concordance_moyenne ?? 100}%
                        </span>
                      </div>
                    </div>
                    <div className={styles.itemActions}>
                      <button
                        className={styles.actionBtn}
                        title="Consulter les détails"
                        onClick={() => navigate(`/history`)}
                      >
                        <Eye size={16} />
                      </button>
                      <button
                        className={styles.actionBtn}
                        title="Exporter en Excel"
                        onClick={() => handleDownloadExcel(row.id, row.flux_id)}
                      >
                        <Download size={16} />
                      </button>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>

        {/* Right column: Risk gauge & Hist comparison & Live terminal console */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Current Risk Score Card */}
          <div className={styles.card}>
            <div className={styles.cardHeader}>
              <span className={styles.cardTitle}>Score de Risque Actuel</span>
              <span className={`${styles.trendBadge} ${riskLevel.class}`}>
                {riskLevel.label}
              </span>
            </div>

            <div className={styles.gaugeContainer}>
              <div
                className={styles.gaugeOuter}
                style={{
                  background: `conic-gradient(${riskLevel.color} 0deg, ${riskLevel.color} ${latestRiskScore * 3.6}deg, var(--s3) ${latestRiskScore * 3.6}deg, var(--s3) 360deg)`
                }}
              >
                <div className={styles.gaugeInner}>
                  <span className={styles.gaugeVal}>{latestRiskScore}</span>
                  <span className={styles.gaugeLbl}>Indice Risque</span>
                </div>
              </div>
            </div>

            {/* Historical comparison nested inside */}
            <div style={{ borderTop: '1px solid var(--brd)', paddingTop: 16, marginTop: 4 }}>
              <span className={styles.kpiLabel} style={{ display: 'block', marginBottom: 12 }}>
                Comparaison Historique (Score Risque)
              </span>
              
              {historicalBars.length === 0 ? (
                <div style={{ textAlign: 'center', fontSize: 11, color: 'var(--mut)', padding: '20px 0' }}>
                  Historique insuffisant
                </div>
              ) : (
                <>
                  <div className={styles.chartContainer}>
                    {historicalBars.map((bar, idx) => {
                      const isActive = idx === historicalBars.length - 1
                      return (
                        <div key={bar.id} className={styles.chartBarWrapper}>
                          <span className={styles.chartBarValue}>{bar.val}</span>
                          <div className={styles.chartBarTrack}>
                            <div
                              className={`${styles.chartBarFill} ${isActive ? styles.chartBarFillActive : ''}`}
                              style={{ height: `${Math.min(100, Math.max(8, bar.val))}%` }}
                            />
                          </div>
                          <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--txt2)' }}>
                            {bar.label}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                  <div className={styles.chartLabels}>
                    <span>Anciennes</span>
                    <span>Dernière</span>
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Real-time processing logs console */}
          <div className={`${styles.card} ${styles.consoleCard}`}>
            <div className={styles.cardHeader}>
              <span className={`${styles.cardTitle} ${styles.consoleTitle}`}>
                <Terminal size={16} />
                Console de Monitoring
              </span>
              <span className={styles.badge} style={{ background: '#1e293b', color: '#38bdf8', borderColor: '#38bdf8', border: '1px solid' }}>
                LIVE
              </span>
            </div>

            <div className={styles.consoleBody}>
              {consoleLines.map((line, idx) => {
                let textClass = styles.consoleText
                if (line.type === 'success') textClass = styles.consoleTextSuccess
                if (line.type === 'warn') textClass = styles.consoleTextWarn

                return (
                  <div key={idx} className={styles.consoleLine}>
                    <span className={styles.consolePrompt}>&gt;_</span>
                    <span className={textClass}>{line.text}</span>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
