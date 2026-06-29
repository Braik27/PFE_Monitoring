import { useEffect, useState, useCallback } from 'react'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'
import { buildReportingFromApi, mapHistoryRow } from '../../hooks/useApi'
import styles from './Reporting.module.css'

const PERIODS = [
  { key: 'week',  label: 'Cette semaine' },
  { key: 'month', label: 'Ce mois' },
  { key: 'year',  label: 'Cette année' },
]

export default function Reporting() {
  const { showToast } = useToast()
  const [period, setPeriod] = useState('month')
  const [divFilter, setDivFilter] = useState('')
  const [fluxTabs, setFluxTabs] = useState<string[]>([])
  const [activeTab, setActiveTab] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string> = { period }
      if (divFilter) params.division = divFilter
      const res = await api.get('/api/reporting', { params })
      const rows = Array.isArray(res.data) ? res.data : []
      const d = buildReportingFromApi(rows as Record<string, unknown>[])
      setData(d)
      const tabs: string[] = d.by_flux ? Object.keys(d.by_flux) : []
      setFluxTabs(tabs)
      if (tabs.length) setActiveTab((t) => (t && tabs.includes(t) ? t : tabs[0]))
    } catch (err: any) {
      try {
        const res2 = await api.get('/api/history', { params: { limit: 200 } })
        const raw = Array.isArray(res2.data) ? res2.data : []
        const list = raw.map((r: Record<string, unknown>) => mapHistoryRow(r))
        const byFlux: Record<string, any[]> = {}
        list.forEach((a: any) => {
          const key = a.flux_name ?? a.flux_id
          if (!byFlux[key]) byFlux[key] = []
          byFlux[key].push(a)
        })
        setData({ by_flux: byFlux, total: list.length })
        const tabs = Object.keys(byFlux)
        setFluxTabs(tabs)
        if (tabs.length) setActiveTab((t) => (t && tabs.includes(t) ? t : tabs[0]))
      } catch {
        showToast('Erreur chargement reporting', 'error')
      }
    } finally {
      setLoading(false)
    }
  }, [period, divFilter, showToast])

  useEffect(() => { load() }, [load])

  const currentData = data?.by_flux?.[activeTab] ?? []

  const concColor = (r: number) => r >= 95 ? 'var(--green)' : r >= 80 ? 'var(--orange)' : 'var(--red)'

  return (
    <>
          {/* Period bar */}
      <div className={styles.periodBar}>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--txt2)', marginRight: 4 }}>Période :</span>
        {PERIODS.map(p => (
          <button key={p.key} className={`${styles.pchip} ${period === p.key ? styles.pchipOn : ''}`} onClick={() => setPeriod(p.key)}>
            {p.label}
          </button>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <select value={divFilter} onChange={e => setDivFilter(e.target.value)} className={styles.divSel} aria-label="Filtrer par division">
            <option value="">Toutes divisions</option>
            <option value="KSA">🇸🇦 KSA</option>
            <option value="KWT">🇰🇼 KWT</option>
            <option value="SPG">🇸🇬 SPG</option>
            <option value="DOHA">🇶🇦 DOHA</option>
          </select>
          <button className="btn bg-btn bsm" onClick={load}>🔄</button>
        </div>
      </div>

      {/* Flux tabs */}
      <div className={styles.fluxTabs}>
        {fluxTabs.map(t => (
          <button key={t} className={`${styles.fxTab} ${activeTab === t ? styles.fxTabOn : ''}`} onClick={() => setActiveTab(t)}>
            {t}
          </button>
        ))}
      </div>

      {loading ? (
        <div className={styles.loadWrap}><div className="spin" style={{ width: 28, height: 28, borderWidth: 3, borderTopColor: 'var(--blue)', borderColor: 'var(--brd)' }} /></div>
      ) : !activeTab ? (
        <div className="sblk"><div className="empty"><div className="empty-ico">📈</div><div className="empty-txt">Aucune donnée</div></div></div>
      ) : (
        <div className={styles.rGrid}>
          {/* Chart: concordance history */}
          <div className={styles.rCard}>
            <h4>📊 Concordance — {activeTab}</h4>
            <p>Historique des analyses sur la période</p>
            {(Array.isArray(currentData) ? currentData : []).slice(0, 10).map((a: any, i: number) => (
              <div key={i} className={styles.bcRow}>
                <span className={styles.bcLbl}>{a.division ?? a.label?.slice(0, 6) ?? `#${i+1}`}</span>
                <div className={styles.bcBarW}>
                  <div className={styles.bcBar} style={{ width: `${a.concordance_rate ?? 0}%`, background: concColor(a.concordance_rate ?? 0) }}>
                    <span className={styles.bcVal}>{a.concordance_rate ?? 0}%</span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Stats by division */}
          <div className={styles.rCard}>
            <h4>🌐 Répartition par division</h4>
            <p>Analyses regroupées par pays</p>
            <div className={styles.divGrid}>
              {Object.entries(
                (Array.isArray(currentData) ? currentData : []).reduce((acc: any, a: any) => {
                  const div = a.division || 'N/A'
                  if (!acc[div]) acc[div] = { count: 0, critiques: 0, avg_conc: [] }
                  acc[div].count++
                  acc[div].critiques += a.n_critiques ?? 0
                  acc[div].avg_conc.push(a.concordance_rate ?? 0)
                  return acc
                }, {})
              ).map(([div, s]: any) => (
                <div key={div} className={styles.divStat}>
                  <div className={styles.divStatName}>{div}</div>
                  <div className={styles.divStatV} style={{ color: concColor(Math.round(s.avg_conc.reduce((a: number, b: number) => a+b,0)/s.avg_conc.length)) }}>
                    {Math.round(s.avg_conc.reduce((a: number, b: number) => a+b,0)/s.avg_conc.length)}%
                  </div>
                  <div className={styles.divStatSub}>{s.count} analyse(s)</div>
                  {s.critiques > 0 && <div style={{ fontSize: 10, color: 'var(--red)', fontWeight: 600 }}>{s.critiques} critique(s)</div>}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
