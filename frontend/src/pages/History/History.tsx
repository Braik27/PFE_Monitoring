import { useEffect, useState, useCallback } from 'react'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'
import { mapHistoryRow } from '../../hooks/useApi'
import styles from './History.module.css'

const DIVISION_FLAGS: Record<string, string> = { KSA: '🇸🇦', KWT: '🇰🇼', SPG: '🇸🇬', DOHA: '🇶🇦' }

export default function History() {
  const { showToast } = useToast()
  const [analyses, setAnalyses] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [fluxFilter, setFluxFilter] = useState('')
  const [divFilter, setDivFilter] = useState('')
  const [fluxOptions, setFluxOptions] = useState<string[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params: any = { limit: 100 }
      if (fluxFilter) params.flux_id = fluxFilter
      if (divFilter)  params.division = divFilter
      const res = await api.get('/api/history', { params })
      const raw = Array.isArray(res.data) ? res.data : []
      const list = raw.map((r) => mapHistoryRow(r as Record<string, unknown>))
      setAnalyses(list)
      setTotal(res.data.total ?? list.length)
      setFluxOptions([...new Set(list.map((a: any) => a.flux_id).filter(Boolean))] as string[])
    } catch { showToast('Erreur chargement historique', 'error') }
    finally { setLoading(false) }
  }, [fluxFilter, divFilter, showToast])

  useEffect(() => { load() }, [load])

  const concColor = (r: number) => r >= 95 ? 'var(--green)' : r >= 80 ? 'var(--orange)' : 'var(--red)'

  return (
    <>
          <div className={styles.filters}>
        <select value={fluxFilter} onChange={e => setFluxFilter(e.target.value)}>
          <option value="">Tous les flux</option>
          {fluxOptions.map(f => <option key={f} value={f}>{f}</option>)}
        </select>
        <select value={divFilter} onChange={e => setDivFilter(e.target.value)}>
          <option value="">Toutes divisions</option>
          <option value="KSA">KSA</option>
          <option value="KWT">KWT</option>
          <option value="SPG">SPG</option>
          <option value="DOHA">DOHA</option>
        </select>
        <button className="btn bg-btn bsm" onClick={load}>🔄</button>
      </div>

      <div className="sblk">
        <div className="sblk-h">
          <span style={{ fontSize: 13, fontWeight: 700 }}>📋 Toutes les analyses</span>
          <span className="bdg b-x">{total}</span>
        </div>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--mut)' }}>Chargement...</div>
        ) : analyses.length === 0 ? (
          <div className="empty">
            <div className="empty-ico">📋</div>
            <div className="empty-txt">Aucune analyse trouvée</div>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>#</th><th>Flux</th><th>Division</th><th>Direction</th>
                  <th>Description</th><th>Concordance</th><th>Critiques</th>
                  <th>Warnings</th><th>Analyste</th><th>Date</th>
                </tr>
              </thead>
              <tbody>
                {analyses.map((a, i) => (
                  <tr key={a.id ?? i}>
                    <td style={{ color: 'var(--mut)', fontSize: 11 }}>{i + 1}</td>
                    <td style={{ fontWeight: 700 }}>{a.flux_name ?? a.flux_id}</td>
                    <td>
                      {a.division
                        ? <span className="bdg b-x">{DIVISION_FLAGS[a.division] ?? ''} {a.division}</span>
                        : <span style={{ color: 'var(--mut)' }}>—</span>
                      }
                    </td>
                    <td>
                      <span className={`bdg ${a.direction === 'EXPORT' ? 'b-b' : 'b-p'}`} style={{ fontSize: 10 }}>
                        {a.direction ?? '—'}
                      </span>
                    </td>
                    <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {a.label ?? '—'}
                    </td>
                    <td>
                      <span style={{ fontWeight: 800, color: concColor(a.concordance_rate ?? 0) }}>
                        {a.concordance_rate ?? '—'}%
                      </span>
                    </td>
                    <td>
                      {a.n_critiques > 0
                        ? <span className="bdg b-r">{a.n_critiques}</span>
                        : <span style={{ color: 'var(--mut)' }}>0</span>
                      }
                    </td>
                    <td>
                      {a.n_warnings > 0
                        ? <span className="bdg b-o">{a.n_warnings}</span>
                        : <span style={{ color: 'var(--mut)' }}>0</span>
                      }
                    </td>
                    <td style={{ fontSize: 11 }}>{a.analyst ?? '—'}</td>
                    <td style={{ fontSize: 11, color: 'var(--mut)', whiteSpace: 'nowrap' }}>
                      {a.created_at ? new Date(a.created_at).toLocaleString('fr-FR') : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
