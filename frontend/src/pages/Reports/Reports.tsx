import { useState } from 'react'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'
import styles from './Reports.module.css'

const today = () => new Date().toISOString().slice(0, 10)
const thisMonth = () => new Date().toISOString().slice(0, 7)

export default function Reports() {
  const { showToast } = useToast()
  const [rptDate, setRptDate] = useState(today())
  const [rptDiv, setRptDiv] = useState('')
  const [rptMonth, setRptMonth] = useState(thisMonth())
  const [rptMonthDiv, setRptMonthDiv] = useState('')
  const [cbDate, setCbDate] = useState(today())
  const [cbData, setCbData] = useState<any>(null)
  const [cbLoading, setCbLoading] = useState(false)
  const [downloading, setDownloading] = useState<string | null>(null)

  const download = async (type: string) => {
    setDownloading(type)
    try {
      let url = ''
      let params: any = {}

      if (type === 'daily') {
        url = '/api/report/daily'
        params = { date: rptDate, division: rptDiv || undefined }
      } else if (type === 'monthly') {
        url = '/api/report/monthly'
        params = { month: rptMonth, division: rptMonthDiv || undefined }
      } else if (type === 'by-division') {
        url = '/api/report/by-division'
        params = { date: rptDate }
      } else if (type === 'cb-csv') {
        url = '/api/report/customerbalance/csv'
        params = { date: cbDate }
      } else if (type === 'cb-excel') {
        url = '/api/report/customerbalance/excel'
        params = { date: cbDate }
      }

      const res = await api.get(url, { params, responseType: 'blob' })
      const blob = new Blob([res.data])
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)

      // Get filename from Content-Disposition or default
      const cd = res.headers['content-disposition'] ?? ''
      const match = cd.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/)
      link.download = match ? match[1].replace(/['"]/g, '') : `rapport_${type}_${today()}.xlsx`
      link.click()
      URL.revokeObjectURL(link.href)
      showToast('Téléchargement démarré !', 'success')
    } catch (err: any) {
      showToast(err?.response?.data?.error ?? 'Erreur lors du téléchargement', 'error')
    } finally {
      setDownloading(null)
    }
  }

  const loadCBReport = async () => {
    setCbLoading(true)
    setCbData(null)
    try {
      const res = await api.get('/api/report/customerbalance', { params: { date: cbDate } })
      setCbData(res.data)
    } catch (err: any) {
      showToast(err?.response?.data?.error ?? 'Erreur rapport CustomerBalance', 'error')
    } finally {
      setCbLoading(false)
    }
  }

  const DivSelect = ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <select value={value} onChange={e => onChange(e.target.value)} className={styles.inlineSelect}>
      <option value="">Toutes divisions</option>
      <option value="KWT">🇰🇼 KWT</option>
      <option value="DOHA">🇶🇦 DOHA</option>
      <option value="KSA">🇸🇦 KSA</option>
      <option value="SPG">🇸🇬 SPG</option>
    </select>
  )

  return (
    <>
          {/* ── Rapport journalier ── */}
      <div className={styles.section}>
        <h3>📥 Rapports journaliers par client</h3>

        <div className={styles.reportGrid}>
          {/* Daily */}
          <div className={styles.reportCard}>
            <div className={styles.reportTitle}>📅 Rapport journalier</div>
            <div className={styles.reportMeta}>1 onglet par jour — format client</div>
            <div className={styles.inlineRow}>
              <input type="date" value={rptDate} onChange={e => setRptDate(e.target.value)} className={styles.inlineInput} />
              <DivSelect value={rptDiv} onChange={setRptDiv} />
            </div>
            <div className={styles.cardActions}>
              <button className="btn bp bsm" disabled={downloading === 'daily'} onClick={() => download('daily')}>
                {downloading === 'daily' ? <><span className="spin" /> Génération...</> : '⬇ Télécharger'}
              </button>
              <button className="btn borange bsm" title="Un fichier par division — ZIP" disabled={downloading === 'by-division'} onClick={() => download('by-division')}>
                {downloading === 'by-division' ? <span className="spin" /> : '📦 ZIP par division'}
              </button>
            </div>
          </div>

          {/* Monthly */}
          <div className={styles.reportCard}>
            <div className={styles.reportTitle}>📊 Rapport mensuel</div>
            <div className={styles.reportMeta}>Un onglet par jour du mois</div>
            <div className={styles.inlineRow}>
              <input type="month" value={rptMonth} onChange={e => setRptMonth(e.target.value)} className={styles.inlineInput} />
              <DivSelect value={rptMonthDiv} onChange={setRptMonthDiv} />
            </div>
            <div className={styles.cardActions}>
              <button className="btn bpurple bsm" disabled={downloading === 'monthly'} onClick={() => download('monthly')}>
                {downloading === 'monthly' ? <><span className="spin" /> Génération...</> : '⬇ Rapport mensuel'}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ── CustomerBalance ── */}
      <div className={styles.cbSection}>
        <div className={styles.cbHeader}>
          <div>
            <span style={{ fontSize: 14, fontWeight: 800 }}>📋 Rapport journalier — CustomerBalance</span>
            <div style={{ fontSize: 11, color: 'var(--mut)', marginTop: 2 }}>
              Suivi des lignes intégrées vs rejetées (préfixe <code>OPEC1R</code>)
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <input type="date" value={cbDate} onChange={e => setCbDate(e.target.value)} className={styles.inlineInput} />
            <button className="btn bp bsm" onClick={loadCBReport}>🔄 Actualiser</button>
            <button className="btn bg-btn bsm" disabled={downloading === 'cb-csv'} onClick={() => download('cb-csv')}>
              {downloading === 'cb-csv' ? <span className="spin" /> : '⬇ CSV'}
            </button>
            <button className="btn bgreen bsm" disabled={downloading === 'cb-excel'} onClick={() => download('cb-excel')}>
              {downloading === 'cb-excel' ? <span className="spin" /> : '⬇ Excel'}
            </button>
          </div>
        </div>

        {/* KPIs */}
        {cbData && (
          <div className={styles.cbKpis}>
            {[
              { v: cbData.total_integrated ?? 0, l: 'Lignes intégrées (CBLC1I)', c: 'var(--green)' },
              { v: cbData.total_rejected  ?? 0, l: 'Lignes rejetées (OPEC1R)',  c: 'var(--red)' },
              { v: cbData.total_lines     ?? 0, l: 'Total lignes Cegid',         c: 'var(--txt)' },
              { v: cbData.total_anomalies ?? 0, l: 'Anomalies détectées',        c: 'var(--red)' },
            ].map(k => (
              <div key={k.l} className={styles.cbKpi}>
                <div style={{ fontSize: 20, fontWeight: 800, color: k.c }}>{k.v}</div>
                <div style={{ fontSize: 10, color: 'var(--mut)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.4px' }}>{k.l}</div>
              </div>
            ))}
          </div>
        )}

        {/* Legend */}
        <div className={styles.cbLegend}>
          <strong>Légende des préfixes Cegid :</strong><br />
          <span className={styles.pillInt}>CBLC1I</span> = intégré dans Cegid et transmis à Oracle &nbsp;·&nbsp;
          <span className={styles.pillRej}>OPEC1R</span> = <em>rejeté</em>, non intégré &nbsp;·&nbsp;
          <span className={styles.pillOther}>OPEC1I / CBLC1R</span> = autres statuts
        </div>

        {/* Table */}
        {cbLoading ? (
          <div style={{ textAlign: 'center', padding: 30, color: 'var(--mut)' }}>Chargement...</div>
        ) : (
          <table className={styles.cbTable}>
            <thead>
              <tr>
                <th>Division / OU</th>
                <th>Nb intégrées <span className={styles.pillInt}>CBLC1I</span></th>
                <th>Nb rejetées <span className={styles.pillRej}>OPEC1R</span></th>
                <th>Taux rejet</th>
                <th>Statut</th>
              </tr>
            </thead>
            <tbody>
              {(cbData?.rows ?? []).length === 0 ? (
                <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--mut)', padding: 16 }}>
                  Cliquez sur « Actualiser » pour charger le rapport
                </td></tr>
              ) : (cbData?.rows ?? []).map((r: any, i: number) => {
                const rejRate = r.nb_rejected && r.nb_integrated
                  ? Math.round((r.nb_rejected / (r.nb_integrated + r.nb_rejected)) * 100)
                  : 0
                return (
                  <tr key={i}>
                    <td style={{ fontWeight: 700 }}>{r.division ?? r.ou ?? '—'}</td>
                    <td><span className={styles.pillInt}>{r.nb_integrated ?? 0}</span></td>
                    <td><span className={styles.pillRej}>{r.nb_rejected ?? 0}</span></td>
                    <td style={{ fontWeight: 700, color: rejRate > 10 ? 'var(--red)' : 'var(--green)' }}>{rejRate}%</td>
                    <td>
                      <span className={`bdg ${rejRate > 10 ? 'b-r' : rejRate > 0 ? 'b-o' : 'b-g'}`}>
                        {rejRate > 10 ? 'KO' : rejRate > 0 ? 'TBC' : 'OK'}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
