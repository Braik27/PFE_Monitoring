import { useEffect, useState, useCallback } from 'react'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'

export default function Monitoring() {
  const { showToast } = useToast()
  const [perf, setPerf] = useState<any>(null)
  const [health, setHealth] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [perfRes, healthRes] = await Promise.all([
        api.get('/api/system/perf').catch(() => ({ data: null })),
        api.get('/health').catch(() => ({ data: null })),
      ])
      setPerf(perfRes.data)
      setHealth(healthRes.data)
    } catch { showToast('Erreur monitoring', 'error') }
    finally { setLoading(false) }
  }, [showToast])

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t) }, [load])

  const StatCard = ({ label, value, unit = '', color = 'var(--blue)' }: any) => (
    <div style={{ background: '#fff', border: '1.5px solid var(--brd)', borderRadius: 'var(--r)', padding: '18px', boxShadow: 'var(--sh)' }}>
      <div style={{ fontSize: 10, color: 'var(--mut)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.5px', marginBottom: 8 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 800, color }}>{value ?? '—'}{unit}</div>
    </div>
  )

  return (
    <>
          {/* Health status */}
      <div style={{ background: health?.status === 'healthy' ? 'var(--grn-lt)' : 'var(--red-lt)', border: `1.5px solid ${health?.status === 'healthy' ? 'var(--grn-md)' : 'var(--red-md)'}`, borderRadius: 'var(--r)', padding: '14px 20px', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 20 }}>{health?.status === 'healthy' ? '✅' : '❌'}</span>
        <div>
          <div style={{ fontWeight: 700, color: health?.status === 'healthy' ? 'var(--green)' : 'var(--red)' }}>
            Système {health?.status === 'healthy' ? 'opérationnel' : 'dégradé'}
          </div>
          <div style={{ fontSize: 11, color: 'var(--mut)' }}>
            Environnement : {health?.environment ?? '—'} · Base de données : {health?.database ?? '—'}
          </div>
        </div>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--mut)' }}>Mise à jour toutes les 30s</span>
      </div>

      {/* Performance KPIs */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--mut)' }}>Chargement des métriques...</div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 20 }}>
            <StatCard label="Requêtes totales"    value={perf?.total_requests ?? 0}  color="var(--blue)" />
            <StatCard label="Temps moyen (ms)"     value={perf?.avg_duration_ms != null ? Math.round(perf.avg_duration_ms) : '—'}  unit=" ms" color="var(--purple)" />
            <StatCard label="Taux d'erreur"        value={perf?.error_rate != null ? `${(perf.error_rate * 100).toFixed(1)}` : '—'} unit="%" color={perf?.error_rate > 0.05 ? 'var(--red)' : 'var(--green)'} />
            <StatCard label="Erreurs 5xx"          value={perf?.total_errors ?? 0}   color="var(--red)" />
          </div>

          {/* Additional metrics */}
          {perf?.recent_errors?.length > 0 && (
            <div className="sblk">
              <div className="sblk-h"><span style={{ fontSize: 13, fontWeight: 700 }}>⚠️ Erreurs récentes</span></div>
              <div style={{ overflowX: 'auto' }}>
                <table>
                  <thead><tr><th>Timestamp</th><th>Endpoint</th><th>Code</th><th>Message</th></tr></thead>
                  <tbody>
                    {perf.recent_errors.map((e: any, i: number) => (
                      <tr key={i}>
                        <td style={{ fontSize: 11, color: 'var(--mut)', whiteSpace: 'nowrap' }}>{e.timestamp ?? '—'}</td>
                        <td><span className="val-box">{e.endpoint ?? '—'}</span></td>
                        <td><span className="bdg b-r">{e.status ?? '—'}</span></td>
                        <td style={{ fontSize: 11 }}>{e.message ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </>
  )
}
