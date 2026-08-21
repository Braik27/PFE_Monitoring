import { type ReactNode, useEffect, useState } from 'react'
import { Clock, AlertTriangle, CheckCircle2, Ban } from 'lucide-react'
import api from '../../lib/api'
import styles from './SlaCompliance.module.css'

interface TrendPeriod {
  compliance_pct: number
  resolved_in_sla: number
  resolved_late: number
  mttr_hours: number
  ignored_count: number
}

interface SlaMetrics {
  compliance_pct: number
  mttr_hours: number
  current_breaches: number
  resolved_in_sla: number
  resolved_late: number
  ignored_count: number
  trend_7d: TrendPeriod
  trend_30d: TrendPeriod
}

function pctColor(pct: number): string {
  if (pct >= 90) return 'var(--green)'
  if (pct >= 75) return 'var(--orange)'
  return 'var(--red)'
}

function KpiCard({
  label,
  value,
  sub,
  color,
  icon,
}: {
  label: string
  value: string | number
  sub?: string
  color?: string
  icon: ReactNode
}) {
  return (
    <div className={styles.kpiCard}>
      <div className={styles.kpiIcon}>{icon}</div>
      <div className={styles.kpiLabel}>{label}</div>
      <div className={styles.kpiValue} style={{ color: color ?? 'inherit' }}>{value}</div>
      {sub && <div className={styles.kpiSub}>{sub}</div>}
    </div>
  )
}

export default function SlaCompliance() {
  const [metrics, setMetrics] = useState<SlaMetrics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get('/api/sla/metrics', { params: { days: 30 } })
      .then(res => setMetrics(res.data))
      .catch(() => setError('Impossible de charger les métriques SLA'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className={styles.wrap}>
        <div className={styles.header}>
          <h2 className={styles.title}>Conformité SLA</h2>
        </div>
        <div className={styles.loading}>Chargement des KPIs…</div>
      </div>
    )
  }

  if (error || !metrics) {
    return (
      <div className={styles.wrap}>
        <div className={styles.header}>
          <h2 className={styles.title}>Conformité SLA</h2>
        </div>
        <div className={styles.error}>{error || 'Données indisponibles'}</div>
      </div>
    )
  }

  const complianceColor = pctColor(metrics.compliance_pct)
  const breachColor = metrics.current_breaches > 0 ? 'var(--red)' : 'var(--green)'

  return (
    <div className={styles.wrap}>
      <div className={styles.header}>
        <h2 className={styles.title}>Conformité SLA</h2>
        <span className={styles.period}>30 derniers jours</span>
      </div>

      <div className={styles.grid}>
        <KpiCard
          label="Résolues dans les délais"
          value={`${metrics.compliance_pct}%`}
          sub={`${metrics.resolved_in_sla} / ${metrics.resolved_in_sla + metrics.resolved_late} alertes`}
          color={complianceColor}
          icon={<CheckCircle2 size={20} />}
        />
        <KpiCard
          label="MTTR moyen"
          value={`${metrics.mttr_hours}h`}
          sub="temps moyen de résolution"
          icon={<Clock size={20} />}
        />
        <KpiCard
          label="En breach actif"
          value={metrics.current_breaches}
          sub={metrics.current_breaches > 0 ? 'intervention requise' : 'aucun dépassement'}
          color={breachColor}
          icon={<AlertTriangle size={20} />}
        />
        <KpiCard
          label="Alertes ignorées"
          value={metrics.ignored_count}
          sub="hors périmètre conformité"
          icon={<Ban size={20} />}
        />
      </div>

      <div className={styles.trends}>
        <div className={styles.trendCard}>
          <div className={styles.trendTitle}>Tendance 7 jours</div>
          <div className={styles.trendRow}>
            <span>Conformité</span>
            <strong style={{ color: pctColor(metrics.trend_7d.compliance_pct) }}>
              {metrics.trend_7d.compliance_pct}%
            </strong>
          </div>
          <div className={styles.trendRow}>
            <span>MTTR</span>
            <strong>{metrics.trend_7d.mttr_hours}h</strong>
          </div>
          <div className={styles.trendRow}>
            <span>Ignorées</span>
            <strong>{metrics.trend_7d.ignored_count}</strong>
          </div>
        </div>
        <div className={styles.trendCard}>
          <div className={styles.trendTitle}>Tendance 30 jours</div>
          <div className={styles.trendRow}>
            <span>Conformité</span>
            <strong style={{ color: pctColor(metrics.trend_30d.compliance_pct) }}>
              {metrics.trend_30d.compliance_pct}%
            </strong>
          </div>
          <div className={styles.trendRow}>
            <span>MTTR</span>
            <strong>{metrics.trend_30d.mttr_hours}h</strong>
          </div>
          <div className={styles.trendRow}>
            <span>Ignorées</span>
            <strong>{metrics.trend_30d.ignored_count}</strong>
          </div>
        </div>
      </div>
    </div>
  )
}
